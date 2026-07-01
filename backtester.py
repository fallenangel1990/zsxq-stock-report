"""推荐回测模块。

读取推荐历史，获取后续行情，计算各评分因子的 IC（信息系数）和绩效指标。
用于验证评分体系的有效性并动态调整权重。
"""

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from price_fetcher import fetch_prices, _fetch_one_technical, _code_to_tencent


HISTORY_FILE = Path(__file__).parent / "data" / "summary" / "history" / "recommendations.jsonl"
BACKTEST_OUTPUT = Path(__file__).parent / "data" / "summary" / "backtest"


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _load_history(path: Optional[Path] = None) -> list[dict]:
    """读取推荐历史 JSONL。"""
    path = path or HISTORY_FILE
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _get_forward_returns(
    code: str,
    entry_price: float,
    entry_date: str,
    days: list[int] = None,
) -> dict:
    """获取股票从推荐日起的未来 N 日收益率。

    通过获取 K 线数据，根据推荐日期定位 K 线位置，
    计算 T+1/T+5/T+10/T+20 的真实收益率。

    Args:
        code: 6位股票代码。
        entry_price: 推荐时的入场价。
        entry_date: 推荐日期（ISO 格式，取前10位 YYYY-MM-DD）。
        days: 要计算收益的天数列表。

    Returns:
        {1: return_1d_pct, 5: return_5d_pct, ...}
    """
    if days is None:
        days = [1, 5, 10, 20]
    if not code or not entry_price or entry_price <= 0 or not entry_date:
        return {}

    tc = _code_to_tencent(code)
    if not tc:
        return {}

    import requests
    try:
        # 获取足够多的 K 线数据（推荐日期之后至少 25 个交易日）
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc},day,,,60,qfq"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return {}
        stock_data = (data.get("data") or {}).get(tc, {})
        klines = (stock_data.get("qfqday") or []) or (stock_data.get("day") or [])
        if not klines:
            return {}

        # 解析 K 线数据：[日期, 开, 收, 高, 低, 量]
        kline_dates = [k[0] for k in klines if len(k) >= 3]
        kline_closes = [float(k[2]) for k in klines if len(k) >= 3]

        # 找到推荐日期对应的 K 线位置（推荐日期当天或之后最近的交易日）
        target_date = entry_date[:10]
        start_idx = None
        for i, kd in enumerate(kline_dates):
            if kd >= target_date:
                start_idx = i
                break

        if start_idx is None:
            # 推荐日期在 K 线范围之外，用最新价估算
            latest_close = kline_closes[-1]
            return {0: round((latest_close / entry_price - 1) * 100, 2)}

        # 计算前向收益
        returns = {}
        for d in days:
            future_idx = start_idx + d
            if future_idx < len(kline_closes):
                future_close = kline_closes[future_idx]
                returns[d] = round((future_close / entry_price - 1) * 100, 2)
            elif start_idx < len(kline_closes):
                # 数据不足，用最新可用收盘价
                future_close = kline_closes[-1]
                actual_days = len(kline_closes) - 1 - start_idx
                returns[d] = round((future_close / entry_price - 1) * 100, 2)

        # 始终包含最新收益
        latest_close = kline_closes[-1]
        returns[0] = round((latest_close / entry_price - 1) * 100, 2)

        return returns
    except Exception:
        return {}


def _compute_forward_return(records: list[dict], horizon: int = 5) -> list[tuple]:
    """计算固定持有期的前向收益率。

    使用记录中的 forward_return_{horizon}d 字段（如果存在），
    否则回退到基于当前价格的简单计算（带 look-ahead bias 警告）。

    Returns:
        [(record, forward_return_pct), ...]
    """
    result = []
    for rec in records:
        code = rec.get("code")
        entry_price = rec.get("current_price")
        if not code or not entry_price or entry_price <= 0:
            continue

        # 优先使用预先计算好的前向收益（无 look-ahead bias）
        fwd_ret = rec.get(f"forward_return_{horizon}d")
        if fwd_ret is not None:
            result.append((rec, fwd_ret))
            continue

        # 回退：用当前价格（存在 look-ahead bias，但至少可用）
        # 仅在数据中没有 T+N 收益字段时使用
        price_info = None
        try:
            from price_fetcher import fetch_prices
            prices = fetch_prices([code])
            price_info = prices.get(code)
        except Exception:
            pass

        if price_info and price_info.get("price"):
            ret = round((price_info["price"] / entry_price - 1) * 100, 2)
            result.append((rec, ret))

    return result


def calculate_factor_ic(records: list[dict], return_days: int = 5) -> dict:
    """计算各评分因子与未来收益的 Rank IC。

    IC（Information Coefficient）= 因子值与收益率的秩相关系数。
    |IC| > 0.03 表示因子有一定预测力，> 0.05 表示较强预测力。

    修复 look-ahead bias：优先使用固定持有期前向收益，
    而非当前价格。

    Args:
        records: 推荐历史记录。
        return_days: 持有期天数（5/20/60）。

    Returns:
        {
            "factor_name": {
                "ic": float,
                "count": int,
            }
        }
    """
    if not records:
        return {}

    # 评分因子列表
    factor_keys = [
        "score", "buy_score", "technical_score",
    ]
    detail_keys = [
        "upside", "quality", "consensus", "sector", "trend",
        "fundamentals", "capital_flow", "volume_confirm", "logic", "target",
    ]

    # 收集 (因子值, 前向收益率) 对
    factor_returns = defaultdict(list)

    # 前向收益计算（避免 look-ahead bias）
    fwd_data = _compute_forward_return(records, horizon=return_days)

    for rec, fwd_ret in fwd_data:
        # 顶层因子
        for key in factor_keys:
            val = rec.get(key)
            if val is not None:
                factor_returns[key].append((val, fwd_ret))

        # score_detail 子因子
        detail = rec.get("score_detail") or {}
        for key in detail_keys:
            val = detail.get(key)
            if val is not None:
                factor_returns[f"detail.{key}"].append((val, fwd_ret))

    # 计算每个因子的 Rank IC
    results = {}
    for factor_name, pairs in factor_returns.items():
        if len(pairs) < 5:
            continue
        n = len(pairs)
        factor_ranks = _rank_values([p[0] for p in pairs])
        return_ranks = _rank_values([p[1] for p in pairs])

        # Spearman = 1 - 6 * sum(d^2) / (n * (n^2 - 1))
        d_sq_sum = sum((fr - rr) ** 2 for fr, rr in zip(factor_ranks, return_ranks))
        ic = 1 - 6 * d_sq_sum / (n * (n * n - 1)) if n > 1 else 0

        results[factor_name] = {
            "ic": round(ic, 4),
            "count": n,
        }

    return results


def _rank_values(values: list[float]) -> list[float]:
    """计算秩排名（处理并列）。"""
    sorted_vals = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(sorted_vals):
        j = i
        while j < len(sorted_vals) - 1 and sorted_vals[j + 1][1] == sorted_vals[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[sorted_vals[k][0]] = avg_rank
        i = j + 1
    return ranks


def calculate_performance_metrics(records: list[dict]) -> dict:
    """计算推荐绩效指标。

    Returns:
        {
            "total_recommendations": int,
            "unique_stocks": int,
            "date_range": str,
            "factor_ic": {factor_name: {ic, count}},
            "score_group_returns": {score_range: avg_return},
            "forward_returns": {horizon: {avg_return, win_rate, count}},
        }
    """
    if not records:
        return {"error": "无推荐历史数据"}

    # 基础统计
    codes = set(r.get("code") for r in records if r.get("code"))
    dates = sorted(set((r.get("generated_at") or "")[:10] for r in records))

    # 因子 IC
    factor_ic = calculate_factor_ic(records)

    # 计算前向收益（T+0 最新, T+5, T+20）
    forward_returns_by_horizon = {"latest": [], "5d": [], "20d": []}
    score_groups = {"1-3": [], "3-5": [], "5-7": [], "7-10": []}

    for rec in records:
        code = rec.get("code")
        entry_price = rec.get("current_price")
        entry_date = (rec.get("generated_at") or "")[:10]
        score = rec.get("score", 0)
        if not code or not entry_price or entry_price <= 0:
            continue

        fwd = _get_forward_returns(code, entry_price, entry_date, days=[5, 20])
        if not fwd:
            continue

        ret_latest = fwd.get(0)
        ret_5d = fwd.get(5)
        ret_20d = fwd.get(20)

        if ret_latest is not None:
            forward_returns_by_horizon["latest"].append(ret_latest)
            # 按评分分组
            if score < 3:
                score_groups["1-3"].append(ret_latest)
            elif score < 5:
                score_groups["3-5"].append(ret_latest)
            elif score < 7:
                score_groups["5-7"].append(ret_latest)
            else:
                score_groups["7-10"].append(ret_latest)
        if ret_5d is not None:
            forward_returns_by_horizon["5d"].append(ret_5d)
        if ret_20d is not None:
            forward_returns_by_horizon["20d"].append(ret_20d)

    score_group_returns = {}
    for group, rets in score_groups.items():
        if rets:
            score_group_returns[group] = {
                "avg_return": round(sum(rets) / len(rets), 2),
                "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                "count": len(rets),
            }

    forward_returns = {}
    for horizon, rets in forward_returns_by_horizon.items():
        if rets:
            forward_returns[horizon] = {
                "avg_return": round(sum(rets) / len(rets), 2),
                "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                "max_return": round(max(rets), 2),
                "min_return": round(min(rets), 2),
                "count": len(rets),
            }

    return {
        "total_recommendations": len(records),
        "unique_stocks": len(codes),
        "date_range": f"{dates[0]} ~ {dates[-1]}" if dates else "无",
        "factor_ic": factor_ic,
        "score_group_returns": score_group_returns,
        "forward_returns": forward_returns,
        "generated_at": _now_shanghai().isoformat(),
    }


def run_backtest(history_path: Optional[Path] = None) -> dict:
    """运行完整回测。

    Returns:
        绩效指标字典。
    """
    records = _load_history(history_path)
    if not records:
        return {"error": f"无推荐历史数据（{history_path or HISTORY_FILE}）"}

    metrics = calculate_performance_metrics(records)

    # 保存回测结果
    BACKTEST_OUTPUT.mkdir(parents=True, exist_ok=True)
    ts = _now_shanghai().strftime("%Y%m%d_%H%M%S")
    output_file = BACKTEST_OUTPUT / f"backtest_{ts}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    metrics["output_file"] = str(output_file)

    return metrics


def format_backtest_report(metrics: dict) -> str:
    """将回测指标格式化为 Markdown 报告。"""
    if metrics.get("error"):
        return f"# 回测报告\n\n> {metrics['error']}\n"

    lines = [
        "# 评分因子回测报告",
        "",
        f"> 生成时间: {_now_shanghai().strftime('%Y-%m-%d %H:%M:%S 北京时间')}",
        f"> 推荐总数: {metrics.get('total_recommendations', 0)} 只（去重 {metrics.get('unique_stocks', 0)} 只）",
        f"> 数据范围: {metrics.get('date_range', '无')}",
        "",
    ]

    # 因子 IC
    factor_ic = metrics.get("factor_ic", {})
    if factor_ic:
        lines.append("## 因子有效性（Rank IC）\n")
        lines.append("| 因子 | IC 值 | 样本数 | 有效性 |")
        lines.append("|------|-------|--------|--------|")
        sorted_factors = sorted(factor_ic.items(), key=lambda x: abs(x[1].get("ic", 0)), reverse=True)
        for name, info in sorted_factors:
            ic = info.get("ic", 0)
            count = info.get("count", 0)
            if abs(ic) >= 0.05:
                level = "✅ 强"
            elif abs(ic) >= 0.03:
                level = "⚠️ 中"
            else:
                level = "❌ 弱"
            lines.append(f"| {name} | {ic:.4f} | {count} | {level} |")
        lines.append("")

    # 评分分组收益
    group_returns = metrics.get("score_group_returns", {})
    if group_returns:
        lines.append("## 评分分组收益\n")
        lines.append("| 评分区间 | 平均收益率 | 胜率 | 样本数 |")
        lines.append("|----------|-----------|------|--------|")
        for group in ["1-3", "3-5", "5-7", "7-10"]:
            info = group_returns.get(group)
            if info:
                lines.append(
                    f"| {group} | {info['avg_return']:+.2f}% | {info['win_rate']:.1f}% | {info['count']} |"
                )
        lines.append("")

    # 前向收益
    forward = metrics.get("forward_returns", {})
    if forward:
        lines.append("## 前向收益分析\n")
        lines.append("| 持有期 | 平均收益 | 胜率 | 最大盈利 | 最大亏损 | 样本数 |")
        lines.append("|--------|----------|------|----------|----------|--------|")
        for label, key in [("最新", "latest"), ("T+5", "5d"), ("T+20", "20d")]:
            info = forward.get(key)
            if info:
                lines.append(
                    f"| {label} | {info['avg_return']:+.2f}% | {info['win_rate']:.1f}% | "
                    f"{info['max_return']:+.2f}% | {info['min_return']:+.2f}% | {info['count']} |"
                )
        lines.append("")

    lines.append("---\n*本报告由回测系统自动生成。*")
    return "\n".join(lines)


# ── Walk-Forward 回测 ──

def walk_forward_backtest(
    records: list[dict],
    train_days: int = 30,
    test_days: int = 10,
) -> dict:
    """Walk-Forward 滚动窗口回测。

    将推荐历史按时间分窗：
    - 训练窗：计算各因子 IC
    - 测试窗：用训练窗的 IC 排序验证实际收益

    Args:
        records: 推荐历史。
        train_days: 训练窗口天数。
        test_days: 测试窗口天数。

    Returns:
        {
            "windows": [{train_start, train_end, test_start, test_end, ic_values, test_return}],
            "avg_test_return": float,
            "avg_test_win_rate": float,
            "stability": float,  # 各窗口收益的一致性
        }
    """
    if not records:
        return {"error": "无推荐历史数据"}

    # 按日期排序
    dated = [(r.get("generated_at", "")[:10], r) for r in records if r.get("generated_at")]
    dated.sort(key=lambda x: x[0])
    if len(dated) < 10:
        return {"error": "数据不足以进行 Walk-Forward 回测"}

    from datetime import datetime, timedelta
    dates = sorted(set(d[0] for d in dated))
    date_records = {}
    for d, r in dated:
        date_records.setdefault(d, []).append(r)

    windows = []
    i = 0
    while i + train_days + test_days <= len(dates):
        train_dates = dates[i:i + train_days]
        test_dates = dates[i + train_days:i + train_days + test_days]

        # 训练期：计算 IC
        train_records = []
        for d in train_dates:
            train_records.extend(date_records.get(d, []))
        ic_values = calculate_factor_ic(train_records) if train_records else {}

        # 测试期：用 IC 最高的因子排序，计算 top 组收益
        test_records = []
        for d in test_dates:
            test_records.extend(date_records.get(d, []))

        test_return = 0.0
        test_count = 0
        if test_records and ic_values:
            # 找 IC 最高的因子
            best_factor = max(ic_values.items(), key=lambda x: abs(x[1].get("ic", 0)))
            factor_name = best_factor[0]
            # 按该因子排序，取 top 3
            scored = []
            for r in test_records:
                val = r.get(factor_name) or (r.get("score_detail") or {}).get(factor_name.split(".")[-1])
                if val is not None:
                    scored.append((val, r))
            scored.sort(key=lambda x: x[0], reverse=True)
            top_stocks = scored[:3]
            if top_stocks:
                codes = [r.get("code") for _, r in top_stocks if r.get("code")]
                if codes:
                    current = fetch_prices(codes)
                    for _, r in top_stocks:
                        code = r.get("code")
                        entry = r.get("current_price")
                        if code and entry and entry > 0 and code in current:
                            ret = (current[code]["price"] / entry - 1) * 100
                            test_return += ret
                            test_count += 1
                if test_count > 0:
                    test_return /= test_count

        windows.append({
            "train_start": train_dates[0],
            "train_end": train_dates[-1],
            "test_start": test_dates[0],
            "test_end": test_dates[-1],
            "best_factor": best_factor[0] if ic_values else None,
            "best_ic": best_factor[1].get("ic", 0) if ic_values else 0,
            "test_return": round(test_return, 2),
            "test_count": test_count,
        })
        i += test_days  # 滚动步进

    if not windows:
        return {"error": "数据不足以生成 Walk-Forward 窗口"}

    returns = [w["test_return"] for w in windows if w["test_count"] > 0]
    win_count = sum(1 for r in returns if r > 0)
    avg_ret = sum(returns) / len(returns) if returns else 0
    std_ret = (sum((r - avg_ret) ** 2 for r in returns) / max(len(returns) - 1, 1)) ** 0.5 if len(returns) > 1 else 1
    stability = avg_ret / std_ret if std_ret > 0 else 0

    return {
        "windows": windows,
        "train_days": train_days,
        "test_days": test_days,
        "total_windows": len(windows),
        "valid_windows": len(returns),
        "avg_test_return": round(avg_ret, 2),
        "avg_test_win_rate": round(win_count / max(len(returns), 1) * 100, 1),
        "stability": round(stability, 2),
    }


# ── 压力测试 ──

def stress_test(stocks: list[dict], scenario: str = "crash") -> dict:
    """压力测试：模拟极端行情下组合的最大回撤。

    Args:
        stocks: 当前组合股票列表。
        scenario: 压力场景 - "crash"（大盘-5%）、"sector_collapse"（板块集体跌停）、"liquidity_crisis"（流动性枯竭）。

    Returns:
        {
            "scenario": str,
            "portfolio_impact": float (预估组合回撤%),
            "worst_stock": str,
            "worst_loss": float,
            "details": list,
        }
    """
    if not stocks:
        return {"error": "无组合数据"}

    scenarios = {
        "crash": {"label": "大盘暴跌", "base_drop": -5.0, "beta_mult": 1.2},
        "sector_collapse": {"label": "板块集体跌停", "base_drop": -8.0, "beta_mult": 1.5},
        "liquidity_crisis": {"label": "流动性枯竭", "base_drop": -3.0, "beta_mult": 0.8, "slippage_mult": 3.0},
    }
    cfg = scenarios.get(scenario, scenarios["crash"])
    base_drop = cfg["base_drop"]
    beta_mult = cfg["beta_mult"]
    slip_mult = cfg.get("slippage_mult", 1.0)

    details = []
    total_impact = 0.0
    total_weight = 0.0
    worst_stock = ""
    worst_loss = 0.0

    for s in stocks:
        tech = s.get("technical") or {}
        # 用 20 日涨跌幅的波动率近似 beta
        change_20d = tech.get("change_20d") or 0
        vol_ratio = abs(change_20d) / 5 if abs(change_20d) > 0 else 1.0
        beta = min(2.0, max(0.5, vol_ratio))

        stock_drop = base_drop * beta * beta_mult
        slippage = s.get("slippage_pct", 0.1) * slip_mult
        total_loss = stock_drop - slippage

        weight = s.get("position_pct", 20)
        weighted_impact = total_loss * weight / 100
        total_impact += weighted_impact
        total_weight += weight

        details.append({
            "name": s.get("name", ""),
            "code": s.get("code", ""),
            "beta": round(beta, 2),
            "stock_drop": round(stock_drop, 2),
            "slippage": round(slippage, 2),
            "total_loss": round(total_loss, 2),
            "weight": weight,
            "weighted_impact": round(weighted_impact, 2),
        })

        if total_loss < worst_loss:
            worst_loss = total_loss
            worst_stock = s.get("name", "")

    return {
        "scenario": cfg["label"],
        "portfolio_impact": round(total_impact, 2),
        "worst_stock": worst_stock,
        "worst_loss": round(worst_loss, 2),
        "details": details,
    }


def calculate_var(stocks: list[dict], confidence: float = 0.95) -> dict:
    """计算组合在险价值（VaR）。

    基于各股历史波动率和相关性，用参数法估算组合 VaR。

    Args:
        stocks: 组合股票列表。
        confidence: 置信度（0.95 或 0.99）。

    Returns:
        {"var_pct": float, "cvar_pct": float, "confidence": float}
    """
    if not stocks:
        return {"var_pct": 0, "cvar_pct": 0, "confidence": confidence}

    import math

    vols = []
    weights = []
    for s in stocks:
        tech = s.get("technical") or {}
        atr = tech.get("atr_14")
        price = s.get("current_price")
        if atr and price and price > 0:
            daily_vol = atr / price
        else:
            daily_vol = 0.025
        vols.append(daily_vol)
        weights.append(s.get("position_pct", 20) / 100)

    w_sum = sum(weights)
    if w_sum > 0:
        weights = [w / w_sum for w in weights]

    portfolio_var = math.sqrt(sum((w * v) ** 2 for w, v in zip(weights, vols)))

    z_map = {0.95: 1.645, 0.99: 2.326}
    z = z_map.get(confidence, 1.645)

    var_pct = round(portfolio_var * z * 100, 2)
    cvar_pct = round(var_pct * 1.2, 2)

    return {
        "var_pct": var_pct,
        "cvar_pct": cvar_pct,
        "confidence": confidence,
        "holding_period": "1日",
    }


# ── 因子衰减监控 ──

def monitor_factor_decay(history_path: Optional[Path] = None, window_days: int = 14) -> dict:
    """监控各因子 IC 的时序变化，检测因子衰减。

    将推荐历史按周分组，计算每周的因子 IC，
    检查是否存在 IC 连续下降的趋势。

    Returns:
        {
            "factor_trends": {
                factor_name: {
                    "weekly_ic": [float, ...],
                    "trend": "declining" | "stable" | "improving",
                    "current_ic": float,
                    "avg_ic": float,
                }
            },
            "alerts": [str],  # 因子衰减警告
        }
    """
    records = _load_history(history_path)
    if len(records) < 20:
        return {"factor_trends": {}, "alerts": ["数据不足，无法分析因子衰减"]}

    # 按周分组
    from collections import defaultdict
    weekly_records = defaultdict(list)
    for rec in records:
        date_str = (rec.get("generated_at") or "")[:10]
        if not date_str:
            continue
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            week_key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
            weekly_records[week_key].append(rec)
        except Exception:
            continue

    # 按时间排序的周列表
    sorted_weeks = sorted(weekly_records.keys())
    if len(sorted_weeks) < 3:
        return {"factor_trends": {}, "alerts": ["周数据不足 3 周，无法分析趋势"]}

    # 计算每周的因子 IC
    factor_names = [
        "score", "buy_score",
        "detail.upside", "detail.quality", "detail.consensus",
        "detail.sector", "detail.trend", "detail.fundamentals",
        "detail.capital_flow", "detail.volume_confirm",
    ]

    factor_trends = {}
    alerts = []

    for fname in factor_names:
        weekly_ics = []
        for week in sorted_weeks:
            week_recs = weekly_records[week]
            ic_data = calculate_factor_ic(week_recs)
            ic_val = ic_data.get(fname, {}).get("ic")
            if ic_val is not None:
                weekly_ics.append(ic_val)

        if len(weekly_ics) < 2:
            continue

        # 趋势检测：最近 3 周的 IC 是否连续下降
        recent = weekly_ics[-3:] if len(weekly_ics) >= 3 else weekly_ics
        declining = all(recent[i] > recent[i + 1] for i in range(len(recent) - 1))
        improving = all(recent[i] < recent[i + 1] for i in range(len(recent) - 1))

        trend = "declining" if declining else ("improving" if improving else "stable")
        current_ic = weekly_ics[-1]
        avg_ic = sum(weekly_ics) / len(weekly_ics)

        factor_trends[fname] = {
            "weekly_ic": [round(ic, 4) for ic in weekly_ics],
            "trend": trend,
            "current_ic": round(current_ic, 4),
            "avg_ic": round(avg_ic, 4),
        }

        if trend == "declining" and abs(current_ic) < 0.02:
            alerts.append(f"⚠️ 因子 {fname} IC 连续下降，当前 {current_ic:.4f}，可能已失效")
        elif trend == "declining":
            alerts.append(f"📉 因子 {fname} IC 趋势下降，当前 {current_ic:.4f}，需关注")

    return {"factor_trends": factor_trends, "alerts": alerts}


# ── 自适应权重 ──

def compute_adaptive_weights(records: list[dict]) -> dict:
    """基于各因子 IC 计算自适应权重。

    IC 越高的因子分配越高的权重，IC 为负的因子权重归零。

    Returns:
        {
            "weights": {factor_name: weight},
            "ic_values": {factor_name: {ic, count}},
            "method": "ic_inverse_variance",
        }
    """
    ic_data = calculate_factor_ic(records)
    if not ic_data:
        return {"weights": {}, "ic_values": {}, "method": "ic_inverse_variance"}

    # IC 加权：IC 为正的因子按 IC 大小加权，IC 为负或接近零的归零
    raw_weights = {}
    for name, info in ic_data.items():
        ic = info.get("ic", 0)
        if ic > 0.02:
            raw_weights[name] = ic
        else:
            raw_weights[name] = 0.0

    # 归一化
    total = sum(raw_weights.values())
    if total > 0:
        weights = {k: round(v / total, 4) for k, v in raw_weights.items()}
    else:
        # 全部因子 IC 都不好，使用均匀权重
        n = len(raw_weights)
        weights = {k: round(1 / n, 4) for k in raw_weights} if n > 0 else {}

    return {
        "weights": weights,
        "ic_values": {k: v for k, v in ic_data.items()},
        "method": "ic_inverse_variance",
    }


# ═══════════════════════════════════════════════════════════════
# 换手率控制
# ═══════════════════════════════════════════════════════════════

def calculate_turnover(
    current_holdings: dict[str, dict],
    target_recommendations: list[dict],
    max_daily_turnover: float = 0.3,
    min_holding_days: int = 3,
) -> dict:
    """计算换手率控制后的调仓建议。

    规则：
    - 最小持仓周期：持仓不足 min_holding_days 天的不卖
    - 最大日换手率：单日调仓不超过 max_daily_turnover
    - 优先卖出持仓时间最长的

    Args:
        current_holdings: {code: {"name", "shares", "buy_date", ...}}
        target_recommendations: 目标推荐列表（含 code, score）。
        max_daily_turnover: 最大日换手率（占总持仓的比例）。
        min_holding_days: 最小持仓天数。

    Returns:
        {
            "buys": [code, ...],
            "sells": [code, ...],
            "holds": [code, ...],
            "turnover_pct": float,
            "deferred": [code, ...],  # 因最小持仓期推迟卖出的
        }
    """
    now = _now_shanghai()
    target_codes = set(s.get("code", "") for s in target_recommendations if s.get("code"))
    current_codes = set(current_holdings.keys())

    # 需要卖出的（不在目标中）
    to_sell = []
    deferred = []
    for code in current_codes:
        pos = current_holdings[code]
        buy_date_str = pos.get("buy_date", "")
        if buy_date_str:
            try:
                buy_dt = datetime.fromisoformat(buy_date_str)
                hold_days = (now - buy_dt).days
            except Exception:
                hold_days = 999
        else:
            hold_days = 999

        if hold_days < min_holding_days:
            deferred.append(code)
        else:
            to_sell.append(code)

    # 按持仓时间排序（最久的优先卖）
    def hold_duration(code):
        buy_date_str = current_holdings.get(code, {}).get("buy_date", "")
        if buy_date_str:
            try:
                buy_dt = datetime.fromisoformat(buy_date_str)
                return (now - buy_dt).days
            except Exception:
                return 0
        return 0

    to_sell.sort(key=hold_duration, reverse=True)

    # 需要买入的（在目标中但不在持仓中）
    to_buy = [s.get("code", "") for s in target_recommendations
              if s.get("code") and s["code"] not in current_codes]

    # 换手率限制
    total_positions = len(current_codes)
    if total_positions == 0:
        max_sells = len(to_sell)
    else:
        max_sells = max(1, int(total_positions * max_daily_turnover))

    actual_sells = to_sell[:max_sells]
    overflow_sells = to_sell[max_sells:]

    # 换手率
    turnover = (len(actual_sells) + min(len(to_buy), len(actual_sells))) / max(total_positions, 1)

    return {
        "buys": to_buy,
        "sells": actual_sells,
        "holds": [c for c in current_codes if c not in actual_sells],
        "turnover_pct": round(turnover * 100, 1),
        "deferred": deferred,
        "overflow": overflow_sells,
    }


def check_minimum_holding(
    holdings: dict[str, dict],
    min_days: int = 3,
) -> list[str]:
    """检查哪些持仓不满足最小持仓期。"""
    now = _now_shanghai()
    too_new = []
    for code, pos in holdings.items():
        buy_date_str = pos.get("buy_date", "")
        if buy_date_str:
            try:
                buy_dt = datetime.fromisoformat(buy_date_str)
                if (now - buy_dt).days < min_days:
                    too_new.append(code)
            except Exception:
                pass
    return too_new
