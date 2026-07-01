"""市场状态机与自适应配置。

根据大盘环境自动判断市场状态（牛市/熊市/震荡），
动态调整评分权重、过滤阈值和仓位上限。
新增波动率 regime 检测和信用利差信号。
"""

import math
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import requests


# ── 市场状态定义 ──

REGIMES = {
    "bull": {
        "label": "牛市进攻",
        "desc": "趋势向上，赚钱效应好，可积极做多",
        "position_ceiling": "70%-80%",
        "score_threshold": 3.0,
        "ma5_atr_tolerance": 2.0,
        "max_per_sector": 4,
        "weight_overrides": {
            "upside": 0.20, "quality": 0.14, "consensus": 0.10,
            "sector": 0.14, "trend": 0.12, "fundamentals": 0.06,
            "capital_flow": 0.08, "volume_confirm": 0.08, "logic": 0.08,
        },
    },
    "neutral": {
        "label": "震荡平衡",
        "desc": "方向不明，控制仓位，精选个股",
        "position_ceiling": "40%-50%",
        "score_threshold": 3.0,
        "ma5_atr_tolerance": 1.5,
        "max_per_sector": 3,
        "weight_overrides": {
            "upside": 0.22, "quality": 0.16, "consensus": 0.12,
            "sector": 0.12, "trend": 0.08, "fundamentals": 0.08,
            "capital_flow": 0.08, "volume_confirm": 0.07, "logic": 0.07,
        },
    },
    "bear": {
        "label": "熊市防守",
        "desc": "趋势向下，控制风险，只做超跌反弹",
        "position_ceiling": "10%-25%",
        "score_threshold": 3.0,
        "ma5_atr_tolerance": 1.0,
        "max_per_sector": 2,
        "weight_overrides": {
            "upside": 0.15, "quality": 0.18, "consensus": 0.12,
            "sector": 0.08, "trend": 0.05, "fundamentals": 0.15,
            "capital_flow": 0.10, "volume_confirm": 0.10, "logic": 0.07,
        },
    },
}


def detect_market_regime(
    market: dict = None,
    breadth: dict = None,
    external_market: dict = None,
) -> dict:
    """判断当前市场状态。

    综合以下信号：
    1. 大盘评分（market_review 的 evaluate_market_environment）
    2. 指数技术面（fetch_market_environment 的 level）
    3. 市场宽度（涨跌比、涨停数）
    4. 过热信号

    Returns:
        {
            "regime": "bull" | "neutral" | "bear",
            "label": str,
            "desc": str,
            "score": float (0-100),
            "signals": dict,
            "position_ceiling": str,
            "scoring_config": dict,
        }
    """
    market = market or {}
    breadth = breadth or {}
    ext = external_market or {}

    signals = {}
    score = 50.0  # 基础分

    # 信号 1: 大盘评分（来自 market_review 的 evaluate_market_environment）
    env_score = market.get("score")
    if env_score is not None:
        score += (env_score - 50) * 0.4
        signals["market_env"] = env_score

    # 信号 2: 指数技术面水平
    ext_level = ext.get("level", "")
    level_bonus = {"偏强": 12, "中性": 0, "偏弱": -12, "过热": -8}
    if ext_level in level_bonus:
        score += level_bonus[ext_level]
        signals["index_tech"] = ext_level

    # 信号 3: 市场宽度（涨跌比）
    up = breadth.get("up", 0) or 0
    down = breadth.get("down", 0) or 0
    if up and down:
        ratio = up / max(down, 1)
        if ratio > 2.0:
            score += 10
        elif ratio > 1.5:
            score += 5
        elif ratio < 0.5:
            score -= 10
        elif ratio < 0.7:
            score -= 5
        signals["breadth_ratio"] = round(ratio, 2)

    # 信号 4: 涨停/跌停
    limit_up = breadth.get("limit_up", 0) or 0
    limit_down = breadth.get("limit_down", 0) or 0
    if limit_up > 60:
        score += 8
    elif limit_up > 30:
        score += 3
    if limit_down > 30:
        score -= 8
    elif limit_down > 15:
        score -= 3
    signals["limit_up"] = limit_up
    signals["limit_down"] = limit_down

    # 信号 5: 赚钱效应
    money_effect = breadth.get("money_effect", "")
    if money_effect == "强":
        score += 5
    elif money_effect == "弱":
        score -= 5
    signals["money_effect"] = money_effect

    # 信号 6: 波动率 regime
    try:
        vol_regime = detect_volatility_regime()
        score += vol_regime.get("score_impact", 0)
        signals["volatility_regime"] = vol_regime.get("label", "")
        signals["volatility_atr_pct"] = vol_regime.get("atr_pct", 0)
    except Exception:
        pass

    # 信号 7: 信用利差
    # 已禁用：数据获取不稳定

    # 信号 8: 政策事件检测
    try:
        policy = detect_policy_event()
        if policy.get("detected"):
            score += policy.get("impact", 0)
            signals["policy_event"] = policy.get("label", "")
    except Exception:
        pass

    # 信号 9: 流动性状态
    try:
        liquidity = detect_liquidity_regime()
        signals["liquidity_regime"] = liquidity.get("label", "")
        liq_adj = liquidity.get("weight_adjustment", {})
        if liq_adj:
            signals["liquidity_weight_adj"] = liq_adj
    except Exception:
        pass

    # 截断到 0-100
    score = round(max(0, min(100, score)), 1)

    # 判断状态
    if score >= 65:
        regime = "bull"
    elif score <= 38:
        regime = "bear"
    else:
        regime = "neutral"

    config = REGIMES[regime]
    # 流动性状态权重微调
    weight_overrides = dict(config["weight_overrides"])
    liquidity = signals.get("liquidity_weight_adj", {})
    for k, v in liquidity.items():
        if k in weight_overrides:
            weight_overrides[k] = round(weight_overrides[k] + v, 4)

    return {
        "regime": regime,
        "label": config["label"],
        "desc": config["desc"],
        "score": score,
        "signals": signals,
        "position_ceiling": config["position_ceiling"],
        "score_threshold": config["score_threshold"],
        "ma5_atr_tolerance": config["ma5_atr_tolerance"],
        "max_per_sector": config["max_per_sector"],
        "scoring_config": weight_overrides,
    }


def get_scoring_weights(regime_config: dict) -> dict:
    """从市场状态配置中获取评分权重。"""
    return regime_config.get("scoring_config", REGIMES["neutral"]["weight_overrides"])


def get_filter_config(regime_config: dict) -> dict:
    """从市场状态配置中获取过滤参数。"""
    return {
        "score_threshold": regime_config.get("score_threshold", 5.0),
        "ma5_atr_tolerance": regime_config.get("ma5_atr_tolerance", 1.5),
        "max_per_sector": regime_config.get("max_per_sector", 3),
    }


def format_regime_summary(regime_config: dict) -> str:
    """格式化市场状态摘要。"""
    label = regime_config.get("label", "未知")
    score = regime_config.get("score", 0)
    desc = regime_config.get("desc", "")
    ceiling = regime_config.get("position_ceiling", "未知")
    threshold = regime_config.get("score_threshold", 5.0)
    signals = regime_config.get("signals", {})

    parts = [
        f"**{label}**（{score}分）— {desc}",
        f"仓位上限: {ceiling}，精选阈值: ≥{threshold}分",
    ]
    sig_parts = []
    if signals.get("index_tech"):
        sig_parts.append(f"指数技术面:{signals['index_tech']}")
    if signals.get("breadth_ratio"):
        sig_parts.append(f"涨跌比:{signals['breadth_ratio']}")
    if signals.get("limit_up"):
        sig_parts.append(f"涨停:{signals['limit_up']}")
    if signals.get("money_effect"):
        sig_parts.append(f"赚钱效应:{signals['money_effect']}")
    if signals.get("volatility_regime"):
        sig_parts.append(f"波动率:{signals['volatility_regime']}")
    if sig_parts:
        parts.append("信号: " + " / ".join(sig_parts))

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# 波动率 Regime 检测
# ═══════════════════════════════════════════════════════════════

# ── 政策事件关键词库 ──
_POLICY_EVENTS = {
    "降准": {"impact": 8, "bias": "bull", "desc": "央行降准，流动性宽松"},
    "降息": {"impact": 10, "bias": "bull", "desc": "央行降息，融资成本下降"},
    "加息": {"impact": -10, "bias": "bear", "desc": "央行加息，流动性收紧"},
    "提高准备金率": {"impact": -8, "bias": "bear", "desc": "央行收紧流动性"},
    "注册制": {"impact": 3, "bias": "neutral", "desc": "发行制度改革"},
    "减持新规": {"impact": 5, "bias": "bull", "desc": "限制减持，减少供给"},
    "暂停IPO": {"impact": 8, "bias": "bull", "desc": "减少股票供给"},
    "恢复IPO": {"impact": -3, "bias": "bear", "desc": "增加股票供给"},
    "印花税": {"impact": 5, "bias": "bull", "desc": "交易成本变化"},
    "国家队": {"impact": 6, "bias": "bull", "desc": "汇金/社保入场"},
    "窗口指导": {"impact": -2, "bias": "bear", "desc": "监管窗口指导"},
    "资管新规": {"impact": -3, "bias": "bear", "desc": "资管业务规范"},
    "再融资": {"impact": -2, "bias": "bear", "desc": "再融资松绑增加供给"},
    "稳增长": {"impact": 4, "bias": "bull", "desc": "稳增长政策加码"},
    "促消费": {"impact": 3, "bias": "bull", "desc": "消费刺激政策"},
    "基建": {"impact": 2, "bias": "bull", "desc": "基建投资加码"},
    "房地产松绑": {"impact": 4, "bias": "bull", "desc": "地产政策放松"},
    "房地产收紧": {"impact": -4, "bias": "bear", "desc": "地产政策收紧"},
    "双碳": {"impact": 2, "bias": "neutral", "desc": "碳中和政策"},
    "共同富裕": {"impact": -1, "bias": "neutral", "desc": "收入分配政策"},
}


def detect_policy_event(timeout: int = 5) -> dict:
    """检测近期重大政策事件对市场的可能影响。

    通过全市场成交额分位数判断流动性环境，
    结合近期政策关键词判断方向性影响。

    Returns:
        {
            "detected": bool,
            "impact": float (对 regime score 的调整),
            "events": list[str],
            "label": str,
        }
    """
    import requests as req

    result = {"detected": False, "impact": 0.0, "events": [], "label": "无重大政策事件"}

    # 通过全市场成交额判断流动性状态
    _liquidity_regime = detect_liquidity_regime(timeout=timeout)

    # 当前简化版：基于成交额状态给出流动性评分调整
    liquidity_signal = _liquidity_regime.get("signal", "normal")
    if liquidity_signal == "high":
        result["events"].append("增量资金入场")
        result["impact"] = 5.0
        result["detected"] = True
    elif liquidity_signal == "low":
        result["events"].append("缩量环境，注意风险")
        result["impact"] = -5.0
        result["detected"] = True
    elif liquidity_signal == "contraction":
        result["events"].append("存量博弈，结构分化")
        result["impact"] = -2.0
        result["detected"] = True

    if result["detected"]:
        result["label"] = "；".join(result["events"])

    return result


def detect_liquidity_regime(timeout: int = 5) -> dict:
    """检测流动性状态：增量/存量/缩量。

    基于全市场成交额分位数判断：
    - 近 20 日成交额 > 80% 分位数 → 增量环境
    - 近 20 日成交额 < 30% 分位数 → 缩量环境
    - 其他 → 存量环境

    Returns:
        {
            "regime": "incremental" | "stock" | "contraction",
            "signal": "high" | "normal" | "low",
            "avg_amount_yi": float,
            "label": str,
            "weight_adjustment": dict,
        }
    """
    import requests as req

    result = {
        "regime": "stock",
        "signal": "normal",
        "avg_amount_yi": 0,
        "label": "存量环境",
        "weight_adjustment": {},
    }

    try:
        # 获取上证指数成交额作为全市场代理
        url = "https://qt.gtimg.cn/q=sh000001"
        resp = req.get(url, timeout=timeout)
        resp.raise_for_status()
        parts = resp.text.split("~")
        if len(parts) > 37:
            amount_wan = float(parts[37])  # 当日成交额（万元）
            amount_yi = amount_wan / 10000
            result["avg_amount_yi"] = amount_yi

            # 简化：以 8000 亿为增量/存量分界，4000 亿为缩量线
            if amount_yi >= 8000:
                result["regime"] = "incremental"
                result["signal"] = "high"
                result["label"] = "增量环境"
                result["weight_adjustment"] = {"trend": 0.02, "capital_flow": 0.02}
            elif amount_yi <= 4000:
                result["regime"] = "contraction"
                result["signal"] = "low"
                result["label"] = "缩量环境"
                # 缩量环境：降低趋势权重，提高基本面权重
                result["weight_adjustment"] = {"trend": -0.03, "fundamentals": 0.03}
            else:
                result["regime"] = "stock"
                result["signal"] = "normal"
                result["label"] = "存量环境"
    except Exception:
        pass

    return result


def detect_volatility_regime(timeout: int = 10) -> dict:
    """检测波动率 regime。

    基于 50ETF 期权隐含波动率（iVIX 替代）或 ATR 波动率。

    Returns:
        {
            "regime": "low" | "normal" | "high" | "extreme",
            "label": str,
            "score_impact": float,  # 对市场状态分的影响
            "atr_pct": float,  # 主要指数平均 ATR%
        }
    """
    try:
        from price_fetcher import _fetch_one_technical, _code_to_tencent

        indices = [
            ("sh000001", "上证指数"),
            ("sz399001", "深证成指"),
            ("sh000300", "沪深300"),
        ]

        atr_pcts = []
        for tc, name in indices:
            tech = _fetch_one_technical(tc, tc, timeout)
            if tech and tech.get("atr_14") and tech.get("close"):
                atr_pct = tech["atr_14"] / tech["close"] * 100
                atr_pcts.append(atr_pct)

        if not atr_pcts:
            return {"regime": "normal", "label": "波动率正常", "score_impact": 0, "atr_pct": 0}

        avg_atr = sum(atr_pcts) / len(atr_pcts)

        # ATR% 分档（A 股经验值）
        if avg_atr < 1.0:
            regime = "low"
            label = "低波动（平稳）"
            impact = 5  # 低波动偏利好
        elif avg_atr < 2.0:
            regime = "normal"
            label = "波动率正常"
            impact = 0
        elif avg_atr < 3.5:
            regime = "high"
            label = "高波动（谨慎）"
            impact = -8
        else:
            regime = "extreme"
            label = "极端波动（防守）"
            impact = -15

        return {
            "regime": regime,
            "label": label,
            "score_impact": impact,
            "atr_pct": round(avg_atr, 2),
        }
    except Exception:
        return {"regime": "normal", "label": "波动率未知", "score_impact": 0, "atr_pct": 0}


def detect_credit_spread_signal(timeout: int = 10) -> dict:
    """检测信用利差信号。

    通过国债期货和信用债 ETF 的相对强弱间接判断信用利差变化。
    信用利差扩大 → 风险偏好下降 → 利空。

    Returns:
        {
            "signal": "tightening" | "stable" | "widening",
            "label": str,
            "score_impact": float,
        }
    """
    try:
        from price_fetcher import fetch_prices

        # 用国债ETF vs 信用债ETF的相对强弱间接判断
        # 国债ETF(511010) vs 信用债ETF(511220)
        codes = ["511010", "511220"]
        prices = fetch_prices(codes)

        if len(prices) < 2:
            return {"signal": "stable", "label": "信用利差数据不足", "score_impact": 0}

        # 用 5 日涨跌幅差异判断
        from price_fetcher import fetch_5day_changes
        changes = fetch_5day_changes(codes)

        gov_change = changes.get("511010", 0) or 0
        credit_change = changes.get("511220", 0) or 0
        spread_change = gov_change - credit_change  # 国债涨更多 = 信用利差扩大

        if spread_change > 0.5:
            signal = "widening"
            label = "信用利差扩大（避险）"
            impact = -5
        elif spread_change < -0.3:
            signal = "tightening"
            label = "信用利差收窄（风险偏好回升）"
            impact = 3
        else:
            signal = "stable"
            label = "信用利差稳定"
            impact = 0

        return {
            "signal": signal,
            "label": label,
            "score_impact": impact,
            "spread_change": round(spread_change, 2),
        }
    except Exception:
        return {"signal": "stable", "label": "信用利差数据不可用", "score_impact": 0}
