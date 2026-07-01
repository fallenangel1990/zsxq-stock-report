"""组合构建模块。

实现个股间相关性控制、风险预算仓位分配、最优组合推荐。
"""

import math
import requests
from collections import defaultdict
from typing import Optional

from price_fetcher import _code_to_tencent


def _fetch_recent_returns(code: str, days: int = 20, timeout: int = 8) -> list[float]:
    """获取股票最近 N 日收益率序列。"""
    tc = _code_to_tencent(code)
    if not tc:
        return []
    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc},day,,,{days + 5},qfq"
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return []
        stock_data = (data.get("data") or {}).get(tc, {})
        klines = (stock_data.get("qfqday") or []) or (stock_data.get("day") or [])
        if len(klines) < 3:
            return []
        closes = [float(k[2]) for k in klines if len(k) >= 3]
        returns = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes))]
        return returns[-days:]
    except Exception:
        return []


def _correlation(xs: list[float], ys: list[float]) -> float:
    """计算两个等长序列的 Pearson 相关系数。"""
    n = min(len(xs), len(ys))
    if n < 5:
        return 0.0
    xs, ys = xs[:n], ys[:n]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / n)
    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / n)
    if sx == 0 or sy == 0:
        return 0.0
    return round(cov / (sx * sy), 3)


def filter_by_correlation(
    stocks: list[dict],
    max_corr: float = 0.7,
    verbose: bool = False,
) -> list[dict]:
    """相关性控制：组合内任意两只股票相关系数 > max_corr 时，只保留得分更高者。

    Args:
        stocks: 通过趋势精选的股票列表（已按得分降序）。
        max_corr: 最大容忍相关系数。
        verbose: 是否输出日志。

    Returns:
        通过相关性检查的股票列表。
    """
    if len(stocks) <= 1:
        return stocks

    # 获取各股收益率序列
    returns_cache = {}
    for s in stocks:
        code = s.get("code")
        if code:
            returns_cache[code] = _fetch_recent_returns(code, 20)

    # 贪心选择：按得分降序，逐只检查与已选股票的相关性
    selected = []
    selected_returns = []
    removed = []

    for stock in stocks:
        code = stock.get("code")
        if not code or code not in returns_cache:
            selected.append(stock)
            continue

        ret = returns_cache[code]
        if not ret:
            selected.append(stock)
            continue

        # 检查与已选股票的最大相关系数
        max_found = 0.0
        for sel_ret in selected_returns:
            corr = abs(_correlation(ret, sel_ret))
            max_found = max(max_found, corr)

        if max_found > max_corr:
            stock["_filter_reason"] = f"与已选股票相关性过高({max_found:.2f}>{max_corr})"
            removed.append(stock)
            if verbose:
                print(f"  [组合] {stock.get('name', '')} 与已选股票相关性 {max_found:.2f}，跳过", flush=True)
        else:
            selected.append(stock)
            selected_returns.append(ret)

    return selected


def calculate_volatility(returns: list[float]) -> float:
    """计算收益率序列的年化波动率（日波动率 * sqrt(242)）。"""
    if len(returns) < 5:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    daily_vol = math.sqrt(var)
    return round(daily_vol * math.sqrt(242), 2)


def allocate_risk_budget(
    stocks: list[dict],
    total_budget_pct: float = 100.0,
    method: str = "inverse_vol",
) -> list[dict]:
    """风险预算仓位分配。

    Args:
        stocks: 股票列表（需含 technical.atr_14 或可计算波动率）。
        total_budget_pct: 总仓位百分比上限。
        method: 分配方法 - "inverse_vol"（波动率反比）或 "equal"（等权）。

    Returns:
        股票列表，每只增加 position_pct 字段。
    """
    if not stocks:
        return stocks

    if method == "equal":
        pct_each = round(total_budget_pct / len(stocks), 1)
        for s in stocks:
            s["position_pct"] = pct_each
        return stocks

    # 波动率反比分配
    vols = []
    for s in stocks:
        tech = s.get("technical") or {}
        atr = tech.get("atr_14")
        price = s.get("current_price")
        # ATR 占价格的百分比作为波动率代理
        if atr and price and price > 0:
            vol = (atr / price) * 100
        else:
            vol = 5.0  # 默认中等波动率
        vols.append(max(vol, 0.5))  # 避免除零

    inv_vols = [1.0 / v for v in vols]
    total_inv = sum(inv_vols)
    if total_inv == 0:
        pct_each = round(total_budget_pct / len(stocks), 1)
        for s in stocks:
            s["position_pct"] = pct_each
        return stocks

    for i, s in enumerate(stocks):
        raw_pct = (inv_vols[i] / total_inv) * total_budget_pct
        s["position_pct"] = round(min(raw_pct, 40.0), 1)  # 单只上限 40%

    # 归一化确保总和不超过 total_budget_pct
    total_allocated = sum(s["position_pct"] for s in stocks)
    if total_allocated > total_budget_pct:
        scale = total_budget_pct / total_allocated
        for s in stocks:
            s["position_pct"] = round(s["position_pct"] * scale, 1)

    return stocks


def apply_sector_cap(
    stocks: list[dict],
    max_per_sector: int = 3,
    max_sector_pct: float = 0.25,
    total_slots: int = 8,
    verbose: bool = False,
) -> list[dict]:
    """行业敞口控制：限制单一行业在组合中的占比。

    Args:
        stocks: 候选股票列表（已按得分降序）。
        max_per_sector: 单行业最多保留只数。
        max_sector_pct: 单行业最大仓位占比。
        total_slots: 组合总槽位数。
        verbose: 是否输出日志。

    Returns:
        通过行业上限检查的股票列表。
    """
    if not stocks:
        return []

    selected = []
    sector_count = defaultdict(int)
    removed = []

    for stock in stocks:
        if len(selected) >= total_slots:
            break

        sector = stock.get("sector") or stock.get("trending_sector") or "未分类"
        if sector_count[sector] >= max_per_sector:
            stock["_filter_reason"] = f"行业{sector}已达上限({max_per_sector}只)"
            removed.append(stock)
            if verbose:
                print(f"  [行业上限] {stock.get('name', '')}({sector}) 已达上限，跳过", flush=True)
            continue

        selected.append(stock)
        sector_count[sector] += 1

    return selected


def build_optimal_portfolio(
    stocks: list[dict],
    profile: str = "balanced",
) -> list[dict]:
    """从通过筛选的股票中构建最优组合。

    Args:
        stocks: 通过所有筛选的股票列表。
        profile: 组合风格 - "aggressive"（高beta）、"balanced"（均衡）、"defensive"（低波动）。

    Returns:
        组合股票列表（按 profile 排序后的子集）。
    """
    if not stocks:
        return []

    if profile == "aggressive":
        # 激进型：优先高趋势分、高 beta
        scored = [(s, s.get("trend_score", 0) * 0.4 + s.get("score", 0) * 0.3 + s.get("buy_score", 0) * 0.3) for s in stocks]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:min(5, len(scored))]]

    if profile == "defensive":
        # 防守型：优先低波动、高基本面
        def defensive_score(s):
            tech = s.get("technical") or {}
            atr = tech.get("atr_14", 99)
            price = s.get("current_price") or 1
            vol = (atr / price * 100) if atr and price else 5
            fund = s.get("fundamentals_score", 5)
            return fund * 0.6 - vol * 0.4
        stocks_sorted = sorted(stocks, key=defensive_score, reverse=True)
        return stocks_sorted[:min(5, len(stocks_sorted))]

    # 均衡型：综合评分
    return stocks[:min(5, len(stocks))]


def format_portfolio_summary(
    stocks: list[dict],
    regime: dict = None,
) -> str:
    """格式化组合概览。"""
    if not stocks:
        return "暂无组合"

    lines = []
    total_pct = sum(s.get("position_pct", 0) for s in stocks)
    lines.append(f"组合共 {len(stocks)} 只，总仓位 {total_pct:.0f}%")

    sector_dist = defaultdict(int)
    for s in stocks:
        sector = s.get("sector") or "未分类"
        sector_dist[sector] += 1
    if sector_dist:
        dist_str = "、".join(f"{k}({v})" for k, v in sorted(sector_dist.items(), key=lambda x: -x[1]))
        lines.append(f"行业分布: {dist_str}")

    if regime:
        lines.append(f"市场状态: {regime.get('label', '未知')}，仓位上限 {regime.get('position_ceiling', '未知')}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Kelly 公式仓位分配
# ═══════════════════════════════════════════════════════════════

def kelly_criterion(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """计算 Kelly 比例。

    Kelly% = (p * b - q) / b
    其中 p = 胜率, q = 1-p, b = 平均盈利/平均亏损

    Returns:
        Kelly 比例（0-1），已做半 Kelly 安全处理。
    """
    if avg_loss == 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0

    b = abs(avg_win / avg_loss)  # 赔率
    p = win_rate
    q = 1 - p

    kelly = (p * b - q) / b

    # 半 Kelly（更保守）
    half_kelly = kelly / 2

    # 限制在合理范围
    return max(0.0, min(0.4, half_kelly))


def allocate_kelly(
    stocks: list[dict],
    total_budget_pct: float = 100.0,
    default_win_rate: float = 0.55,
    default_avg_win: float = 8.0,
    default_avg_loss: float = 5.0,
) -> list[dict]:
    """基于 Kelly 公式的仓位分配。

    使用每只股票的推荐评分和历史胜率估算 Kelly 比例，
    然后按 Kelly 比例分配仓位。

    Args:
        stocks: 股票列表（含 score, technical_score 等）。
        total_budget_pct: 总仓位百分比上限。
        default_win_rate: 默认胜率（无历史数据时）。
        default_avg_win: 默认平均盈利%。
        default_avg_loss: 默认平均亏损%。

    Returns:
        股票列表，每只增加 position_pct 字段。
    """
    if not stocks:
        return stocks

    kelly_values = []
    for s in stocks:
        # 用评分估算胜率和赔率
        score = s.get("score", 5.0)
        technical_score = s.get("technical_score", 5.0)

        # 评分越高胜率越高
        est_win_rate = default_win_rate + (score - 5.0) * 0.03
        est_win_rate = max(0.35, min(0.75, est_win_rate))

        # 技术面好赔率更高
        target_str = s.get("target_str", "")
        if target_str:
            est_avg_win = default_avg_win * (1 + (score - 5.0) * 0.05)
        else:
            est_avg_win = default_avg_win

        kelly = kelly_criterion(est_win_rate, est_avg_win, default_avg_loss)
        kelly_values.append(max(0.01, kelly))

    # 归一化
    total_kelly = sum(kelly_values)
    if total_kelly == 0:
        pct_each = round(total_budget_pct / len(stocks), 1)
        for s in stocks:
            s["position_pct"] = pct_each
        return stocks

    for i, s in enumerate(stocks):
        raw_pct = (kelly_values[i] / total_kelly) * total_budget_pct
        s["position_pct"] = round(min(raw_pct, 35.0), 1)  # 单只上限 35%

    # 归一化确保总和不超过 total_budget_pct
    total_allocated = sum(s["position_pct"] for s in stocks)
    if total_allocated > total_budget_pct:
        scale = total_budget_pct / total_allocated
        for s in stocks:
            s["position_pct"] = round(s["position_pct"] * scale, 1)

    return stocks


# ═══════════════════════════════════════════════════════════════
# 风险平价（Risk Parity）
# ═══════════════════════════════════════════════════════════════

def allocate_risk_parity(
    stocks: list[dict],
    total_budget_pct: float = 100.0,
) -> list[dict]:
    """风险平价仓位分配：让每只股票对组合风险贡献相等。

    风险贡献 = 权重 × 波动率
    目标：所有股票的 风险贡献 相等

    Args:
        stocks: 股票列表（需含 technical.atr_14）。
        total_budget_pct: 总仓位百分比上限。

    Returns:
        股票列表，每只增加 position_pct 字段。
    """
    if not stocks:
        return stocks

    # 计算每只股票的波动率
    vols = []
    for s in stocks:
        tech = s.get("technical") or {}
        atr = tech.get("atr_14")
        price = s.get("current_price")
        if atr and price and price > 0:
            vol = atr / price
        else:
            vol = 0.025  # 默认 2.5% 日波动
        vols.append(max(vol, 0.005))

    # 风险平价：权重 = 1/波动率
    inv_vols = [1.0 / v for v in vols]
    total_inv = sum(inv_vols)

    for i, s in enumerate(stocks):
        raw_pct = (inv_vols[i] / total_inv) * total_budget_pct
        s["position_pct"] = round(min(raw_pct, 40.0), 1)

    # 归一化
    total_allocated = sum(s["position_pct"] for s in stocks)
    if total_allocated > total_budget_pct:
        scale = total_budget_pct / total_allocated
        for s in stocks:
            s["position_pct"] = round(s["position_pct"] * scale, 1)

    return stocks


def select_allocation_method(
    stocks: list[dict],
    method: str = "auto",
) -> list[dict]:
    """自动选择最优仓位分配方法。

    方法选择逻辑：
    - 有推荐历史且样本充足 → Kelly 公式
    - 有 ATR 数据 → 风险平价
    - 默认 → 波动率反比

    Args:
        stocks: 股票列表。
        method: "auto" | "kelly" | "risk_parity" | "inverse_vol" | "equal"

    Returns:
        股票列表，每只增加 position_pct 字段。
    """
    if method == "kelly":
        return allocate_kelly(stocks)
    if method == "risk_parity":
        return allocate_risk_parity(stocks)
    if method == "equal":
        return allocate_risk_budget(stocks, method="equal")
    if method == "inverse_vol":
        return allocate_risk_budget(stocks, method="inverse_vol")

    # auto: 根据数据可用性选择
    has_atr = any(
        (s.get("technical") or {}).get("atr_14") for s in stocks
    )
    has_history = False
    try:
        from backtester import _load_history
        history = _load_history()
        has_history = len(history) > 20
    except Exception:
        pass

    if has_history:
        return allocate_kelly(stocks)
    elif has_atr:
        return allocate_risk_parity(stocks)
    else:
        return allocate_risk_budget(stocks, method="inverse_vol")
