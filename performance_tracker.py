"""推荐绩效跟踪模块。

追踪推荐后的实际表现，计算胜率、盈亏比等指标，
按来源/板块/评分区间统计，输出绩效报告。
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from price_fetcher import fetch_prices


HISTORY_FILE = Path(__file__).parent / "data" / "summary" / "history" / "recommendations.jsonl"
PERFORMANCE_OUTPUT = Path(__file__).parent / "data" / "summary" / "performance"


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


def track_performance(history_path: Optional[Path] = None) -> dict:
    """追踪推荐绩效。

    Returns:
        {
            "summary": {total, win_count, loss_count, win_rate, avg_return, ...},
            "by_sector": {sector: {win_rate, avg_return, count}},
            "by_score_range": {range: {win_rate, avg_return, count}},
            "by_opportunity_type": {type: {win_rate, avg_return, count}},
            "top_winners": [...],
            "top_losers": [...],
        }
    """
    records = _load_history(history_path)
    if not records:
        return {"error": f"无推荐历史数据（{history_path or HISTORY_FILE}）"}

    # 获取所有推荐股票的当前价格
    codes = list(set(r.get("code") for r in records if r.get("code")))
    current_prices = fetch_prices(codes)

    # 计算每只股票的收益
    evaluated = []
    for rec in records:
        code = rec.get("code")
        entry_price = rec.get("current_price")
        if not code or not entry_price or entry_price <= 0:
            continue
        price_info = current_prices.get(code)
        if not price_info or not price_info.get("price"):
            continue

        current_price = price_info["price"]
        ret = round((current_price / entry_price - 1) * 100, 2)
        evaluated.append({
            **rec,
            "current_price_now": current_price,
            "return_pct": ret,
            "is_win": ret > 0,
        })

    if not evaluated:
        return {"error": "无可评估的推荐记录"}

    # 汇总统计
    total = len(evaluated)
    wins = [e for e in evaluated if e["is_win"]]
    losses = [e for e in evaluated if not e["is_win"]]
    returns = [e["return_pct"] for e in evaluated]

    summary = {
        "total": total,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / total * 100, 1) if total else 0,
        "avg_return": round(sum(returns) / total, 2) if total else 0,
        "max_return": round(max(returns), 2) if returns else 0,
        "min_return": round(min(returns), 2) if returns else 0,
        "median_return": round(sorted(returns)[len(returns) // 2], 2) if returns else 0,
    }

    # 盈亏比
    avg_win = sum(e["return_pct"] for e in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(e["return_pct"] for e in losses) / len(losses)) if losses else 1
    summary["profit_loss_ratio"] = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

    # 按板块统计
    by_sector = _group_stats(evaluated, "sector")

    # 按评分区间统计
    def score_range(score):
        if score < 3:
            return "1-3分"
        elif score < 5:
            return "3-5分"
        elif score < 7:
            return "5-7分"
        else:
            return "7-10分"

    for e in evaluated:
        e["_score_range"] = score_range(e.get("score", 0))
    by_score_range = _group_stats(evaluated, "_score_range")

    # 按机会类型统计
    by_opportunity_type = _group_stats(evaluated, "opportunity_type")

    # Top 赢家和输家
    evaluated.sort(key=lambda e: e["return_pct"], reverse=True)
    top_winners = [
        {
            "name": e.get("name"),
            "code": e.get("code"),
            "return_pct": e["return_pct"],
            "score": e.get("score"),
            "sector": e.get("sector"),
            "entry_price": e.get("current_price"),
            "current_price": e.get("current_price_now"),
        }
        for e in evaluated[:5]
    ]
    top_losers = [
        {
            "name": e.get("name"),
            "code": e.get("code"),
            "return_pct": e["return_pct"],
            "score": e.get("score"),
            "sector": e.get("sector"),
            "entry_price": e.get("current_price"),
            "current_price": e.get("current_price_now"),
        }
        for e in evaluated[-5:]
    ]

    result = {
        "summary": summary,
        "by_sector": by_sector,
        "by_score_range": by_score_range,
        "by_opportunity_type": by_opportunity_type,
        "top_winners": top_winners,
        "top_losers": top_losers,
        "generated_at": _now_shanghai().isoformat(),
    }

    # 保存结果
    PERFORMANCE_OUTPUT.mkdir(parents=True, exist_ok=True)
    ts = _now_shanghai().strftime("%Y%m%d_%H%M%S")
    output_file = PERFORMANCE_OUTPUT / f"performance_{ts}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    result["output_file"] = str(output_file)

    return result


def _group_stats(evaluated: list[dict], key: str) -> dict:
    """按指定字段分组统计。"""
    groups = defaultdict(list)
    for e in evaluated:
        group_key = e.get(key) or "未分类"
        groups[group_key].append(e["return_pct"])

    result = {}
    for g_key, rets in sorted(groups.items()):
        total = len(rets)
        wins = sum(1 for r in rets if r > 0)
        result[g_key] = {
            "count": total,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "avg_return": round(sum(rets) / total, 2) if total else 0,
        }
    return result


def format_performance_report(metrics: dict) -> str:
    """将绩效指标格式化为 Markdown 报告。"""
    if metrics.get("error"):
        return f"# 推荐绩效报告\n\n> {metrics['error']}\n"

    s = metrics.get("summary", {})
    lines = [
        "# 推荐绩效跟踪报告",
        "",
        f"> 生成时间: {_now_shanghai().strftime('%Y-%m-%d %H:%M:%S 北京时间')}",
        f"> 追踪总数: {s.get('total', 0)} 只",
        "",
        "## 总体绩效\n",
        f"- 胜率: **{s.get('win_rate', 0):.1f}%**（{s.get('win_count', 0)}胜 / {s.get('loss_count', 0)}负）",
        f"- 平均收益率: **{s.get('avg_return', 0):+.2f}%**",
        f"- 中位数收益率: {s.get('median_return', 0):+.2f}%",
        f"- 最大盈利: {s.get('max_return', 0):+.2f}% / 最大亏损: {s.get('min_return', 0):+.2f}%",
        f"- 盈亏比: **{s.get('profit_loss_ratio', 0):.2f}**",
        "",
    ]

    # 评分分组
    by_score = metrics.get("by_score_range", {})
    if by_score:
        lines.append("## 按评分区间\n")
        lines.append("| 评分区间 | 胜率 | 平均收益 | 样本数 |")
        lines.append("|----------|------|----------|--------|")
        for group in ["1-3分", "3-5分", "5-7分", "7-10分"]:
            info = by_score.get(group)
            if info:
                lines.append(
                    f"| {group} | {info['win_rate']:.1f}% | {info['avg_return']:+.2f}% | {info['count']} |"
                )
        lines.append("")

    # 按板块
    by_sector = metrics.get("by_sector", {})
    if by_sector:
        lines.append("## 按板块\n")
        lines.append("| 板块 | 胜率 | 平均收益 | 样本数 |")
        lines.append("|------|------|----------|--------|")
        sorted_sectors = sorted(by_sector.items(), key=lambda x: x[1]["avg_return"], reverse=True)
        for name, info in sorted_sectors[:10]:
            lines.append(
                f"| {name} | {info['win_rate']:.1f}% | {info['avg_return']:+.2f}% | {info['count']} |"
            )
        lines.append("")

    # Top 赢家/输家
    winners = metrics.get("top_winners", [])
    losers = metrics.get("top_losers", [])
    if winners:
        lines.append("## 最佳推荐 Top5\n")
        lines.append("| 股票 | 收益率 | 评分 | 板块 |")
        lines.append("|------|--------|------|------|")
        for w in winners:
            lines.append(f"| {w.get('name', '-')} | {w['return_pct']:+.2f}% | {w.get('score', '-')} | {w.get('sector', '-')} |")
        lines.append("")
    if losers:
        lines.append("## 最差推荐 Top5\n")
        lines.append("| 股票 | 收益率 | 评分 | 板块 |")
        lines.append("|------|--------|------|------|")
        for l_ in losers:
            lines.append(f"| {l_.get('name', '-')} | {l_['return_pct']:+.2f}% | {l_.get('score', '-')} | {l_.get('sector', '-')} |")
        lines.append("")

    lines.append("---\n*本报告由绩效跟踪系统自动生成。*")
    return "\n".join(lines)
