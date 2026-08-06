# 精选优先推荐流动性好的个股 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精选 Top 清单优先推荐流动性好的个股：流动性作为精选分新维度（10%）+ 精选专属门槛（市值≥50亿）。

**Architecture:** 改动集中在 `stock_extractor.py`。新增 `_liquidity_score()`（市值×0.7 + 量比×0.3）与 `_liquidity_eligible()`（市值≥50亿门槛）；重构 `_selectivity_score()` 权重为 35/25/20/10/10；`_rebuild_report` 精选章节先过门槛再排序。enriched 落盘 `liquidity_score`。

**Tech Stack:** Python 3.9+（CI 用 3.12，避免 PEP 604 `dict | None`，用 `Optional[...]`），pytest，PyYAML。`math` 已导入。

## Global Constraints

- `_liquidity_score(stock)` = 市值分×0.7 + 量比分×0.3，0-10
  - 市值分：`min(10, max(2, 5*log10(cap)/log10(2000)))`；cap 缺失/≤0 → 中性 5.0
  - 量比分：vol>=1.5→8 / >=1.2→7 / >=0.8→5 / <0.8→3；vol 缺失 → 中性 5.0
- `_liquidity_eligible(stock, min_cap_yi=50.0)`：cap≥50 → True；cap 缺失 → True；cap<50 → False
- `_selectivity_score` 新权重：score×0.35 + logic×0.25 + ltv×0.2 + buy×0.1 + liquidity×0.1
- 精选门槛只作用于"⭐ 精选 Top 清单"；"📋 按板块分类"保持 `_apply_liquidity_filter`（市值≥20亿）
- enriched 落盘 `liquidity_score`
- 更新既有 `test_selectivity_score_weights` 为新权重
- 类型注解用 `Optional[...]`，不用 `|`
- 变更后必须 `python3 -m pytest tests/ -q` 全绿

---

### Task 1: 新增 `_liquidity_score` 与 `_liquidity_eligible`

**Files:**
- Modify: `stock_extractor.py`（在 `_selectivity_score` 之后新增，约 2118 行处）
- Test: `tests/test_stock_extractor.py`

**Interfaces:**
- Consumes: 无（纯函数，用 `math.log10`）
- Produces:
  - `_liquidity_score(stock: dict) -> float`（0-10）
  - `_liquidity_eligible(stock: dict, min_cap_yi: float = 50.0) -> bool`

- [ ] **Step 1: 写失败测试**

```python
class TestLiquidityScore:
    """Tests for liquidity scoring and eligibility."""

    def test_liquidity_score_weighted(self):
        from stock_extractor import _liquidity_score
        # 市值 250亿 → 5*log10(250)/log10(2000) ≈ 5*2.398/3.301 ≈ 3.63 → 3.63
        # 量比 1.5 → 8.0
        # score = 3.63*0.7 + 8*0.3 = 2.54 + 2.4 = 4.94
        stock = {"market_cap_yi": 250.0, "technical": {"volume_ratio": 1.5}}
        s = _liquidity_score(stock)
        assert 4.0 <= s <= 6.0, f"expected ~4.94, got {s}"

    def test_liquidity_score_missing_signals_neutral(self):
        from stock_extractor import _liquidity_score
        s = _liquidity_score({})
        assert s == 5.0  # 无市值+无量比 → 全中性

    def test_liquidity_score_cap_clamp(self):
        from stock_extractor import _liquidity_score
        # 超大市值 clamp 到 10
        big = _liquidity_score({"market_cap_yi": 1e6, "technical": {"volume_ratio": 1.5}})
        assert big <= 10.0
        # 小市值 clamp 到 2
        small = _liquidity_score({"market_cap_yi": 1.0, "technical": {"volume_ratio": 1.5}})
        assert small >= 2.0

    def test_liquidity_eligible_threshold(self):
        from stock_extractor import _liquidity_eligible
        assert _liquidity_eligible({"market_cap_yi": 100.0}) is True   # ≥50 放行
        assert _liquidity_eligible({"market_cap_yi": 49.0}) is False    # <50 挡
        assert _liquidity_eligible({"market_cap_yi": 50.0}) is True     # ==50 放行
        assert _liquidity_eligible({"market_cap_yi": None}) is True     # 无市值放行
        assert _liquidity_eligible({}) is True                          # 无市值放行
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestLiquidityScore -v`
Expected: FAIL（`ImportError: cannot import name '_liquidity_score'`）

- [ ] **Step 3: 实现两个函数**

在 `_selectivity_score`（2117 行 `return round(score * 0.4 + ...)` 之后）新增：

```python
def _liquidity_score(stock: dict) -> float:
    """流动性评分（0-10）：市值分×70% + 量比活跃度×30%。

    市值分：对数映射（越大越优），无市值给中性 5.0。
    量比分：放量(>=1.5)8 / 温和放量(>=1.2)7 / 中性(>=0.8)5 / 缩量(<0.8)3，无量比给中性 5.0。
    """
    market_cap = stock.get("market_cap_yi")
    if market_cap is not None and market_cap > 0:
        cap_score = min(10.0, max(2.0, 5 * math.log10(market_cap) / math.log10(2000)))
    else:
        cap_score = 5.0

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


def _liquidity_eligible(stock: dict, min_cap_yi: float = 50.0) -> bool:
    """精选流动性门槛：市值>=50亿（中大盘）；无市值数据时放行（不误杀）。"""
    cap = stock.get("market_cap_yi")
    if cap is None:
        return True
    return cap >= min_cap_yi
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestLiquidityScore -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): 新增 _liquidity_score 与 _liquidity_eligible"
```

---

### Task 2: 重构 `_selectivity_score` 权重 + 更新既有测试

**Files:**
- Modify: `stock_extractor.py:2105-2117`（`_selectivity_score`）
- Modify: `tests/test_stock_extractor.py:581-593`（`test_selectivity_score_weights`）

**Interfaces:**
- Consumes: `_liquidity_score`（Task 1）
- Produces: `_selectivity_score` 用新权重 `score×0.35 + logic×0.25 + ltv×0.2 + buy×0.1 + liquidity×0.1`

- [ ] **Step 1: 更新既有测试（新权重）**

```python
    def test_selectivity_score_weights(self):
        from stock_extractor import _selectivity_score
        stock = {
            "score": 4.0,
            "logic_strength": 8.0,
            "long_term_value": 7.0,
            "buy_score": 6.0,
            "liquidity_score": 6.0,
        }
        s = _selectivity_score(stock)
        expected = round(4.0 * 0.35 + 8.0 * 0.25 + 7.0 * 0.2 + 6.0 * 0.1 + 6.0 * 0.1, 2)
        assert s == expected
        # 逻辑强度权重高 → 高分逻辑推高精选分
        assert s > stock["score"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k test_selectivity_score_weights -v`
Expected: FAIL（当前权重 40/30/20/10，期望值不符）

- [ ] **Step 3: 重构 `_selectivity_score`**

```python
def _selectivity_score(stock: dict) -> float:
    """精选综合分：score×35% + logic×25% + ltv×20% + buy×10% + liquidity×10%。"""
    score = stock.get("score", 0)
    logic = stock.get("logic_strength")
    if logic is None:
        logic = _long_term_trend_score(
            stock.get("logic", ""), stock.get("target_str", ""), stock.get("risk_str", "")
        )
    ltv = stock.get("long_term_value")
    if ltv is None:
        ltv = _long_term_value_score(stock)
    buy = stock.get("buy_score", 0)
    liq = stock.get("liquidity_score")
    if liq is None:
        liq = _liquidity_score(stock)
    return round(score * 0.35 + logic * 0.25 + ltv * 0.2 + buy * 0.1 + liq * 0.1, 2)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestSelectivityScores -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): _selectivity_score 增加流动性维度，权重调整为 35/25/20/10/10"
```

---

### Task 3: enriched 落盘 `liquidity_score`

**Files:**
- Modify: `stock_extractor.py:1567-1569`（stock_view 字段赋值块）

**Interfaces:**
- Consumes: `_liquidity_score`（Task 1）
- Produces: 每个 enriched stock 含 `liquidity_score` 顶层字段

- [ ] **Step 1: 写失败测试**

```python
class TestEnrichLiquidityField:
    """Tests for enriched stocks carrying liquidity_score."""

    def test_enriched_stocks_have_liquidity_score(self):
        from unittest import mock
        from stock_extractor import _enrich_and_score
        stocks_json = {
            "quantitative": [{
                "name": "思泉新材", "code": "301308", "sector": "AIDC液冷",
                "logic": "液冷需求激增，供不应求", "target": "目标价50元",
                "target_aggressive": "", "target_moderate": "", "target_conservative": "",
                "risk": "", "moat": "技术壁垒", "moat_score": 5,
                "management": "", "source": "帖子1", "author": "张三", "confidence": 4,
            }],
            "elastic": [], "sectors": [], "risks": [],
        }
        weights = {"upside": 0.2, "quality": 0.22, "consensus": 0.18, "sector": 0.14,
                   "trend": 0.12, "fundamentals": 0.14, "capital_flow": 0.0, "volume_confirm": 0.0}
        with mock.patch("price_fetcher.fetch_prices", return_value={"301308": {"price": 40.0, "pe": 30, "pb": 4, "market_cap_yi": 150}}), \
             mock.patch("price_fetcher.fetch_5day_changes", return_value={"301308": 3.0}), \
             mock.patch("price_fetcher.fetch_technical_indicators", return_value={}), \
             mock.patch("price_fetcher.fetch_market_environment", return_value={}), \
             mock.patch("price_fetcher.fetch_money_flow", return_value={}), \
             mock.patch("market_review.fetch_lhb_details", return_value={}), \
             mock.patch("adaptive_weights.get_latest_weights", return_value=None), \
             mock.patch("market_regime.detect_market_regime", return_value=("中性", {})), \
             mock.patch("market_regime.get_scoring_weights", return_value=weights):
            enriched, _ = _enrich_and_score(stocks_json, verbose=False)
        assert enriched
        s = enriched[0]
        assert "liquidity_score" in s
        assert 0 <= s["liquidity_score"] <= 10
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestEnrichLiquidityField -v`
Expected: FAIL（`"liquidity_score" in s` 断言——字段不存在）

- [ ] **Step 3: 落盘 liquidity_score**

在 `stock_view` 字段赋值块（1567-1569）加一行：

```python
        stock_view["logic_strength"] = round(lt_trend, 1)
        stock_view["long_term_value"] = _long_term_value_score(stock_view)
        stock_view["selectivity_score"] = _selectivity_score(stock_view)
        stock_view["liquidity_score"] = _liquidity_score(stock_view)
```

注意：`_liquidity_score` 从 `stock_view` 读 `market_cap_yi`（1540 行已设）和 `technical`（1552 行已设），顺序正确。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestEnrichLiquidityField -v`
Expected: PASS

- [ ] **Step 5: 全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): enriched 股票落盘 liquidity_score"
```

---

### Task 4: `_rebuild_report` 精选章节加流动性门槛 + 展示

**Files:**
- Modify: `stock_extractor.py:3772-3786`（精选章节）

**Interfaces:**
- Consumes: `_liquidity_eligible`（Task 1）、`passed`（筛选后展示池）
- Produces: 精选 Top 清单只含流动性合格票；头部文案更新；表格加"流动性"列

- [ ] **Step 1: 在既有 `TestSelectivityReportSection` 类中新增测试方法**

既有类（tests/test_stock_extractor.py:640）已有 `test_selectivity_section_present_and_ranked`，**保留它**，只往该类里**新增**一个方法 `test_selectivity_section_excludes_low_liquidity`（不要替换/删除既有方法）。将下面的方法体插入到该类中，紧跟既有方法之后：

```python
    def test_selectivity_section_excludes_low_liquidity(self):
        from unittest import mock
        from stock_extractor import _rebuild_report
        enriched = [
            {"name": "高价值大票", "code": "600001", "category": "elastic", "sector": "AI/人工智能",
             "score": 4.0, "buy_score": 6.0, "logic": "涨价+供不应求，护城河强",
             "target_str": "目标价50元", "market_cap_yi": 200.0, "current_price": 10,
             "source": "帖子1", "risk_display": "", "opportunity_type": "趋势",
             "trade_period": "中线", "moat_score": 8.0, "fundamentals_score": 7.0,
             "technical": {"volume_ratio": 1.5}},
            {"name": "小市值逻辑好", "code": "600002", "category": "elastic", "sector": "AI/人工智能",
             "score": 5.0, "buy_score": 7.0, "logic": "涨价+供不应求，护城河强",
             "target_str": "目标价50元", "market_cap_yi": 30.0, "current_price": 8,
             "source": "帖子1", "risk_display": "", "opportunity_type": "趋势",
             "trade_period": "中线", "moat_score": 8.0, "fundamentals_score": 7.0,
             "technical": {"volume_ratio": 1.5}},
        ]
        with mock.patch("stock_extractor._apply_liquidity_filter", side_effect=lambda s, **kw: s), \
             mock.patch("portfolio_builder.filter_by_correlation", side_effect=lambda s, **kw: s), \
             mock.patch("portfolio_builder.select_allocation_method", side_effect=lambda s, **kw: s), \
             mock.patch("concentration_monitor.compute_concentration", return_value=None):
            trend_data = {"scores": {}, "groups": {}, "logic_map": {}, "market_filter": {},
                          "market_regime": {"label": "中性"}, "style_exposure": {}}
            report = _rebuild_report(enriched, "## 三、细分板块机会\n| 1 | AI/人工智能 | 高价值大票 | 逻辑 | 帖子1 |\n", trend_data)
        assert "⭐ 精选 Top 清单" in report
        # 高价值大票（市值200亿）进精选；小市值逻辑好（市值30亿）被门槛挡在精选外
        assert "高价值大票" in report
        assert "小市值逻辑好" not in report.split("⭐ 精选 Top 清单")[1].split("📋 按板块分类")[0]
        # 头部文案含流动性
        assert "流动性" in report
        assert "📋 按板块分类" in report  # 全量章节保留
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestSelectivityReportSection -v`
Expected: FAIL（小市值票当前会进精选，且头部文案无"流动性"）

- [ ] **Step 3: 改精选章节（3772-3786）**

将精选章节开头改为：

```python
    # ── 0. 精选 Top 清单（全部候选按精选分取前 N，先过滤流动性门槛）──
    scored_candidates = [s for s in passed if _liquidity_eligible(s)]
    for s in scored_candidates:
        if "selectivity_score" not in s:
            s["selectivity_score"] = _selectivity_score(s)
    scored_candidates.sort(key=lambda s: s.get("selectivity_score", 0), reverse=True)
    n_display = max(8, min(15, round(len(scored_candidates) * 0.15)))
    n_display = min(n_display, len(scored_candidates))
    top_picks = scored_candidates[:n_display]

    parts.append("## ⭐ 精选 Top 清单（最有长期投资价值，流动性优先）\n")
    parts.append(
        f"> 精选依据：推荐指数 35% + 逻辑强度 25% + 长期价值 20% + 买点质量 10% + 流动性 10%（市值≥50亿门槛）"
        f"；精选 {len(top_picks)} 只 / 流动性合格 {len(scored_candidates)} 只。"
    )
```

- [ ] **Step 4: 表格加"流动性"列（3793-3816）**

表格头（3793-3797）改为：

```python
        parts.append(
            "| 排名 | 股票名称 | 板块 | 精选分 | 推荐指数 | 逻辑强度 | 长期价值 | 流动性 | 护城河 | 核心逻辑 | 目标参考 | 风险点 |"
        )
        parts.append(
            "|------|----------|------|--------|----------|----------|----------|--------|--------|----------|----------|--------|"
        )
```

行渲染（3799-3816）在 `risk` 之前插入流动性列：

```python
        for i, s in enumerate(top_picks, 1):
            name = _display_stock_name(s)
            sector = s.get("sector", "-")
            sel = s.get("selectivity_score", 0)
            score_str = _format_score_display(s)
            logic_str = s.get("logic_strength", "-")
            ltv_str = s.get("long_term_value", "-")
            liq = s.get("liquidity_score")
            liq_str = f"{liq:.1f} 💧" if liq is not None and liq >= 7.0 else (f"{liq:.1f}" if liq is not None else "-")
            moat_type = s.get("moat_type", "-")
            moat_score = s.get("moat_score", 5.0)
            moat_flag = " 🏰" if moat_score >= 8.0 else ""
            logic = _emphasize_cell(s.get("logic", "")[:70] if s.get("logic") else "")
            target = _emphasize_cell(s.get("target_str", "")[:50])
            risk = s.get("risk_display", "-")[:70]
            parts.append(
                f"| {i} | {name} | {sector} | **{sel:.1f}** | {score_str} | "
                f"{logic_str} | {ltv_str} | {liq_str} | {moat_type}{moat_flag} | "
                f"{logic} | {target} | {risk} |"
            )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestSelectivityReportSection -v`
Expected: PASS

- [ ] **Step 6: 全量测试 + 回归**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿（既有 `TestSectorClassifiedReport` 等测试不因头部文案变化而破坏——若断言了精确文案需检查）

- [ ] **Step 7: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): 精选 Top 清单加流动性门槛（市值≥50亿）与流动性列"
```

---

### Task 5: 端到端验证 + 文档

**Files:**
- Modify: `tests/test_stock_extractor.py`（端到端断言更新）
- Modify: `.wolf/cerebrum.md`、`.wolf/memory.md`（OpenWolf 追踪）

**Interfaces:**
- Consumes: `extract_stock_opportunities` 产出完整报告
- Produces: 端到端验证 + OpenWolf 追踪

- [ ] **Step 1: 更新端到端测试断言**

在 `TestExtractEndToEnd` 追加断言"流动性"文案：

```python
    def test_end_to_end_has_liquidity_in_selectivity(self):
        from unittest import mock
        from stock_extractor import extract_stock_opportunities
        posts = [
            {"title": "AI算力", "author": "张三", "time": "2026-08-01",
             "content": "思泉新材 液冷需求激增，供不应求，目标价50元。买入推荐。"},
        ]
        fake_report = "## 一、有明确量化目标的股票\n| 1 | 思泉新材 | 301308 | 逻辑 | 目标价50元 | 帖子1 |\n" \
                      "## 三、细分板块机会\n| 1 | AIDC液冷 | 思泉新材 | 逻辑 | 帖子1 |\n" \
                      "```json\n{\"quantitative\": [{\"name\": \"思泉新材\", \"code\": \"301308\", " \
                      "\"sector\": \"AIDC液冷\", \"logic\": \"液冷需求激增，供不应求\", " \
                      "\"target\": \"目标价50元\", \"source\": \"帖子1\"}], \"elastic\": [], " \
                      "\"sectors\": [], \"risks\": []}\n```\n"
        fake_client = mock.Mock()
        fake_client.create.return_value = fake_report
        weights = {"upside": 0.2, "quality": 0.22, "consensus": 0.18, "sector": 0.14,
                   "trend": 0.12, "fundamentals": 0.14, "capital_flow": 0.0, "volume_confirm": 0.0}
        with mock.patch("summarizer.get_client", return_value=(fake_client, "deepseek-v4-flash", "deepseek-v4-flash")), \
             mock.patch("storage.save_enriched_stocks", return_value=None), \
             mock.patch("storage.append_recommendation_history", return_value=None), \
             mock.patch("price_fetcher.fetch_prices", return_value={"301308": {"price": 40.0, "pe": 30, "pb": 4, "market_cap_yi": 150}}), \
             mock.patch("price_fetcher.fetch_5day_changes", return_value={"301308": 3.0}), \
             mock.patch("price_fetcher.fetch_technical_indicators", return_value={}), \
             mock.patch("price_fetcher.fetch_market_environment", return_value={}), \
             mock.patch("price_fetcher.fetch_money_flow", return_value={}), \
             mock.patch("market_review.fetch_lhb_details", return_value={}), \
             mock.patch("adaptive_weights.get_latest_weights", return_value=None), \
             mock.patch("market_regime.detect_market_regime", return_value=("中性", {})), \
             mock.patch("market_regime.get_scoring_weights", return_value=weights), \
             mock.patch("stock_extractor._apply_liquidity_filter", side_effect=lambda s, **kw: s), \
             mock.patch("portfolio_builder.filter_by_correlation", side_effect=lambda s, **kw: s), \
             mock.patch("portfolio_builder.select_allocation_method", side_effect=lambda s, **kw: s), \
             mock.patch("concentration_monitor.compute_concentration", return_value=None):
            report = extract_stock_opportunities(posts)
        assert "⭐ 精选 Top 清单" in report
        assert "流动性" in report
        assert "思泉新材" in report
```

- [ ] **Step 2: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k test_end_to_end_has_liquidity_in_selectivity -v`
Expected: PASS

- [ ] **Step 3: 真实数据端到端**

Run: `python3 main.py stocks 2>&1 | tail -40`
Expected: 报告"⭐ 精选 Top 清单"含流动性列，精选票均为市值≥50亿；`data/summary/*_stocks_*.md` 新生成。

- [ ] **Step 4: 全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 5: 更新 OpenWolf 追踪 + 提交**

向 `.wolf/cerebrum.md` Decision Log 追加 2026-08-06 精选流动性条目；`.wolf/memory.md` 追加操作行。

```bash
git add stock_extractor.py tests/test_stock_extractor.py .wolf/cerebrum.md .wolf/memory.md
git commit -m "test(stocks): 精选流动性端到端验证"
git push origin main
```

- [ ] **Step 6: 验证推送**

Run: `git log --oneline origin/main -1`
Expected: 显示最新提交
