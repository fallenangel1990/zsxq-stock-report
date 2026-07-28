# 市场资金集中度指标 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增市场资金集中度指标，从板块资金净流入占比、板块成交额占比、市场宽度背离三个维度综合判定集中度过高风险，在交易员摘要区标红警告，并在盘中实时推送预警。

**Architecture:** 新建独立模块 `concentration_monitor.py` 作为单一数据源（复用 `sector_monitor.fetch_boards` 和 `fetch_market_indices` 获取数据），供 `stock_extractor.py`（报告展示）和 `intraday_monitor.py`（盘中预警）共享调用。模块输出标准 `ConcentrationSnapshot` dict，集成点无需关心数据获取细节。

**Tech Stack:** Python 3.9+, requests（通过 sector_monitor 间接调用），pytest，zoneinfo

## Global Constraints

- Python 兼容性：使用 `Optional[...]` 而非 `dict | None`（本地 Python 3.9）
- 数据降级：东方财富 push2 在 CI 中可能 502，必须有 unavailable 兜底（不静默跳过）
- 模块化：每个文件一个职责，不复制轮子（复用 sector_monitor 的数据获取）
- 报告中 normal 等级不展示（避免冗余）
- 盘中预警边缘触发，同等级单交易日最多推送 1 次
- 14:50 后停止盘中集中度检查
- 文件 < 800 行，函数 < 50 行
- 不可变原则：返回新对象，不修改输入

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `concentration_monitor.py` | **新建** | 核心计算：三信号采集 + 等级判定 + 综合汇总 |
| `test_concentration_monitor.py` | **新建** | 核心模块单元测试 |
| `config.yaml` | 修改 | 新增 `concentration:` 配置段 |
| `config.example.yaml` | 修改 | 新增 `concentration:` 配置段 |
| `stock_extractor.py` | 修改 | `_append_trader_summary()` 集成集中度仪表盘 |
| `intraday_monitor.py` | 修改 | `run_monitor()` 循环新增集中度检查 |

---

## Task 1: concentration_monitor.py 核心模块

**Files:**
- Create: `concentration_monitor.py`
- Test: `test_concentration_monitor.py`

**Interfaces:**
- Consumes: `sector_monitor.fetch_boards()`, `sector_monitor.fetch_market_indices()`（通过 import）
- Produces: `compute_concentration(thresholds=None) -> ConcentrationSnapshot`（主函数，下游唯一入口）

### Task 1.1: 骨架与常量

- [ ] **Step 1: 创建 concentration_monitor.py 骨架**

创建文件，包含模块 docstring、import、常量定义：

```python
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
```

- [ ] **Step 2: 提交骨架**

```bash
git add concentration_monitor.py
git commit -m "feat(concentration): add module skeleton with constants"
```

### Task 1.2: 板块资金净流入集中度

- [ ] **Step 1: 写失败测试**

在 `test_concentration_monitor.py` 中：

```python
"""市场资金集中度模块单元测试。"""
import pytest
from unittest.mock import patch
import concentration_monitor as cm


def _board(name, main_net_yi, amount_yi):
    """构造 mock 板块数据。"""
    return {"name": name, "main_net_yi": main_net_yi, "amount_yi": amount_yi}


class TestFlowConcentration:
    @patch("concentration_monitor.fetch_boards")
    def test_normal_when_dispersed(self, mock_boards):
        """资金分散 → normal。"""
        mock_boards.return_value = [
            _board("半导体", 10, 100),
            _board("AI", 8, 90),
            _board("机器人", 7, 80),
            _board("消费", 5, 70),
        ]
        snap = cm.compute_concentration()
        flow = snap["signals"]["flow_concentration"]
        assert flow["level"] == cm.LEVEL_NORMAL

    @patch("concentration_monitor.fetch_boards")
    def test_elevated_at_55_percent(self, mock_boards):
        """top3 = 55% → elevated。"""
        mock_boards.return_value = [
            _board("半导体", 55, 100),
            _board("AI", 30, 90),
            _board("机器人", 15, 80),
        ]
        snap = cm.compute_concentration()
        flow = snap["signals"]["flow_concentration"]
        assert flow["level"] == cm.LEVEL_ELEVATED
        assert flow["value"] == pytest.approx(1.0)  # 全部净流入集中在这3个

    @patch("concentration_monitor.fetch_boards")
    def test_danger_at_75_percent(self, mock_boards):
        """top3 = 75% → danger。"""
        mock_boards.return_value = [
            _board("半导体", 40, 100),
            _board("AI", 25, 90),
            _board("机器人", 10, 80),
            _board("消费", 10, 70),
            _board("医药", 8, 60),
            _board("金融", 7, 50),
        ]
        snap = cm.compute_concentration()
        flow = snap["signals"]["flow_concentration"]
        assert flow["level"] == cm.LEVEL_DANGER
        assert flow["value"] == pytest.approx(0.75)
        assert len(flow["top3"]) == 3
        assert flow["top3"][0]["name"] == "半导体"

    @patch("concentration_monitor.fetch_boards")
    def test_unavailable_on_fetch_failure(self, mock_boards):
        """fetch 异常 → unavailable。"""
        mock_boards.side_effect = RuntimeError("502")
        snap = cm.compute_concentration()
        assert snap["signals"]["flow_concentration"]["level"] == cm.LEVEL_UNAVAILABLE
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest test_concentration_monitor.py::TestFlowConcentration -v
```
Expected: FAIL（compute_concentration 尚未实现）

- [ ] **Step 3: 实现 compute_concentration 主函数 + 资金流信号**

在 `concentration_monitor.py` 添加：

```python
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
```

并添加骨架主函数（后续任务会扩展）：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest test_concentration_monitor.py::TestFlowConcentration -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add concentration_monitor.py test_concentration_monitor.py
git commit -m "feat(concentration): add flow concentration signal"
```

### Task 1.3: 板块成交额集中度

- [ ] **Step 1: 写失败测试**

```python
class TestTurnoverConcentration:
    @patch("concentration_monitor.fetch_boards")
    def test_normal_when_dispersed(self, mock_boards):
        mock_boards.return_value = [
            _board("半导体", 10, 100),
            _board("AI", 8, 90),
            _board("机器人", 7, 80),
            _board("消费", 5, 300),  # 成交大但资金流分散
        ]
        snap = cm.compute_concentration()
        to = snap["signals"]["turnover_concentration"]
        assert to["level"] == cm.LEVEL_NORMAL

    @patch("concentration_monitor.fetch_boards")
    def test_elevated_at_47_percent(self, mock_boards):
        """top3 成交额占 47% → elevated。"""
        mock_boards.return_value = [
            _board("半导体", 10, 200),
            _board("AI", 8, 150),
            _board("机器人", 7, 120),
            _board("消费", 5, 100),
            _board("医药", 3, 90),
            _board("金融", 2, 80),
        ]
        snap = cm.compute_concentration()
        to = snap["signals"]["turnover_concentration"]
        assert to["level"] == cm.LEVEL_ELEVATED
        assert to["value"] == pytest.approx(0.47, abs=0.01)

    @patch("concentration_monitor.fetch_boards")
    def test_danger_at_62_percent(self, mock_boards):
        """top3 成交额占 62% → danger。"""
        mock_boards.return_value = [
            _board("半导体", 10, 300),
            _board("AI", 8, 200),
            _board("机器人", 7, 120),
            _board("消费", 5, 100),
            _board("医药", 3, 80),
            _board("金融", 2, 70),
        ]
        snap = cm.compute_concentration()
        to = snap["signals"]["turnover_concentration"]
        assert to["level"] == cm.LEVEL_DANGER
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest test_concentration_monitor.py::TestTurnoverConcentration -v
```
Expected: FAIL

- [ ] **Step 3: 实现成交额信号**

添加函数并更新主函数：

```python
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
```

更新 `compute_concentration` 中替换 "待实现"：

```python
    turnover_signal = _compute_turnover_signal(boards, t["turnover_top3_pct"])
    # 替换原来的 _unavailable_signal("待实现")
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest test_concentration_monitor.py::TestTurnoverConcentration -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add concentration_monitor.py test_concentration_monitor.py
git commit -m "feat(concentration): add turnover concentration signal"
```

### Task 1.4: 市场宽度背离

- [ ] **Step 1: 写失败测试**

```python
class TestBreadthDivergence:
    @patch("concentration_monitor.fetch_market_indices")
    def test_normal_when_breadth_healthy(self, mock_indices):
        """指数涨 + 涨跌比 > 1 → normal。"""
        mock_indices.return_value = [
            {"code": "1000001", "name": "上证指数", "change_pct": 0.8,
             "up_count": 1500, "down_count": 800},
        ]
        snap = cm.compute_concentration()
        bd = snap["signals"]["breadth_divergence"]
        assert bd["level"] == cm.LEVEL_NORMAL

    @patch("concentration_monitor.fetch_market_indices")
    def test_elevated_when_ratio_below_1(self, mock_indices):
        """指数涨 + 涨跌比 < 1 → elevated。"""
        mock_indices.return_value = [
            {"code": "1000001", "name": "上证指数", "change_pct": 0.8,
             "up_count": 800, "down_count": 1000},
        ]
        snap = cm.compute_concentration()
        bd = snap["signals"]["breadth_divergence"]
        assert bd["level"] == cm.LEVEL_ELEVATED
        assert bd["advance_decline_ratio"] == pytest.approx(0.8)

    @patch("concentration_monitor.fetch_market_indices")
    def test_danger_when_ratio_below_06(self, mock_indices):
        """指数涨 + 涨跌比 < 0.6 → danger。"""
        mock_indices.return_value = [
            {"code": "1000001", "name": "上证指数", "change_pct": 1.2,
             "up_count": 550, "down_count": 1200},
        ]
        snap = cm.compute_concentration()
        bd = snap["signals"]["breadth_divergence"]
        assert bd["level"] == cm.LEVEL_DANGER

    @patch("concentration_monitor.fetch_market_indices")
    def test_no_trigger_when_index_falls(self, mock_indices):
        """指数跌 → 不触发背离（下跌集中 ≠ 追高风险）。"""
        mock_indices.return_value = [
            {"code": "1000001", "name": "上证指数", "change_pct": -0.5,
             "up_count": 600, "down_count": 1500},
        ]
        snap = cm.compute_concentration()
        bd = snap["signals"]["breadth_divergence"]
        assert bd["level"] == cm.LEVEL_NORMAL

    @patch("concentration_monitor.fetch_market_indices")
    def test_unavailable_on_fetch_failure(self, mock_indices):
        mock_indices.side_effect = RuntimeError("cookie expired")
        snap = cm.compute_concentration()
        assert snap["signals"]["breadth_divergence"]["level"] == cm.LEVEL_UNAVAILABLE
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest test_concentration_monitor.py::TestBreadthDivergence -v
```
Expected: FAIL

- [ ] **Step 3: 实现宽度背离信号**

```python
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
```

更新 `compute_concentration` 主函数，添加 breadth 采集：

```python
    try:
        boards = fetch_boards(board_type="industry")
    except Exception:
        boards = []

    try:
        indices = fetch_market_indices()
    except Exception:
        indices = []

    flow_signal = _compute_flow_signal(boards, t["flow_top3_pct"])
    turnover_signal = _compute_turnover_signal(boards, t["turnover_top3_pct"])
    breadth_signal = _compute_breadth_signal(indices, t["breadth_ratio"])
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest test_concentration_monitor.py::TestBreadthDivergence -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add concentration_monitor.py test_concentration_monitor.py
git commit -m "feat(concentration): add breadth divergence signal"
```

### Task 1.5: 综合等级判定 + summary

- [ ] **Step 1: 写失败测试**

```python
class TestAggregateLevel:
    def test_all_normal(self):
        signals = [
            {"level": "normal"}, {"level": "normal"}, {"level": "normal"},
        ]
        assert cm._aggregate_level(signals) == cm.LEVEL_NORMAL

    def test_two_elevated(self):
        signals = [
            {"level": "elevated"}, {"level": "elevated"}, {"level": "normal"},
        ]
        assert cm._aggregate_level(signals) == cm.LEVEL_ELEVATED

    def test_two_danger(self):
        signals = [
            {"level": "danger"}, {"level": "danger"}, {"level": "normal"},
        ]
        assert cm._aggregate_level(signals) == cm.LEVEL_DANGER

    def test_one_elevated_downgraded(self):
        """仅 1 个 elevated + 2 个 normal → 降级为 normal。"""
        signals = [
            {"level": "elevated"}, {"level": "normal"}, {"level": "normal"},
        ]
        assert cm._aggregate_level(signals) == cm.LEVEL_NORMAL

    def test_unavailable_excluded(self):
        """unavailable 信号不参与综合判定。"""
        signals = [
            {"level": "danger"}, {"level": "unavailable"}, {"level": "unavailable"},
        ]
        # 仅 1 个有效信号，无法达到 ≥2 → normal
        assert cm._aggregate_level(signals) == cm.LEVEL_NORMAL

    def test_all_unavailable(self):
        signals = [
            {"level": "unavailable"}, {"level": "unavailable"},
        ]
        assert cm._aggregate_level(signals) == cm.LEVEL_UNAVAILABLE
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest test_concentration_monitor.py::TestAggregateLevel -v
```
Expected: FAIL

- [ ] **Step 3: 实现综合判定 + summary 生成**

```python
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
```

更新 `compute_concentration` 主函数的返回部分：

```python
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
```

- [ ] **Step 4: 运行全部测试**

```bash
pytest test_concentration_monitor.py -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add concentration_monitor.py test_concentration_monitor.py
git commit -m "feat(concentration): add aggregate level and summary generation"
```

---

## Task 2: 配置段新增

**Files:**
- Modify: `config.yaml`（在 `stocks:` 之后，`ths:` 之前插入）
- Modify: `config.example.yaml`（同上）

### Task 2.1: 添加配置

- [ ] **Step 1: 在 config.yaml 添加 concentration 段**

在 `stocks:` 块之后（约第 94 行，`ths:` 之前）插入：

```yaml
concentration:
  thresholds:
    flow_top3_pct:
      elevated: 0.50
      danger: 0.70
    turnover_top3_pct:
      elevated: 0.45
      danger: 0.60
    breadth_ratio:
      elevated: 1.0
      danger: 0.6
  intraday:
    check_interval_min: 30
    stop_at: "14:50"
```

- [ ] **Step 2: 在 config.example.yaml 同样位置插入相同配置**

- [ ] **Step 3: 提交**

```bash
git add config.yaml config.example.yaml
git commit -m "config: add concentration thresholds and intraday settings"
```

---

## Task 3: 报告集成（stock_extractor.py）

**Files:**
- Modify: `stock_extractor.py`（新增 `_append_concentration_gauge` 函数 + 在 `_append_trader_summary` 中调用）

**Interfaces:**
- Consumes: `concentration_monitor.compute_concentration()`
- Produces: `_append_concentration_gauge(parts, snapshot)` — 向 parts 列表追加集中度仪表盘 Markdown

### Task 3.1: 实现仪表盘渲染函数

- [ ] **Step 1: 写失败测试**

新建 `test_concentration_report.py`：

```python
"""集中度报告集成测试。"""
import pytest
from unittest.mock import patch, MagicMock
import concentration_monitor as cm


class TestConcentrationGauge:
    def _snapshot(self, level, flow_top3=None, turnover_val=0.3,
                  breadth_ratio=1.2, index_change=0.5):
        return {
            "level": level,
            "timestamp": "2026-07-29T14:30:00+08:00",
            "signals": {
                "flow_concentration": {
                    "value": 0.54,
                    "level": "elevated",
                    "top3": flow_top3 or [
                        {"name": "半导体", "share": 0.28},
                        {"name": "AI", "share": 0.16},
                        {"name": "机器人", "share": 0.10},
                    ],
                },
                "turnover_concentration": {
                    "value": turnover_val,
                    "level": "normal",
                    "top3": [],
                },
                "breadth_divergence": {
                    "value": breadth_ratio,
                    "level": "normal",
                    "index_change": index_change,
                    "advance_decline_ratio": breadth_ratio,
                },
            },
            "summary": "资金集中度偏高",
        }

    def test_normal_not_shown(self):
        """normal 等级不展示。"""
        from stock_extractor import _append_concentration_gauge
        parts = []
        _append_concentration_gauge(parts, self._snapshot("normal"))
        assert parts == []

    def test_unavailable_shown(self):
        """unavailable 展示数据缺失提示。"""
        from stock_extractor import _append_concentration_gauge
        parts = []
        _append_concentration_gauge(parts, self._snapshot("unavailable"))
        assert any("暂缺" in p for p in parts)

    def test_elevated_format(self):
        """elevated 展示黄色警告 + 数据行。"""
        from stock_extractor import _append_concentration_gauge
        parts = []
        _append_concentration_gauge(parts, self._snapshot("elevated"))
        text = "\n".join(parts)
        assert "偏高" in text
        assert "54%" in text
        assert "半导体" in text

    def test_danger_format_with_position_advice(self):
        """danger 展示红色警告 + 仓位建议。"""
        from stock_extractor import _append_concentration_gauge
        parts = []
        snap = self._snapshot("danger")
        snap["signals"]["breadth_divergence"]["level"] = "danger"
        snap["signals"]["breadth_divergence"]["advance_decline_ratio"] = 0.55
        _append_concentration_gauge(parts, snap)
        text = "\n".join(parts)
        assert "危险" in text
        assert "仓位" in text
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest test_concentration_report.py -v
```
Expected: FAIL（函数未定义）

- [ ] **Step 3: 实现 `_append_concentration_gauge`**

在 `stock_extractor.py` 中 `_append_trader_summary` 函数之后添加：

```python
def _append_concentration_gauge(parts: list[str], snapshot: Optional[dict]) -> None:
    """在交易员摘要中插入资金集中度仪表盘。

    - normal: 不展示（避免冗余）
    - elevated: 黄色警告 + 数据
    - danger: 红色警告 + 数据 + 仓位建议
    - unavailable: 灰色提示数据缺失
    """
    if not snapshot:
        return

    level = snapshot.get("level", "normal")
    if level == "normal":
        return

    if level == "unavailable":
        parts.append("⚠️ 集中度数据暂缺\n")
        return

    signals = snapshot.get("signals", {})
    flow = signals.get("flow_concentration", {})
    turnover = signals.get("turnover_concentration", {})
    breadth = signals.get("breadth_divergence", {})

    is_danger = level == "danger"
    icon = "🚨" if is_danger else "⚠️"
    label = "危险" if is_danger else "偏高"
    emoji = "🔴" if is_danger else "🟡"

    parts.append(f"{icon} 资金集中度：{emoji} {label}")

    # 资金集中行
    flow_level = flow.get("level", "unavailable")
    if flow_level not in ("unavailable", "normal"):
        pct = flow.get("value", 0) * 100
        top3_str = "、".join(
            f"{t['name']} {t['share']*100:.0f}%"
            for t in flow.get("top3", [])
        )
        parts.append(f"  • 资金集中：前3板块净流入占 {pct:.0f}%（{top3_str}）")

    # 成交集中行
    to_level = turnover.get("level", "unavailable")
    if to_level not in ("unavailable", "normal"):
        pct = turnover.get("value", 0) * 100
        parts.append(f"  • 成交集中：前3板块成交额占 {pct:.0f}%")

    # 宽度背离行
    bd_level = breadth.get("level", "unavailable")
    if bd_level not in ("unavailable", "normal"):
        idx_change = breadth.get("index_change", 0)
        ratio = breadth.get("advance_decline_ratio", 1)
        parts.append(
            f"  • 宽度背离：沪指涨 {idx_change*100:.1f}% 但上涨/下跌 = {ratio:.2f}（权重拉个股跌）"
        )

    # 操作建议
    if is_danger:
        parts.append("  → 资金高度集中，追高风险极大，建议降低仓位至 5 成以下")
    else:
        parts.append("  → 注意追高，控制仓位")
    parts.append("")
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest test_concentration_report.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add stock_extractor.py test_concentration_report.py
git commit -m "feat(report): add concentration gauge to trader summary"
```

### Task 3.2: 在 _append_trader_summary 中调用

- [ ] **Step 1: 修改 `_append_trader_summary` 签名和调用**

修改函数签名，新增 `concentration_snapshot` 参数：

```python
def _append_trader_summary(
    parts: list[str],
    enriched: list[dict],
    trend_scores: dict,
    market_filter: dict,
    style_exposure: dict = None,
    concentration_snapshot: Optional[dict] = None,
) -> None:
```

在函数体末尾（风格暴露段之前，约 line 3390 `parts.append("")` 之后）插入：

```python
    # 集中度仪表盘
    if concentration_snapshot:
        _append_concentration_gauge(parts, concentration_snapshot)
```

- [ ] **Step 2: 在 `_rebuild_report` 中获取数据并传入**

在 `_rebuild_report` 中（line 3663 附近），先获取 concentration snapshot，再传入：

```python
    # 资金集中度（交易员摘要区展示）
    concentration_snapshot = None
    try:
        from concentration_monitor import compute_concentration
        from pathlib import Path
        import yaml
        _config_path = Path(__file__).parent / "config.yaml"
        _conc_thresholds = None
        if _config_path.exists():
            with open(_config_path, "r") as _f:
                _cfg = yaml.safe_load(_f) or {}
            _conc_thresholds = _cfg.get("concentration", {}).get("thresholds")
        concentration_snapshot = compute_concentration(thresholds=_conc_thresholds)
    except Exception as exc:
        print(f"[集中度] 获取失败: {exc}", flush=True)
```

然后修改 line 3663 的调用，传入 `concentration_snapshot=concentration_snapshot`。

- [ ] **Step 3: 运行测试确认无回归**

```bash
pytest test_concentration_report.py -v
```
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add stock_extractor.py
git commit -m "feat(report): wire concentration gauge into rebuild_report"
```

---

## Task 4: 盘中实时预警集成（intraday_monitor.py）

**Files:**
- Modify: `intraday_monitor.py`（新增常量 + `_check_concentration` 函数 + 在循环中调用）

**Interfaces:**
- Consumes: `concentration_monitor.compute_concentration()`
- Produces: `_check_concentration(state) -> list[dict]` — 生成预警 alert dict，格式与 `_check_sector_rotation` 一致

### Task 4.1: 实现集中度预警检查函数

- [ ] **Step 1: 写失败测试**

新建 `test_concentration_intraday.py`：

```python
"""集中度盘中预警测试。"""
import pytest
from unittest.mock import patch
import concentration_monitor as cm


class TestConcentrationIntraday:
    def _snapshot(self, level):
        return {
            "level": level,
            "timestamp": "2026-07-29T14:30:00+08:00",
            "signals": {
                "flow_concentration": {
                    "value": 0.73,
                    "level": "danger",
                    "top3": [
                        {"name": "半导体", "share": 0.35},
                        {"name": "AI", "share": 0.22},
                    ],
                },
                "turnover_concentration": {"value": 0.58, "level": "elevated", "top3": []},
                "breadth_divergence": {"value": 0.55, "level": "danger",
                                       "index_change": 0.012, "advance_decline_ratio": 0.55},
            },
            "summary": "资金高度集中",
        }

    @patch("concentration_monitor.compute_concentration")
    def test_no_alert_on_normal(self, mock_compute):
        mock_compute.return_value = self._snapshot("normal")
        from intraday_monitor import _check_concentration
        alerts = _check_concentration({"concentration_last": {"level": "normal"}})
        assert alerts == []

    @patch("concentration_monitor.compute_concentration")
    def test_alert_on_upgrade(self, mock_compute):
        """等级从 normal 升到 danger → 推送预警。"""
        mock_compute.return_value = self._snapshot("danger")
        from intraday_monitor import _check_concentration
        alerts = _check_concentration({"concentration_last": {"level": "normal"}})
        assert len(alerts) == 1
        assert alerts[0]["type"] == "concentration"
        assert "半导体" in alerts[0]["msg"]

    @patch("concentration_monitor.compute_concentration")
    def test_no_repeat_same_level(self, mock_compute):
        """同等级不重复推送。"""
        mock_compute.return_value = self._snapshot("danger")
        from intraday_monitor import _check_concentration
        state = {"concentration_last": {"level": "danger"}}
        alerts = _check_concentration(state)
        assert alerts == []

    @patch("concentration_monitor.compute_concentration")
    def test_state_updated(self, mock_compute):
        """调用后 state 更新为当前等级。"""
        mock_compute.return_value = self._snapshot("elevated")
        from intraday_monitor import _check_concentration
        state = {"concentration_last": {"level": "normal"}}
        _check_concentration(state)
        assert state["concentration_last"]["level"] == "elevated"

    @patch("concentration_monitor.compute_concentration")
    def test_daily_dedup(self, mock_compute):
        """同等级当天已推送过 → 不重复推送。"""
        mock_compute.return_value = self._snapshot("danger")
        from intraday_monitor import _check_concentration
        state = {"concentration_last": {
            "level": "elevated",
            "last_push_level": "danger",
            "last_push_date": "2026-07-29",  # 今天
        }}
        alerts = _check_concentration(state)
        assert alerts == []
```

- [ ] **Step 2: 运行确认失败**

```bash
pytest test_concentration_intraday.py -v
```
Expected: FAIL

- [ ] **Step 3: 添加常量 + 实现 `_check_concentration`**

在 `intraday_monitor.py` 中 `SECTOR_ROTATION_COOLDOWN` 之后添加：

```python
# ── 资金集中度监控 ──
CONCENTRATION_STATE_KEY = "concentration_last"
```

添加函数（参考 `_check_sector_rotation` 模式）：

```python
def _check_concentration(state: dict) -> list[dict]:
    """检查资金集中度，边缘触发预警。

    仅当等级上升时推送（normal→elevated→danger），
    等级下降时推送解除消息，等级不变不推送。
    """
    from concentration_monitor import compute_concentration

    alerts = []
    try:
        snapshot = compute_concentration()
    except Exception as exc:
        print(f"[集中度] 获取失败: {exc}", flush=True)
        return alerts

    level = snapshot.get("level", "normal")
    last = state.get(CONCENTRATION_STATE_KEY, {})
    last_level = last.get("level", "normal")

    # 更新状态
    state[CONCENTRATION_STATE_KEY] = {
        "level": level,
        "timestamp": snapshot.get("timestamp"),
    }

    # 等级未变 → 不推送
    if level == last_level:
        return alerts

    current_order = _LEVEL_ORDER.get(level, 0)
    last_order = _LEVEL_ORDER.get(last_level, 0)

    # 等级上升时：同等级当天已推送过 → 不重复
    if current_order > last_order:
        today = _now_shanghai().strftime("%Y-%m-%d")
        if last.get("last_push_level") == level and last.get("last_push_date") == today:
            return alerts

    # 等级下降 → 推送解除
    if current_order < last_order:
        now = _now_shanghai()
        alerts.append({
            "code": "CONC",
            "name": "集中度",
            "type": "concentration_release",
            "level": "🟢 机会",
            "priority": 1,
            "msg": f"资金集中度回落至{level}，追高风险下降。",
            "ts": time.time(),
            "time": now.strftime("%H:%M:%S"),
        })
        return alerts

    # 等级上升 → 推送预警
    now = _now_shanghai()
    signals = snapshot.get("signals", {})
    flow = signals.get("flow_concentration", {})
    top3_names = "、".join(t["name"] for t in flow.get("top3", []))

    msg = f"前3板块净流入占 {flow.get('value', 0)*100:.0f}%（{top3_names}）"
    if level == "danger":
        msg += "。资金高度集中，注意追高风险"
    else:
        msg += "。资金集中度偏高，注意追高"

    alerts.append({
        "code": "CONC",
        "name": "集中度",
        "type": "concentration",
        "level": "🔴 严重" if level == "danger" else "🟡 注意",
        "priority": 0,
        "msg": msg,
        "ts": time.time(),
        "time": now.strftime("%H:%M:%S"),
    })

    # 记录今日已推送等级
    state[CONCENTRATION_STATE_KEY]["last_push_level"] = level
    state[CONCENTRATION_STATE_KEY]["last_push_date"] = now.strftime("%Y-%m-%d")
    return alerts
```

在文件顶部 import 区域添加：

```python
_LEVEL_ORDER = {"normal": 0, "elevated": 1, "danger": 2}
```

- [ ] **Step 4: 运行确认通过**

```bash
pytest test_concentration_intraday.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add intraday_monitor.py test_concentration_intraday.py
git commit -m "feat(intraday): add concentration alert check function"
```

### Task 4.2: 在 run_monitor 循环中调用

- [ ] **Step 1: 在 run_monitor 循环中添加集中度检查**

在 `run_monitor` 的 while 循环中，板块轮动检测之后（line 505 之后），添加：

```python
        # 集中度检测（基于 check_interval_min 折算轮次）
        _conc_interval_rounds = max(1, CONCENTRATION_CHECK_INTERVAL_MIN * 60 // poll_interval)
        if round_count % _conc_interval_rounds == 0:
            now_hm = now.hour * 100 + now.minute
            if now_hm < 1450:  # 14:50 后停止检查
                conc_alerts = _check_concentration(state)
                if conc_alerts:
                    alerts.extend(conc_alerts)
```

在模块常量区添加：

```python
CONCENTRATION_CHECK_INTERVAL_MIN = 30  # 集中度检查间隔（分钟）
```

- [ ] **Step 2: 运行全部测试确认无回归**

```bash
pytest test_concentration_monitor.py test_concentration_report.py test_concentration_intraday.py -v
```
Expected: 全部 PASS

- [ ] **Step 3: 提交**

```bash
git add intraday_monitor.py
git commit -m "feat(intraday): wire concentration check into monitor loop"
```

---

## 验收清单

完成后验证：

- [ ] `pytest test_concentration_monitor.py test_concentration_report.py test_concentration_intraday.py -v` 全部通过
- [ ] `python -c "from concentration_monitor import compute_concentration; print(compute_concentration())"` 不报错
- [ ] 报告中 danger 级别展示红色警告 + 仓位建议
- [ ] 报告中 normal 级别不展示集中度段落
- [ ] 盘中预警等级上升时推送，同等级不重复
- [ ] config.yaml / config.example.yaml 新增 concentration 段
- [ ] 所有文件 < 800 行，函数 < 50 行
      danger: 0.70
    turnover