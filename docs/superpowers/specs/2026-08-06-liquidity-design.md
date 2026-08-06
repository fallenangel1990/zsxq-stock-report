# 精选优先推荐流动性好的个股 — 设计文档

> 状态：已确认 ← 2026-08-06 用户审核通过
> 作者：Claude + chenlin
> 关联需求：优先推荐股票流动性好的个股

## 1. 背景与目标

上一功能（精选 Top 清单）按长期投资价值选出 8-15 只个股，但未考虑流动性——可能推荐出市值极小、难成交的票。用户要求**优先推荐流动性好的个股**。

**目标：**
- 流动性作为精选排序的新维度（占精选分 10%）
- 流动性作为精选入场门槛（市值≥50亿，比全量章节更严）
- 复用现有 enriched 字段（市值 + 量比），不引入新数据源

## 2. 范围（用户已确认的三项决策）

- **作用方式**：门槛 + 维度双重（太差的不进精选，越好排序越靠前）
- **信号来源**：用现有字段（`market_cap_yi` + `technical.volume_ratio`），不动 price_fetcher
- **门槛标准**：精选更严（市值≥50亿），全量板块章节保持现有宽松阈值（市值≥20亿）

## 3. 架构

改动集中在 `stock_extractor.py`：

```
新增 _liquidity_score(stock) -> float      # 0-10：市值分×0.7 + 量比分×0.3
新增 _liquidity_eligible(stock) -> bool     # 市值≥50亿；无市值放行

重构 _selectivity_score(stock) -> float     # score×0.35 + logic×0.25 + ltv×0.2 + buy×0.1 + liquidity×0.1
重构 _rebuild_report 精选章节              # 先 _liquidity_eligible 过滤，再按 _selectivity_score 取前 N
```

## 4. 数据流与展示

### 4.1 流动性评分函数（Part 1）

```python
def _liquidity_score(stock: dict) -> float:
    """流动性评分（0-10）：市值分×70% + 量比活跃度×30%。

    市值分：对数映射，无市值给中性 5.0
    量比分：放量（>=1.5）8 / 温和放量（>=1.2）7 / 中性（>=0.8）5 / 缩量（<0.8）3，无量比给中性 5.0
    """
    market_cap = stock.get("market_cap_yi")
    if market_cap is not None and market_cap > 0:
        cap_score = min(10.0, max(2.0, 5 * math.log10(market_cap) / math.log10(2000)))
    else:
        cap_score = 5.0  # 无市值 → 中性

    tech = stock.get("technical") or {}
    volume_ratio = tech.get("volume_ratio")
    if volume_ratio is None:
        vol_score = 5.0
    elif volume_ratio >= 1.5:
        vol_score = 8.0
    elif volume_ratio >= 1.2:
        vol_score = 7.0
    elif volume_ratio >= 0.8:
        vol_score = 5.0
    else:
        vol_score = 3.0

    return round(cap_score * 0.7 + vol_score * 0.3, 2)
```

### 4.2 精选分权重重构（Part 1）

```python
def _selectivity_score(stock: dict) -> float:
    """精选综合分：score×35% + logic×25% + ltv×20% + buy×10% + liquidity×10%。"""
    # ... 现有 score/logic/ltv/buy 计算不变 ...
    liq = stock.get("liquidity_score")
    if liq is None:
        liq = _liquidity_score(stock)
    return round(score * 0.35 + logic * 0.25 + ltv * 0.2 + buy * 0.1 + liq * 0.1, 2)
```

权重从 40/30/20/10 调整为 35/25/20/10/10——流动性占 10%，从 score 和 logic 各让出 5%，ltv/buy 不变。理由：流动性是"可执行性"信号，权重不宜压过投资价值判断。

### 4.3 精选门槛（Part 2）

```python
def _liquidity_eligible(stock: dict, min_cap_yi: float = 50.0) -> bool:
    """精选流动性门槛：市值>=50亿（中大盘）；无市值数据时放行（不误杀）。"""
    cap = stock.get("market_cap_yi")
    if cap is None:
        return True
    return cap >= min_cap_yi
```

`_rebuild_report` 精选章节（原 3772-3780）改为：
- 先 `scored_candidates = [s for s in passed if _liquidity_eligible(s)]`
- 再按 `selectivity_score` 排序取前 N

门槛只作用于精选；"📋 按板块分类"全量章节保持 `_apply_liquidity_filter`（市值≥20亿）。

### 4.4 展示 + 落盘（Part 3）

**精选章节头部文案：**
```
> 精选依据：推荐指数 35% + 逻辑强度 25% + 长期价值 20% + 买点质量 10% + 流动性 10%（市值≥50亿门槛）
```

**表格新增"流动性"列**：显示 `liquidity_score`，`>=7` 打 `💧` 标记；无市值放行票显示 `-`。

**落盘 enriched JSON**：每个 stock 存 `liquidity_score`，`selectivity_score` 用新权重重算。

## 5. 错误处理

- 市值/量比缺失 → 流动性中性分（5.0），不误杀
- 精选门槛过滤后候选 <8 只 → 动态 N 取 `min(N, len)`，可能不足 8，属预期
- 全部被门槛挡 → top_picks 空，显示"本次无候选个股"
- 无市值票放行（43/76 无市值，硬剔除会砍掉近半候选；行情缺失≠流动性差）

## 6. 测试

- 单元测试 `_liquidity_score`：市值/量比加权、缺失信号中性兜底、clamp 边界（25亿→~4.6、2700亿→~8.3）
- 单元测试 `_liquidity_eligible`：市值≥50 放行、<50 挡、无市值放行
- 更新 `test_selectivity_score_weights` 为新权重（35/25/20/10/10）
- 回归：精选章节测试（门槛过滤 + 流动性列 + 💧标记）
- 端到端：真实数据 `main.py stocks` 验证精选 Top 均为流动性合格票
