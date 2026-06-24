"""市场状态机与自适应配置。

根据大盘环境自动判断市场状态（牛市/熊市/震荡），
动态调整评分权重、过滤阈值和仓位上限。
"""

import math
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


# ── 市场状态定义 ──

REGIMES = {
    "bull": {
        "label": "牛市进攻",
        "desc": "趋势向上，赚钱效应好，可积极做多",
        "position_ceiling": "70%-80%",
        "score_threshold": 4.5,
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
        "score_threshold": 5.0,
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
        "score_threshold": 6.0,
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
        "scoring_config": config["weight_overrides"],
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
    if sig_parts:
        parts.append("信号: " + " / ".join(sig_parts))

    return "\n".join(parts)
