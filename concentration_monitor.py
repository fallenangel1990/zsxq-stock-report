"""市场资金集中度监控模块。

从三个维度综合判定全市场资金是否过度涌入少数板块/个股：
1. 板块资金净流入集中度（top3 板块净流入占比）
2. 板块成交额集中度（top3 板块成交额占比）
3. 市场宽度背离（指数上涨但涨跌比 < 1）

输出标准 ConcentrationSnapshot，供报告和盘中预警共享调用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sector_monitor import fetch_boards, fetch_market_indices

# ── 等级常量 ──
LEVEL_NORMAL = "normal"
LEVEL_ELEVATED = "elevated"
LEVEL_DANGER = "danger"
LEVEL_UNAVAILABLE = "unavailable"

# ── 默认阈值 ──
_DEFAULT_THRESHOLDS = {
    "flow_top3_pct": {"elevated": 0.50, "danger": 0.70},
    "turnover_top3_pct": {"elevated": 0.45, "danger": 0.60},
    "breadth_ratio": {"elevated": 1.0, "danger": 0.6},
}


def _now_shanghai() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _compute_flow_signal(
    boards: list[dict], thresholds: dict,
) -> dict:
    """计算板块资金净流入集中度信号。

    value = top3 正净流入之和 / 全市场正净流入之和。
    仅统计净流入 > 0 的板块（流出板块不参与集中度计算）。
    """
    if not boards:
        return _unavailable_signal("无板块数据")

    positive = [b for b in boards if b.get("main_net_yi", 0) > 0]
    total = sum(b.get("main_net_yi", 0) for b in positive)
    if total <= 0:
        return {"value": 0.0, "level": LEVEL_NORMAL, "top3": []}

    top3 = sorted(positive, key=lambda b: b["main_net_yi"], reverse=True)[:3]
    top3_sum = sum(b["main_net_yi"] for b in top3)
    value = top3_sum / total

    top3_out = [
        {
            "name": b.get("name", ""),
            "share": round(b["main_net_yi"] / total, 4),
            "net_inflow_yi": b["main_net_yi"],
        }
        for b in top3
    ]
    return {
        "value": round(value, 4),
        "level": _judge_threshold(value, thresholds),
        "top3": top3_out,
    }


def _judge_threshold(value: float, thresholds: dict) -> str:
    """根据 elevated/danger 阈值判定等级。"""
    if value >= thresholds["danger"]:
        return LEVEL_DANGER
    if value >= thresholds["elevated"]:
        return LEVEL_ELEVATED
    return LEVEL_NORMAL


def _unavailable_signal(reason: str = "") -> dict:
    return {"value": 0.0, "level": LEVEL_UNAVAILABLE, "top3": [], "reason": reason}


def _compute_turnover_signal(
    boards: list[dict], thresholds: dict,
) -> dict:
    """计算板块成交额集中度信号。"""
    if not boards:
        return _unavailable_signal("无板块数据")

    total = sum(b.get("amount_yi", 0) for b in boards)
    if total <= 0:
        return {"value": 0.0, "level": LEVEL_NORMAL, "top3": []}

    top3 = sorted(boards, key=lambda b: b.get("amount_yi", 0), reverse=True)[:3]
    top3_sum = sum(b.get("amount_yi", 0) for b in top3)
    value = top3_sum / total

    top3_out = [
        {"name": b.get("name", ""), "share": round(b["amount_yi"] / total, 4)}
        for b in top3
    ]
    return {
        "value": round(value, 4),
        "level": _judge_threshold(value, thresholds),
        "top3": top3_out,
    }


def compute_concentration(
    thresholds: Optional[dict] = None,
) -> dict:
    """计算市场资金集中度快照。

    Args:
        thresholds: 阈值配置，None 使用默认值。

    Returns:
        ConcentrationSnapshot dict。
    """
    t = thresholds or _DEFAULT_THRESHOLDS

    try:
        boards = fetch_boards(board_type="industry")
    except Exception:
        boards = []

    flow_signal = _compute_flow_signal(boards, t["flow_top3_pct"])
    turnover_signal = _compute_turnover_signal(boards, t["turnover_top3_pct"])

    try:
        indices = fetch_market_indices()
    except Exception:
        indices = []

    breadth_signal = _compute_breadth_signal(indices, t["breadth_ratio"])

    signals = [flow_signal, turnover_signal, breadth_signal]
    level = _aggregate_level(signals)

    return {
        "level": level,
        "timestamp": _now_shanghai().isoformat(),
        "signals": {
            "flow_concentration": flow_signal,
            "turnover_concentration": turnover_signal,
            "breadth_divergence": breadth_signal,
        },
        "summary": _build_summary({"level": level, "signals": {
            "flow_concentration": flow_signal,
        }}),
    }


def _aggregate_level(signals: list[dict]) -> str:
    """综合多信号等级。

    规则：
    - 所有有效信号 normal → normal
    - ≥2 个有效信号 ≥ elevated → elevated
    - ≥2 个有效信号 ≥ danger → danger
    - 其余（如仅 1 个 elevated）→ 降级为 normal
    - unavailable 不参与计数
    """
    valid = [s["level"] for s in signals if s.get("level") != LEVEL_UNAVAILABLE]
    if not valid:
        return LEVEL_UNAVAILABLE

    elevated_count = sum(1 for l in valid if l in (LEVEL_ELEVATED, LEVEL_DANGER))
    danger_count = sum(1 for l in valid if l == LEVEL_DANGER)

    if danger_count >= 2:
        return LEVEL_DANGER
    if elevated_count >= 2:
        return LEVEL_ELEVATED
    return LEVEL_NORMAL


def _build_summary(snapshot: dict) -> str:
    """生成一句话总结。"""
    signals = snapshot.get("signals", {})
    flow = signals.get("flow_concentration", {})
    top3_names = "、".join(t["name"] for t in flow.get("top3", [])[:3])
    level = snapshot.get("level", "normal")

    if level == LEVEL_DANGER and top3_names:
        return f"资金高度集中于{top3_names}板块"
    if level == LEVEL_ELEVATED and top3_names:
        return f"资金集中度偏高，集中于{top3_names}板块"
    if level == LEVEL_UNAVAILABLE:
        return "集中度数据暂缺"
    return "资金分布较分散"


def _compute_breadth_signal(
    indices: list[dict], thresholds: dict,
) -> dict:
    """计算市场宽度背离信号。

    仅在指数上涨时判定：涨跌比 < 1 说明权重拉个股跌，资金集中。
    指数下跌时不触发（下跌集中 ≠ 追高风险）。
    """
    if not indices:
        return _unavailable_signal("无指数数据")

    # 优先使用上证指数，缺失时取第一个
    sh_index = next(
        (i for i in indices if i.get("code") in ("1000001", "000001")),
        indices[0],
    )
    index_change = sh_index.get("change_pct", 0)
    up_count = sh_index.get("up_count", 0)
    down_count = sh_index.get("down_count", 0)

    if down_count <= 0:
        ratio = 1.0 if up_count > 0 else 0.0
    else:
        ratio = up_count / down_count

    # 指数下跌时不触发背离
    if index_change <= 0:
        return {
            "value": round(ratio, 2),
            "level": LEVEL_NORMAL,
            "index_change": index_change,
            "advance_decline_ratio": round(ratio, 2),
        }

    # 指数上涨时，比值越低越危险（阈值含义与 flow/turnover 相反）
    if ratio < thresholds["danger"]:
        level = LEVEL_DANGER
    elif ratio < thresholds["elevated"]:
        level = LEVEL_ELEVATED
    else:
        level = LEVEL_NORMAL

    return {
        "value": round(ratio, 2),
        "level": level,
        "index_change": index_change,
        "advance_decline_ratio": round(ratio, 2),
    }
