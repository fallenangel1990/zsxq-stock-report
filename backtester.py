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


def calculate_factor_ic(records: list[dict], return_days: int = 5) -> dict:
    """计算各评分因子与未来收益的 Rank IC。

    IC（Information Coefficient）= 因子值与收益率的秩相关系数。
    |IC| > 0.03 表示因子有一定预测力，> 0.05 表示较强预测力。

    Args:
        records: 推荐历史记录。
        return_days: 用于计算收益率的天数。

    Returns:
        {
            "factor_name": {
                "ic": float,       # 平均 IC
                "ic_ir": float,    # IC 信息比率（IC / std(IC)）
                "positive_rate": float,  # IC 为正的比例
                "count": int,      # 有效样本数
            }
        }
    """
    if not records:
        return {}

    # 按推荐日期分组
    date_groups = defaultdict(list)
    for rec in records:
        date_str = (rec.get("generated_at") or "")[:10]
        if date_str:
            date_groups[date_str].append(rec)

    # 评分因子列表
    factor_keys = [
        "score", "buy_score", "technical_score",
    ]
    # score_detail 中的子因子
    detail_keys = [
        "upside", "quality", "consensus", "sector", "trend",
        "fundamentals", "capital_flow", "volume_confirm", "logic", "target",
    ]

    # 收集每期的 (因子值, 收益率) 对
    factor_returns = defaultdict(list)  # factor_name -> [(factor_value, return_pct)]

    for date_str, recs in date_groups.items():
        # 获取该期所有推荐股票的当前价格
        codes = [r.get("code") for r in recs if r.get("code")]
        if not codes:
            continue
        current_prices = fetch_prices(codes)

        for rec in recs:
            code = rec.get("code")
            entry_price = rec.get("current_price")
            if not code or not entry_price or entry_price <= 0:
                continue

            price_info = current_prices.get(code)
            if not price_info or not price_info.get("price"):
                continue

            ret = round((price_info["price"] / entry_price - 1) * 100, 2)

            # 顶层因子
            for key in factor_keys:
                val = rec.get(key)
                if val is not None:
                    factor_returns[key].append((val, ret))

            # score_detail 子因子
            detail = rec.get("score_detail") or {}
            for key in detail_keys:
                val = detail.get(key)
                if val is not None:
                    factor_returns[f"detail.{key}"].append((val, ret))

    # 计算每个因子的 Rank IC
    results = {}
    for factor_name, pairs in factor_returns.items():
        if len(pairs) < 5:
            continue
        # 计算秩相关
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
