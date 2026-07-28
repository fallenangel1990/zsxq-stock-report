# 市场资金集中度指标 — 设计文档

> 状态：已确认 ← 2026-07-29 用户审核通过
> 作者：Claude + chenlin
> 关联需求：增加交易集中度指标，及时提示风险

## 1. 背景与目标

当前系统已有**板块拥挤度惩罚**（同板块推荐过多时扣分）和**组合集中度上限**（单板块仓位 ≤25%），但缺少一个统一的**市场资金集中度指标**——衡量全市场资金是否过度涌入少数板块/个股，并在集中度过高时及时提示追高风险。

**目标：**
- 新增一个综合集中度指标，覆盖三个维度：板块资金净流入占比、板块成交额占比、市场宽度背离
- 在交易员摘要区域展示，超阈值时标红警告
- 在盘中实时监控，触发时通过现有预警通道推送

## 2. 架构

```
concentration_monitor.py          ← 新建：集中度计算核心（单一数据源）
    ├── compute_concentration()   ← 主函数，返回 ConcentrationSnapshot
    ├── _fetch_sector_flow()      ← 板块资金净流入（东方财富 push2）
    ├── _fetch_sector_turnover()  ← 板块成交额（东方财富 push2）
    ├── _fetch_breadth()          ← 市场宽度（同花顺 indexflash）
    ├── _judge_level()            ← 单信号等级判定
    └── _aggregate_level()        ← 综合等级判定

集成点：
    stock_extractor.py  ← _append_trader_summary() → 报告标红展示
    intraday_monitor.py ← 每 30 分钟检查 → 边缘触发推送预警
```

模块与 `market_regime.py` 平级，但职责不同：
- `market_regime`：判断"市场是什么状态"（牛/熊/震荡/波动率）
- `concentration_monitor`：判断"资金是否太集中"（追高风险）

## 3. 数据结构

### ConcentrationSnapshot（compute_concentration 返回值）

```python
{
    "level": "normal" | "elevated" | "danger",
    "timestamp": "2026-07-29T14:30:00+08:00",
    "signals": {
        "flow_concentration": {
            "value": 0.73,              # 前3板块净流入占比
            "level": "normal" | "elevated" | "danger" | "unavailable",
            "top3": [
                {"name": "半导体", "share": 0.35, "net_inflow_yi": 120.5},
                {"name": "AI", "share": 0.22, "net_inflow_yi": 80.3},
                {"name": "机器人", "share": 0.16, "net_inflow_yi": 58.9}
            ]
        },
        "turnover_concentration": {
            "value": 0.58,
            "level": "normal" | "elevated" | "danger" | "unavailable",
            "top3": [{"name": "半导体", "share": 0.25}, ...]
        },
        "breadth_divergence": {
            "value": 0.55,              # 上涨家数/下跌家数
            "level": "normal" | "elevated" | "danger" | "unavailable",
            "index_change": 0.012,      # 指数涨跌幅
            "advance_decline_ratio": 0.55
        }
    },
    "summary": "资金高度集中于半导体/AI/机器人板块"  # 一句话总结
}
```

## 4. 计算逻辑

### 4.1 信号 1：板块资金净流入集中度

1. 拉取全行业板块主力资金净流入数据
2. 按净流入降序排列，取前 3 板块
3. `value = top3 净流入之和 / 全市场净流入之和`
4. 阈值（固定，可在 config 调整）：
   - `value > 0.50` → elevated
   - `value > 0.70` → danger

### 4.2 信号 2：板块成交额集中度

1. 拉取全行业板块成交额
2. 按成交额降序排列，取前 3 板块
3. `value = top3 成交额之和 / 全市场成交额之和`
4. 阈值：
   - `value > 0.45` → elevated
   - `value > 0.60` → danger

### 4.3 信号 3：市场宽度背离

1. 获取全市场上涨家数、下跌家数
2. `advance_decline_ratio = 上涨家数 / 下跌家数`
3. 获取**上证指数（沪指）**当日涨跌幅（数据缺失时用沪深300 兜底）
4. 判定条件：**指数上涨**且 `advance_decline_ratio < 1` → 背离（权重拉、个股跌）
   - `ratio < 1.0` → elevated
   - `ratio < 0.6` → danger
5. 指数下跌时不触发此信号（下跌集中 ≠ 追高风险）

### 4.4 综合等级判定

| 条件 | 等级 |
|------|------|
| 所有有效信号 normal | 🟢 normal |
| ≥2 个有效信号 ≥ elevated | 🟡 elevated |
| ≥2 个有效信号 ≥ danger | 🔴 danger |
| 其余组合 | 取最高信号等级降级（如 1 个 elevated + 2 个 normal = normal） |

`unavailable` 信号不参与计数。

### 4.5 数据源与降级

| 数据 | 主数据源 | 兜底 |
|------|---------|------|
| 板块资金净流入 | 东方财富 push2 行业板块 `f62` 字段 | 腾讯行情板块接口 |
| 板块成交额 | 东方财富 push2 行业板块 `f20` 字段 | 腾讯行情板块接口 |
| 市场宽度（涨跌家数） | 同花顺 indexflash | 东方财富 push2 全A统计 |

- 主数据源失败时尝试兜底
- 全部失败 → 该信号标记 `level: "unavailable"`，报告中注明"数据缺失"
- 所有信号都 unavailable → `level: "unavailable"`，报告展示"⚠️ 集中度数据暂缺"

## 5. 报告集成（stock_extractor.py）

### 5.1 位置

在 `_append_trader_summary()` 中，**紧跟市场状态之后**插入集中度仪表盘。

### 5.2 展示规则

| 等级 | 展示 |
|------|------|
| normal | **不展示**（避免冗余） |
| elevated | 黄色警告 + 详细数据 |
| danger | 红色警告 + 详细数据 + 仓位建议 |
| unavailable | 灰色提示"⚠️ 集中度数据暂缺" |

### 5.3 展示格式

**elevated 示例：**
```
⚠️ 资金集中度：🟡 偏高
  • 资金集中：前3板块净流入占 54%（半导体 28%、AI 16%、机器人 10%）
  • 成交集中：前3板块成交额占 47%
  • 宽度背离：沪指涨 0.8% 但上涨/下跌 = 0.91（权重拉个股跌）
  → 注意追高，控制仓位
```

**danger 示例：**
```
🚨 资金集中度：🔴 危险
  • 资金集中：前3板块净流入占 73%（半导体 35%、AI 22%、机器人 16%）
  • 成交集中：前3板块成交额占 58%
  • 宽度背离：沪指涨 1.2% 但上涨/下跌 = 0.55
  → 资金高度集中，追高风险极大，建议降低仓位至 5 成以下
```

## 6. 盘中实时预警集成（intraday_monitor.py）

### 6.1 检查频率

每 **30 分钟**检查一次（与价格预警同频），在现有监控循环中追加。

### 6.2 触发逻辑（边缘触发）

- 读取 `data/state/concentration_last.json` 获取上次等级
- 当前等级 > 上次等级 → 推送预警（升级）
- 当前等级 < 上次等级 → 推送解除消息（降级）
- 等级不变 → 不推送
- 单个交易日同等级最多推送 **1 次**

### 6.3 推送通道

复用 `intraday_monitor.py` 现有推送通道（邮件/Webhook，与价格预警一致）。

### 6.4 预警文案

```
🚨 集中度预警 [14:30]
等级：🔴 危险（上次：🟡 偏高）
前3板块净流入占 73%（半导体 35%、AI 22%）
沪指涨 1.2% 但上涨/下跌 = 0.55
→ 资金高度集中，注意追高风险
```

### 6.5 退出时间

**14:50 后不再检查**（接近收盘，预警无意义），与现有 intraday_monitor 行为一致。

## 7. 配置

config.yaml 新增段：

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

`config.example.yaml` 同步新增。

## 8. 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `concentration_monitor.py` | **新建** | 核心计算模块 |
| `stock_extractor.py` | 修改 | `_append_trader_summary()` 集成 |
| `intraday_monitor.py` | 修改 | 新增集中度监控循环 |
| `config.yaml` | 修改 | 新增配置段 |
| `config.example.yaml` | 修改 | 新增配置段 |
| `test_concentration_monitor.py` | **新建** | 单元测试 |

## 9. 测试计划

### 9.1 单元测试（test_concentration_monitor.py）

| 测试用例 | 验证内容 |
|---------|---------|
| test_flow_concentration_normal | 资金分散 → normal |
| test_flow_concentration_elevated | top3 = 55% → elevated |
| test_flow_concentration_danger | top3 = 75% → danger |
| test_turnover_concentration | 成交额集中度计算正确 |
| test_breadth_divergence_normal | 指数涨 + 涨跌比 > 1 → normal |
| test_breadth_divergence_danger | 指数涨 + 涨跌比 < 0.6 → danger |
| test_breadth_no_trigger_on_down | 指数跌 → 不触发背离 |
| test_aggregate_elevated | ≥2 信号 elevated → elevated |
| test_aggregate_danger | ≥2 信号 danger → danger |
| test_unavailable_excluded | unavailable 信号不参与综合判定 |
| test_data_fallback | 主源失败 → 尝试兜底 |
| test_all_unavailable | 全失败 → level = unavailable |

### 9.2 集成测试

| 测试用例 | 验证内容 |
|---------|---------|
| report_shows_elevated | mock elevated → 报告生成黄色警告文本 |
| report_shows_danger | mock danger → 报告生成红色警告 + 仓位建议 |
| report_hides_normal | mock normal → 报告无集中度段落 |
| intraday_edge_trigger | 连续两次 danger → 只推送一次 |
| intraday_upgrade_trigger | normal → danger → 推送 |
| intraday_downgrade_notice | danger → elevated → 推送解除 |

## 10. 风险与注意事项

- **东方财富 push2 在 CI 中可能 502**（已知问题，见 cerebrum），必须有降级策略
- **盘中预警防刷屏**：边缘触发 + 同等级限推 1 次
- **数据缺失不静默**：unavailable 必须在报告中有提示
- **宽度背离只在指数上涨时判定**：下跌集中不是追高风险，不应误报
- **同花顺 cookies 可能过期**：与现有 intraday_monitor 共享相同的 cookie 管理逻辑
