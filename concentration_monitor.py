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

# 等级排序（用于综合判定）
_LEVEL_ORDER = {
    LEVEL_NORMAL: 0,
    LEVEL_ELEVATED: 1,
    LEVEL_DANGER: 2,
}

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
    except Exception as exc:
        boards = []

    flow_signal = _compute_flow_signal(boards, t["flow_top3_pct"])

    return {
        "level": flow_signal["level"],
        "timestamp": _now_shanghai().isoformat(),
        "signals": {
            "flow_concentration": flow_signal,
            "turnover_concentration": _unavailable_signal("待实现"),
            "breadth_divergence": _unavailable_signal("待实现"),
        },
        "summary": "",
    }
