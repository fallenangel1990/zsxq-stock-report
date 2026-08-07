# 量化交易闭环 — 设计文档

> 状态：已确认 ← 2026-08-07 用户审核通过
> 作者：Claude + chenlin
> 关联需求：在现有基础上加上量化交易，打通现有闭环

## 1. 背景与目标

项目已有较完整的量化交易基础设施：`auto_trader.py`（RiskController 风控 + BrokerClient 券商对接 + SignalGenerator 信号 + AutoTrader 执行）、`paper_trader.py`（模拟交易）、`backtester.py`（回测）、`auction_*`（竞价）。同时我们刚优化了股票数据链路（精选 Top 清单、流动性门槛、换手率、长期价值）。

但各能力**互相独立**，没有形成"评分 → 精选 → 信号 → 执行 → 回测"的连贯闭环。

**目标：**
- 新增 `quant` 一键闭环命令，串联现有能力成单一流程
- 交易信号消费精选增强数据（selectivity/liquidity/turnover/long_term_value）
- 保留 config 的 semi/full 双模式（默认 semi 人工确认）
- 产出统一格式的"量化交易决策报告"

## 2. 范围（用户已确认的三项决策）

- **打通现有闭环**：用单一 `quant` 命令编排现有模块，不重写信号/风控/券商逻辑
- **信号来源**：精选增强数据（含 selectivity/liquidity/turnover）
- **交易模式**：双模式 semi 优先（semi 人工确认，full 自动带风控）

## 3. 架构

```
main.py: quant 命令（编排器）
  ├── extract_stock_opportunities()     → 生成含精选分的 enriched 数据
  ├── 精选 Top 清单                      → selectivity_score 取前 N + 流动性门槛
  ├── auto_trader.SignalGenerator       → 生成买卖信号（消费精选字段）
  ├── auto_trader.AutoTrader.run()      → semi/full 执行（semi 人工确认）
  ├── backtester.run_backtest()         → 回测复盘
  └── 输出 data/quant/quant_report_*.md → 决策报告

auto_trader.py: SignalGenerator/RiskController 增强
  ├── RiskController 新增配置：selectivity_min / liquidity_gate
  ├── generate_signals 买入分支：过 _liquidity_eligible，reason 附长期价值/流动性
  └── 买入排序改为 (selectivity_score, buy_score)

paper_trader.py: auto_trade_from_recommendations 增强
  └── 只模拟交易精选合格 + 过流动性门槛的票
```

## 4. 数据流与展示

### 4.1 `quant` 一键闭环命令（Part 1）

```
python3 main.py quant [--mode semi|full] [--no-execute]

流程：
1. extract_stock_opportunities(posts) → enriched 数据（含精选分/流动性/换手率）
2. 精选 Top 清单：selectivity_score 取前 N + _liquidity_eligible 门槛
3. SignalGenerator(risk).generate_signals(精选数据, positions)
4. 执行：semi=人工确认 / full=自动执行（AutoTrader.run）；--no-execute 只出信号
5. run_backtest() + format_backtest_report() → 回测复盘
6. 输出 data/quant/quant_report_*.md
```

- `quant` 是编排器，调用现有函数，不改它们内部逻辑
- `--no-execute`：只生成信号和回测，不下单（安全预览）
- semi：打印信号 + 每笔需人工确认（`--yes` 或交互）；full：自动执行带风控兜底

### 4.2 信号接入精选增强数据（Part 2）

`SignalGenerator.generate_signals` 增强：

```
买入分支新增：
  if liquidity_gate and not _liquidity_eligible(stock): skip("流动性不足")
  买入排序：(selectivity_score, buy_score) 降序（精选分优先，同分按买点）
  信号 reason 附上：长期价值 X.X / 流动性 Y.Y

RiskController 新增配置：
  selectivity_min: 3.0  （精选分阈值，可选）
  liquidity_gate: True  （流动性门槛开关）
```

- `_liquidity_eligible` / `_selectivity_score` 从 stock_extractor import 复用
- 改动集中在 SignalGenerator/RiskController，不影响 BrokerClient/连接
- 精选字段缺失时优雅降级（老数据无 selectivity → 回退 buy_score 排序）

### 4.3 模拟交易接入 + 决策报告（Part 3）

**paper_trader 增强**：`auto_trade_from_recommendations` 只模拟精选合格 + 流动性门槛的票，输出格式与 auto_trader 信号一致（buy/sell/hold/skip + 精选分/流动性标注）。

**决策报告** `data/quant/quant_report_*.md`：
```
# 🤖 量化交易决策报告 2026-08-07 09:30
## 📊 市场概览（指数/涨跌/集中度摘要）
## ⭐ 精选候选（前 N 只）——复用精选 Top 清单
## 📈 交易信号（🟢买入/🔴卖出/⏸持有/⏭跳过）
## 🛡️ 风控状态（当日交易数/熔断/回撤/黑名单）
## 📊 回测复盘（run_backtest 摘要：Alpha/Beta/Sharpe）
```

- 报告由 `quant` 编排生成，复用 `format_backtest_report`、信号统计、精选清单
- semi 含"待确认交易"清单；full 含"已执行"记录
- 保存到 `data/quant/`，可被 dashboard 引用

## 5. 错误处理

- 券商连接失败：`quant` 报告 error 并退出，不产生空报告
- 回撤熔断：`AutoTrader.run` 返回 circuit_breaker，报告标注并停止交易
- 无推荐数据：报错"请先运行 stocks"
- 精选字段缺失（老数据）：排序降级回退 buy_score
- `--no-execute` 不下单，仅预览（安全模式）

## 6. 测试

- 单元测试：SignalGenerator 增强（流动性门槛过滤、精选分排序、reason 标注）
- 单元测试：RiskController 新配置项（selectivity_min/liquidity_gate 默认值）
- 单元测试：paper_trader 精选过滤
- 集成测试：`quant --no-execute` 端到端（mock 券商/行情，验证报告生成）
- 回归：现有 trade/paper-trade/backtest 命令不破坏
