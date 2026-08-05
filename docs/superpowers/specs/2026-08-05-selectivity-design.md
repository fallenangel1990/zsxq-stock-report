# 精选 Top 清单（长期投资价值优先）— 设计文档

> 状态：已确认 ← 2026-08-05 用户审核通过
> 作者：Claude + chenlin
> 关联需求：一次分析 300 篇帖子个股过多，设计精选方案提取最有投资价值的股票；优先推荐具备长期投资价值的个股

## 1. 背景与目标

一次分析 213 篇相关帖子产出 **77 只候选**，分散在 ~16 个板块，报告全量展示过多。且评分基线偏低：实测最高分仅 2.1，全部 77 只落在"剔除/暂不买入"层，缺乏头部区分度。

**目标：**
- 报告顶部新增"⭐ 精选 Top 清单"，动态选出最有投资价值的 8-15 只，作为快速决策入口
- 优先推荐**具备长期投资价值**的个股（护城河 + 基本面 + 长期景气）
- 同步调整评分基线，让候选池能分出高分段（否则精选无从谈起）

## 2. 范围（用户已确认的三项决策）

- **输出形态**：精选 Top 清单（8-15 只，动态 N）
- **排序依据**：分数 + 逻辑强度（+ 长期投资价值维度）
- **评分基线**：同步调整，让候选池分出高分段

## 3. 架构

改动集中在 `stock_extractor.py`：

```
_enrich_and_score() 内：
  base_consensus / recency_weight / market_penalty / _calibrate 基线调整
  → 新增 stock["logic_strength"]（复用 _long_term_trend_score）
  → 新增 stock["long_term_value"]（_long_term_value_score）
  → 新增 stock["selectivity_score"]（_selectivity_score）

_rebuild_report() 内：
  → 新增 "⭐ 精选 Top 清单" 章节（按 selectivity_score 取前 N）
  → 保留 "📋 按板块分类" 全量章节
```

新增函数：

```
def _long_term_value_score(stock: dict) -> float:
    """长期投资价值（0-10）：护城河40% + 基本面30% + 长期景气30%。"""

def _selectivity_score(stock: dict) -> float:
    """精选综合分：score×40% + logic_strength×30% + long_term_value×20% + buy_score×10%。"""
```

复用已有信号（DRY）：
- `_long_term_trend_score`（涨价/供需/景气/扩产/国产替代/目标价，0-10）→ 逻辑强度
- `moat_score`（护城河 1-5 → `_parse_moat_score` 映射 0-10，AI 提取）
- `fundamentals_score`（PE/PB/市值基本面，已算）
- `buy_score`（买点质量，已算）

## 4. 数据流与展示

### 4.1 评分基线调整（Part 1）

| 位置 | 现状 | 调整 |
|------|------|------|
| `base_consensus` 1 作者 | 2.0 | → 3.5 |
| `base_consensus` post_count>=2 | 3.0 | → 4.0 |
| `recency_weight` 应用 | 裸乘 | → `0.85 + 0.15 * recency_weight` |
| `buy_score` market_penalty | 全额扣 | → `min(market_penalty, 1.0)` |
| `_calibrate_recommendation_score` 下限 | `max(1.0,...)` | → `max(1.5,...)` |

目标：候选池从"全挤在 1-2 分"变为"能分出 3-5 分中坚 + 少数 5+ 头部"。

### 4.2 长期价值 + 逻辑强度 + 精选分（Part 2 修订）

```python
def _long_term_value_score(stock: dict) -> float:
    moat = stock.get("moat_score", 5.0)             # 0-10
    fundamentals = stock.get("fundamentals_score", 5.0)  # 0-10
    trend = stock.get("long_term_trend") or _long_term_trend_score(
        stock.get("logic", ""), stock.get("target_str", ""), stock.get("risk_str", "")
    )                                                # 0-10
    return round(moat * 0.4 + fundamentals * 0.3 + trend * 0.3, 2)

def _selectivity_score(stock: dict) -> float:
    score = stock.get("score", 0)
    logic = stock.get("logic_strength") or _long_term_trend_score(
        stock.get("logic", ""), stock.get("target_str", ""), stock.get("risk_str", "")
    )
    ltv = stock.get("long_term_value") or _long_term_value_score(stock)
    buy = stock.get("buy_score", 0)
    return round(score * 0.4 + logic * 0.3 + ltv * 0.2 + buy * 0.1, 2)
```

每个 stock 在 `_enrich_and_score` 内落盘：`stock["logic_strength"]`、`stock["long_term_value"]`、`stock["selectivity_score"]`，供报告展示与下游（ths_sync/paper_trader）使用。

### 4.3 精选 Top 清单章节（Part 3）

`_rebuild_report` 中"📋 按板块分类"之前新增：

```
## ⭐ 精选 Top 清单（最有长期投资价值）

> 精选依据：推荐指数 40% + 逻辑强度 30% + 长期价值 20% + 买点质量 10%
> 精选 N 只 / 全部候选 M 只

| 排名 | 股票名称 | 板块 | 精选分 | 推荐指数 | 逻辑强度 | 长期价值 | 护城河 | 核心逻辑(截断) | 目标参考 | 风险点 |
|------|----------|------|--------|----------|----------|----------|--------|----------------|----------|--------|
```

- `selectivity_score` 降序取前 N
- 护城河列显示 AI 提取的护城河类型（品牌定价权/技术壁垒等），`moat_score>=8` 的票打 `🏰` 标记
- 动态 N：`N = max(8, min(15, round(candidate_count * 0.15)))`
  - 候选 ≥100 → 15 只；50-100 → 8-15 只；<50 → 保底 8 只
- "📋 按板块分类"全量章节保留，作为完整视图
- 精选分不替代现有 `score`/`buy_score`，现有阈值/回测/分层逻辑不受影响

## 5. 错误处理

- 逻辑强度/长期价值信号缺失（logic/target 为空）：`_long_term_trend_score` 返回基础分 3.0，`moat_score` 缺省 5.0，不会崩溃
- 候选池过小（<8 只）：动态 N 保底 8 只会超出候选数，取 `min(N, len(candidates))`
- 无候选：精选章节显示"本次无候选个股"，与按板块分类的空态一致

## 6. 测试

- 单元测试 `_long_term_value_score`：护城河/基本面/景气加权正确
- 单元测试 `_selectivity_score`：权重正确、缺省信号兜底
- 回归测试：`_rebuild_report` 产出含"⭐ 精选 Top 清单"，且按板块分类章节仍存在
- 评分基线调整回归：现有 `TestEnrichAndScore` 类测试不破坏（共识/校准变化验证）
- 端到端：真实数据 `main.py stocks` 验证 77 只候选能分出 Top N
