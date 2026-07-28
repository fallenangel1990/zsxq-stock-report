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
