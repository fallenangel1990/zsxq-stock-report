"""因子研究框架。

提供分组回测（Quintile Analysis）、因子相关矩阵、
因子换手率分析，用于系统化评估和优化评分因子。
"""

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from adaptive_weights import _rank_values, FACTOR_TO_WEIGHT


DATA_DIR = Path(__file__).parent / "data"
FACTOR_RESEARCH_DIR = DATA_DIR / "factor_research"


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


# ═══════════════════════════════════════════════════════════════
# 分组回测（Quintile Analysis）
# ═══════════════════════════════════════════════════════════════

def quintile_analysis(
    records: list[dict],
    factor_name: str,
    n_groups: int = 5,
    return_field: str = "forward_return_latest",
) -> dict:
    """分组回测：按因子值分 N 组，检查每组收益是否单调递增。

    Args:
        records: 推荐历史。
        factor_name: 因子名称（score_detail 中的 key）。
        n_groups: 分组数。

    Returns:
        {
            "factor": str,
            "groups": [{"group": int, "avg_factor": float, "avg_return": float,
                        "win_rate": float, "count": int, "cumulative_return": float}],
            "monotonicity": float,  # 单调性指标 [-1, 1]
            "spread": float,  # top - bottom 组收益差
        }
    """
    # 收集因子值和收益
    pairs = []
    for rec in records:
        detail = rec.get("score_detail") or {}
        val = rec.get(factor_name) or detail.get(factor_name)
        ret = rec.get(return_field)
        if val is not None and ret is not None:
            pairs.append((val, ret))

    if len(pairs) < n_groups * 2:
        return {"factor": factor_name, "groups": [], "monotonicity": 0, "spread": 0}

    # 按因子值排序分组
    pairs.sort(key=lambda x: x[0])
    group_size = len(pairs) // n_groups
    groups = []

    for g in range(n_groups):
        start = g * group_size
        end = start + group_size if g < n_groups - 1 else len(pairs)
        group_pairs = pairs[start:end]

        factor_vals = [p[0] for p in group_pair]
        returns = [p[1] for p in group_pairs]

        avg_factor = sum(factor_vals) / len(factor_vals)
        avg_return = sum(returns) / len(returns)
        win_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
        cumulative = sum(returns)

        groups.append({
            "group": g + 1,
            "avg_factor": round(avg_factor, 2),
            "avg_return": round(avg_return, 2),
            "win_rate": round(win_rate, 1),
            "count": len(group_pairs),
            "cumulative_return": round(cumulative, 2),
        })

    # 单调性：相邻组收益差的方向一致性
    diffs = [groups[i + 1]["avg_return"] - groups[i]["avg_return"]
             for i in range(len(groups) - 1)]
    if diffs:
        monotonicity = sum(1 for d in diffs if d > 0) / len(diffs) * 2 - 1
    else:
        monotonicity = 0

    spread = groups[-1]["avg_return"] - groups[0]["avg_return"]

    return {
        "factor": factor_name,
        "groups": groups,
        "monotonicity": round(monotonicity, 3),
        "spread": round(spread, 2),
    }


def run_all_quintile_analyses(
    records: list[dict],
    return_field: str = "forward_return_latest",
) -> dict[str, dict]:
    """对所有因子运行分组回测。"""
    factors = list(FACTOR_TO_WEIGHT.keys()) + ["score", "buy_score", "technical_score"]
    results = {}
    for fname in factors:
        result = quintile_analysis(records, fname, return_field=return_field)
        if result["groups"]:
            results[fname] = result
    return results


# ═══════════════════════════════════════════════════════════════
# 因子相关矩阵
# ═══════════════════════════════════════════════════════════════

def factor_correlation_matrix(records: list[dict]) -> dict:
    """计算因子间的 Pearson 相关系数矩阵。

    Returns:
        {
            "matrix": {factor_a: {factor_b: correlation}},
            "high_correlations": [(factor_a, factor_b, corr)],
        }
    """
    factors = list(FACTOR_TO_WEIGHT.keys())
    factor_vals = defaultdict(list)

    for rec in records:
        detail = rec.get("score_detail") or {}
        for fname in factors:
            val = detail.get(fname)
            factor_vals[fname].append(val)

    # 对齐长度
    min_len = min(len(v) for v in factor_vals.values()) if factor_vals else 0
    if min_len < 5:
        return {"matrix": {}, "high_correlations": []}

    for fname in factors:
        factor_vals[fname] = factor_vals[fname][:min_len]

    # 计算相关矩阵
    matrix = {}
    high_corrs = []

    for f1 in factors:
        matrix[f1] = {}
        for f2 in factors:
            v1 = factor_vals[f1]
            v2 = factor_vals[f2]

            # 去掉 None
            pairs = [(a, b) for a, b in zip(v1, v2) if a is not None and b is not None]
            if len(pairs) < 5:
                matrix[f1][f2] = 0
                continue

            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            n = len(xs)
            mx = sum(xs) / n
            my = sum(ys) / n
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
            sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / n)
            sy = math.sqrt(sum((y - my) ** 2 for y in ys) / n)
            corr = cov / (sx * sy) if sx > 0 and sy > 0 else 0
            matrix[f1][f2] = round(corr, 3)

            if f1 < f2 and abs(corr) > 0.7:
                high_corrs.append((f1, f2, round(corr, 3)))

    high_corrs.sort(key=lambda x: abs(x[2]), reverse=True)

    return {
        "matrix": matrix,
        "high_correlations": high_corrs,
    }


# ═══════════════════════════════════════════════════════════════
# 因子换手率
# ═══════════════════════════════════════════════════════════════

def factor_turnover(
    records_by_date: dict[str, list[dict]],
    factor_name: str,
    top_pct: float = 0.2,
) -> dict:
    """计算因子换手率：相邻日期 top 组合的重叠度。

    Args:
        records_by_date: {date_str: [records]} 按日期分组的推荐记录。
        factor_name: 因子名称。
        top_pct: 选取 top 百分比。

    Returns:
        {
            "avg_turnover": float,  # 平均换手率 (0-1, 越低越好)
            "turnover_series": [float],
        }
    """
    sorted_dates = sorted(records_by_date.keys())
    if len(sorted_dates) < 2:
        return {"avg_turnover": 0, "turnover_series": []}

    turnovers = []
    prev_top = set()

    for date in sorted_dates:
        recs = records_by_date[date]
        # 按因子值排序
        scored = []
        for r in recs:
            detail = r.get("score_detail") or {}
            val = r.get(factor_name) or detail.get(factor_name)
            if val is not None:
                scored.append((val, r.get("code", "")))

        if not scored:
            continue

        scored.sort(key=lambda x: x[0], reverse=True)
        n_top = max(1, int(len(scored) * top_pct))
        current_top = set(code for _, code in scored[:n_top] if code)

        if prev_top:
            overlap = len(current_top & prev_top)
            total = len(current_top | prev_top)
            turnover = 1 - overlap / total if total > 0 else 1
            turnovers.append(round(turnover, 3))

        prev_top = current_top

    avg = sum(turnovers) / len(turnovers) if turnovers else 0
    return {
        "avg_turnover": round(avg, 3),
        "turnover_series": turnovers,
    }


# ═══════════════════════════════════════════════════════════════
# 综合因子研究报告
# ═══════════════════════════════════════════════════════════════

def generate_factor_research_report(records: list[dict]) -> dict:
    """生成因子研究综合报告。"""
    quintiles = run_all_quintile_analyses(records)
    correlations = factor_correlation_matrix(records)

    # 按单调性排序
    sorted_quintiles = sorted(
        quintiles.items(),
        key=lambda x: abs(x[1].get("monotonicity", 0)),
        reverse=True,
    )

    report = {
        "quintile_analysis": quintiles,
        "correlation_matrix": correlations,
        "top_monotonic_factors": [
            {"factor": f, "monotonicity": q["monotonicity"], "spread": q["spread"]}
            for f, q in sorted_quintiles[:5]
        ],
        "high_correlations": correlations.get("high_correlations", []),
        "generated_at": _now_shanghai().isoformat(),
    }

    # 保存
    FACTOR_RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    ts = _now_shanghai().strftime("%Y%m%d_%H%M%S")
    output_file = FACTOR_RESEARCH_DIR / f"research_{ts}.json"
    output_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_file"] = str(output_file)

    return report


def format_factor_research_report(report: dict) -> str:
    """格式化因子研究报告为 Markdown。"""
    lines = [
        "# 因子研究报告",
        "",
        f"> 生成时间: {_now_shanghai().strftime('%Y-%m-%d %H:%M:%S 北京时间')}",
        "",
    ]

    # 分组回测
    quintiles = report.get("quintile_analysis", {})
    if quintiles:
        lines.extend([
            "## 一、分组回测（Quintile Analysis）\n",
            "| 因子 | 单调性 | 多空收益 | Q1收益 | Q5收益 | 样本数 |",
            "|------|--------|----------|--------|--------|--------|",
        ])
        sorted_q = sorted(
            quintiles.items(),
            key=lambda x: abs(x[1].get("monotonicity", 0)),
            reverse=True,
        )
        for fname, q in sorted_q:
            groups = q.get("groups", [])
            if len(groups) >= 2:
                lines.append(
                    f"| {fname} | {q['monotonicity']:+.3f} | {q['spread']:+.2f}% | "
                    f"{groups[0]['avg_return']:+.2f}% | {groups[-1]['avg_return']:+.2f}% | "
                    f"{sum(g['count'] for g in groups)} |"
                )
        lines.append("")

    # 相关矩阵
    high_corrs = report.get("high_correlations", [])
    if high_corrs:
        lines.extend([
            "## 二、高相关因子（需警惕共线性）\n",
            "| 因子A | 因子B | 相关系数 |",
            "|-------|-------|----------|",
        ])
        for f1, f2, corr in high_corrs:
            lines.append(f"| {f1} | {f2} | {corr:+.3f} |")
        lines.append("")

    # Top 因子
    top_factors = report.get("top_monotonic_factors", [])
    if top_factors:
        lines.extend([
            "## 三、最优因子排名\n",
            "| 排名 | 因子 | 单调性 | 多空收益 |",
            "|------|------|--------|----------|",
        ])
        for i, f in enumerate(top_factors, 1):
            lines.append(
                f"| {i} | {f['factor']} | {f['monotonicity']:+.3f} | {f['spread']:+.2f}% |"
            )
        lines.append("")

    lines.append("---\n*本报告由因子研究系统自动生成。*")
    return "\n".join(lines)
