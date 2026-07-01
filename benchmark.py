"""基准对比与收益归因模块。

提供 CSI300/CSI500 基准对比、Brinson 风格归因、因子归因、
行业归因分析，用于评估推荐系统的真实 alpha。
"""

import json
import math
import requests
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


DATA_DIR = Path(__file__).parent / "data"
ATTRIBUTION_DIR = DATA_DIR / "attribution"
BENCHMARK_CACHE = {}


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


# ═══════════════════════════════════════════════════════════════
# 基准收益获取
# ═══════════════════════════════════════════════════════════════

def fetch_benchmark_returns(
    benchmark: str = "csi300",
    days: int = 60,
    timeout: int = 10,
) -> list[dict]:
    """获取基准指数日收益率序列。

    Args:
        benchmark: "csi300" | "csi500" | "csi1000" | "wind_a"
        days: 获取最近 N 个交易日数据。

    Returns:
        [{"date": "YYYY-MM-DD", "close": float, "return_pct": float}, ...]
    """
    code_map = {
        "csi300": "sh000300",
        "csi500": "sh000905",
        "csi1000": "sh000852",
        "wind_a": "sh000001",
    }
    tc = code_map.get(benchmark, "sh000300")

    cache_key = f"{tc}_{days}"
    if cache_key in BENCHMARK_CACHE:
        return BENCHMARK_CACHE[cache_key]

    try:
        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={tc},day,,,{days + 5},qfq"
        )
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return []

        stock_data = (data.get("data") or {}).get(tc, {})
        klines = (stock_data.get("qfqday") or []) or (stock_data.get("day") or [])
        if len(klines) < 2:
            return []

        result = []
        for i in range(1, len(klines)):
            close = float(klines[i][2])
            prev_close = float(klines[i - 1][2])
            ret = round((close / prev_close - 1) * 100, 4) if prev_close > 0 else 0
            result.append({
                "date": klines[i][0],
                "close": close,
                "return_pct": ret,
            })

        BENCHMARK_CACHE[cache_key] = result
        return result
    except Exception as e:
        print(f"[基准] {benchmark} 获取失败: {e}", flush=True)
        return []


def fetch_multiple_benchmarks(days: int = 60) -> dict[str, list[dict]]:
    """获取多个基准指数的收益率序列。"""
    benchmarks = {}
    for name in ("csi300", "csi500", "wind_a"):
        benchmarks[name] = fetch_benchmark_returns(name, days)
    return benchmarks


# ═══════════════════════════════════════════════════════════════
# Alpha / Beta / 信息比率
# ═══════════════════════════════════════════════════════════════

def calculate_alpha_beta(
    portfolio_returns: list[float],
    benchmark_returns: list[float],
) -> dict:
    """计算组合 vs 基准的 Alpha 和 Beta。

    使用 OLS 回归：R_p = alpha + beta * R_b + epsilon

    Returns:
        {"alpha_annual": float, "beta": float, "r_squared": float,
         "tracking_error": float, "information_ratio": float}
    """
    n = min(len(portfolio_returns), len(benchmark_returns))
    if n < 5:
        return {"error": "数据不足"}

    pr = portfolio_returns[:n]
    br = benchmark_returns[:n]

    mean_p = sum(pr) / n
    mean_b = sum(br) / n

    cov_pb = sum((p - mean_p) * (b - mean_b) for p, b in zip(pr, br)) / n
    var_b = sum((b - mean_b) ** 2 for b in br) / n

    beta = cov_pb / var_b if var_b > 0 else 1.0
    alpha_daily = mean_p - beta * mean_b
    alpha_annual = alpha_daily * 242  # 年化

    # R²
    ss_res = sum((p - (alpha_daily + beta * b)) ** 2 for p, b in zip(pr, br))
    ss_tot = sum((p - mean_p) ** 2 for p in pr)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    # Tracking Error & Information Ratio
    excess = [p - b for p, b in zip(pr, br)]
    mean_excess = sum(excess) / n
    te = math.sqrt(sum((e - mean_excess) ** 2 for e in excess) / n) * math.sqrt(242)
    ir = (mean_excess * 242) / te if te > 0 else 0

    return {
        "alpha_annual": round(alpha_annual, 4),
        "beta": round(beta, 4),
        "r_squared": round(r_squared, 4),
        "tracking_error": round(te, 4),
        "information_ratio": round(ir, 4),
        "n_observations": n,
    }


def calculate_sharpe_ratio(
    returns: list[float],
    risk_free_rate: float = 0.02,
) -> float:
    """计算年化夏普比率。"""
    if len(returns) < 2:
        return 0.0
    mean_ret = sum(returns) / len(returns)
    var = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
    daily_vol = math.sqrt(var)
    annual_vol = daily_vol * math.sqrt(242)
    annual_ret = mean_ret * 242
    sharpe = (annual_ret - risk_free_rate) / annual_vol if annual_vol > 0 else 0
    return round(sharpe, 4)


def calculate_max_drawdown(returns: list[float]) -> dict:
    """计算最大回撤。"""
    if not returns:
        return {"max_drawdown": 0, "peak_date": "", "trough_date": ""}

    cumulative = [1.0]
    for r in returns:
        cumulative.append(cumulative[-1] * (1 + r / 100))

    peak = cumulative[0]
    peak_idx = 0
    max_dd = 0
    dd_peak_idx = 0
    dd_trough_idx = 0

    for i, val in enumerate(cumulative):
        if val > peak:
            peak = val
            peak_idx = i
        dd = (peak - val) / peak
        if dd > max_dd:
            max_dd = dd
            dd_peak_idx = peak_idx
            dd_trough_idx = i

    return {
        "max_drawdown": round(max_dd * 100, 2),
        "peak_index": dd_peak_idx,
        "trough_index": dd_trough_idx,
    }


# ═══════════════════════════════════════════════════════════════
# 行业归因（Brinson 简化版）
# ═══════════════════════════════════════════════════════════════

def sector_attribution(
    portfolio_stocks: list[dict],
    portfolio_returns: dict[str, float],
    benchmark_sectors: Optional[dict[str, float]] = None,
) -> dict:
    """行业归因分析。

    计算每个行业对组合收益的贡献。

    Args:
        portfolio_stocks: 组合股票列表（含 sector 字段）。
        portfolio_returns: {code: return_pct} 各股收益率。
        benchmark_sectors: {sector: benchmark_weight} 基准行业权重（可选）。

    Returns:
        {
            "sector_contributions": {sector: {"return": float, "weight": float, "contribution": float}},
            "total_return": float,
            "top_sectors": list,
        }
    """
    # 按行业分组
    sector_stocks = defaultdict(list)
    for stock in portfolio_stocks:
        sector = stock.get("sector") or "未分类"
        sector_stocks[sector].append(stock)

    contributions = {}
    total_weight = sum(s.get("position_pct", 20) for s in portfolio_stocks)
    if total_weight == 0:
        total_weight = 100

    for sector, stocks in sector_stocks.items():
        # 行业权重
        sector_weight = sum(s.get("position_pct", 20) for s in stocks) / total_weight

        # 行业平均收益
        sector_rets = []
        for s in stocks:
            code = s.get("code", "")
            if code in portfolio_returns:
                sector_rets.append(portfolio_returns[code])

        sector_return = sum(sector_rets) / len(sector_rets) if sector_rets else 0

        # 行业贡献 = 行业权重 × 行业收益
        contribution = sector_weight * sector_return / 100

        contributions[sector] = {
            "return": round(sector_return, 2),
            "weight": round(sector_weight * 100, 1),
            "contribution": round(contribution * 100, 2),
            "stock_count": len(stocks),
        }

    total_return = sum(c["contribution"] for c in contributions.values())
    sorted_sectors = sorted(
        contributions.items(), key=lambda x: x[1]["contribution"], reverse=True
    )

    return {
        "sector_contributions": contributions,
        "total_return": round(total_return, 2),
        "top_sectors": [
            {"sector": s, **info} for s, info in sorted_sectors[:5]
        ],
    }


# ═══════════════════════════════════════════════════════════════
# 因子归因
# ═══════════════════════════════════════════════════════════════

def factor_attribution(
    records: list[dict],
    return_field: str = "forward_return_latest",
) -> dict:
    """因子归因：分析各评分因子对收益的贡献。

    方法：对每个因子做截面回归，计算因子暴露与收益的相关性。

    Returns:
        {
            "factor_exposures": {factor: {"avg_exposure": float, "ic": float}},
            "factor_returns": {factor: {"long_short_return": float}},
        }
    """
    factor_names = [
        "upside", "quality", "consensus", "sector", "trend",
        "fundamentals", "capital_flow", "volume_confirm", "logic", "target",
    ]

    # 收集各因子值和收益
    factor_values = defaultdict(list)
    returns = []

    for rec in records:
        detail = rec.get("score_detail") or {}
        ret = rec.get(return_field)
        if ret is None:
            continue
        returns.append(ret)

        for fname in factor_names:
            val = detail.get(fname)
            if val is not None:
                factor_values[fname].append(val)
            else:
                factor_values[fname].append(None)

    if not returns:
        return {"factor_exposures": {}, "factor_returns": {}}

    exposures = {}
    factor_returns = {}

    for fname in factor_names:
        vals = factor_values[fname]
        # 对齐有效值
        valid_pairs = [(v, r) for v, r in zip(vals, returns) if v is not None]
        if len(valid_pairs) < 5:
            continue

        vs = [p[0] for p in valid_pairs]
        rs = [p[1] for p in valid_pairs]

        avg_exposure = sum(vs) / len(vs)

        # IC
        n = len(valid_pairs)
        from adaptive_weights import _rank_values
        v_ranks = _rank_values(vs)
        r_ranks = _rank_values(rs)
        d_sq = sum((vr - rr) ** 2 for vr, rr in zip(v_ranks, r_ranks))
        ic = 1 - 6 * d_sq / (n * (n * n - 1)) if n > 1 else 0

        # Long-short: top 20% vs bottom 20%
        sorted_pairs = sorted(valid_pairs, key=lambda x: x[0])
        n_group = max(1, len(sorted_pairs) // 5)
        bottom_ret = sum(p[1] for p in sorted_pairs[:n_group]) / n_group
        top_ret = sum(p[1] for p in sorted_pairs[-n_group:]) / n_group

        exposures[fname] = {
            "avg_exposure": round(avg_exposure, 2),
            "ic": round(ic, 4),
            "count": len(valid_pairs),
        }
        factor_returns[fname] = {
            "long_short_return": round(top_ret - bottom_ret, 2),
            "top_return": round(top_ret, 2),
            "bottom_return": round(bottom_ret, 2),
        }

    return {
        "factor_exposures": exposures,
        "factor_returns": factor_returns,
    }


# ═══════════════════════════════════════════════════════════════
# 综合绩效报告
# ═══════════════════════════════════════════════════════════════

def generate_performance_report(
    records: list[dict],
    benchmark: str = "csi300",
) -> dict:
    """生成综合绩效报告。

    Returns:
        {
            "summary": {total_return, annual_return, sharpe, max_dd, ...},
            "vs_benchmark": {alpha, beta, ir, tracking_error, ...},
            "factor_attribution": {...},
            "sector_attribution": {...},
        }
    """
    # 收集组合收益
    portfolio_returns = []
    codes_with_returns = []
    for rec in records:
        ret = rec.get("forward_return_latest")
        if ret is not None:
            portfolio_returns.append(ret)
            codes_with_returns.append(rec.get("code"))

    if not portfolio_returns:
        return {"error": "无收益数据"}

    # 基本指标
    total_return = sum(portfolio_returns)
    avg_return = total_return / len(portfolio_returns)
    sharpe = calculate_sharpe_ratio(portfolio_returns)
    dd_info = calculate_max_drawdown(portfolio_returns)

    # 基准对比
    bench_data = fetch_benchmark_returns(benchmark, days=60)
    bench_returns = [d["return_pct"] for d in bench_data]

    vs_benchmark = {}
    if bench_returns:
        vs_benchmark = calculate_alpha_beta(portfolio_returns, bench_returns[:len(portfolio_returns)])

    # 因子归因
    factor_attr = factor_attribution(records)

    # 行业归因
    portfolio_returns_map = {}
    for rec in records:
        code = rec.get("code")
        ret = rec.get("forward_return_latest")
        if code and ret is not None:
            portfolio_returns_map[code] = ret

    sector_attr = sector_attribution(records, portfolio_returns_map)

    report = {
        "summary": {
            "total_recommendations": len(records),
            "total_return_pct": round(total_return, 2),
            "avg_return_pct": round(avg_return, 2),
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": dd_info["max_drawdown"],
            "win_rate": round(
                sum(1 for r in portfolio_returns if r > 0) / len(portfolio_returns) * 100, 1
            ),
            "benchmark": benchmark,
        },
        "vs_benchmark": vs_benchmark,
        "factor_attribution": factor_attr,
        "sector_attribution": sector_attr,
        "generated_at": _now_shanghai().isoformat(),
    }

    # 保存
    ATTRIBUTION_DIR.mkdir(parents=True, exist_ok=True)
    ts = _now_shanghai().strftime("%Y%m%d_%H%M%S")
    output_file = ATTRIBUTION_DIR / f"performance_{ts}.json"
    output_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_file"] = str(output_file)

    return report


def format_performance_report(report: dict) -> str:
    """格式化绩效报告为 Markdown。"""
    if report.get("error"):
        return f"# 绩效报告\n\n> {report['error']}\n"

    summary = report.get("summary", {})
    vs = report.get("vs_benchmark", {})
    lines = [
        "# 推荐系统绩效报告",
        "",
        f"> 生成时间: {_now_shanghai().strftime('%Y-%m-%d %H:%M:%S 北京时间')}",
        "",
        "## 一、核心指标\n",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 推荐总数 | {summary.get('total_recommendations', 0)} |",
        f"| 累计收益率 | {summary.get('total_return_pct', 0):+.2f}% |",
        f"| 平均收益率 | {summary.get('avg_return_pct', 0):+.2f}% |",
        f"| 夏普比率 | {summary.get('sharpe_ratio', 0):.2f} |",
        f"| 最大回撤 | {summary.get('max_drawdown_pct', 0):.2f}% |",
        f"| 胜率 | {summary.get('win_rate', 0):.1f}% |",
        "",
    ]

    if vs and not vs.get("error"):
        lines.extend([
            "## 二、基准对比\n",
            f"| 指标 | 数值 |",
            f"|------|------|",
            f"| Alpha（年化） | {vs.get('alpha_annual', 0):+.2%} |",
            f"| Beta | {vs.get('beta', 0):.2f} |",
            f"| R² | {vs.get('r_squared', 0):.2f} |",
            f"| 跟踪误差 | {vs.get('tracking_error', 0):.2%} |",
            f"| 信息比率 | {vs.get('information_ratio', 0):.2f} |",
            "",
        ])

    # 因子归因
    factor_attr = report.get("factor_attribution", {})
    factor_returns = factor_attr.get("factor_returns", {})
    if factor_returns:
        lines.extend([
            "## 三、因子归因\n",
            "| 因子 | 多头收益 | 空头收益 | 多空收益 |",
            "|------|----------|----------|----------|",
        ])
        sorted_factors = sorted(
            factor_returns.items(),
            key=lambda x: abs(x[1].get("long_short_return", 0)),
            reverse=True,
        )
        for fname, info in sorted_factors:
            lines.append(
                f"| {fname} | {info.get('top_return', 0):+.2f}% | "
                f"{info.get('bottom_return', 0):+.2f}% | "
                f"{info.get('long_short_return', 0):+.2f}% |"
            )
        lines.append("")

    # 行业归因
    sector_attr = report.get("sector_attribution", {})
    top_sectors = sector_attr.get("top_sectors", [])
    if top_sectors:
        lines.extend([
            "## 四、行业归因\n",
            "| 行业 | 权重 | 行业收益 | 贡献 |",
            "|------|------|----------|------|",
        ])
        for s in top_sectors:
            lines.append(
                f"| {s['sector']} | {s.get('weight', 0):.1f}% | "
                f"{s.get('return', 0):+.2f}% | {s.get('contribution', 0):+.2f}% |"
            )
        lines.append("")

    lines.append("---\n*本报告由绩效归因系统自动生成。*")
    return "\n".join(lines)
