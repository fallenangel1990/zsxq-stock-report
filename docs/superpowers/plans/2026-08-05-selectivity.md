# 精选 Top 清单（长期投资价值优先）— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 报告顶部新增"⭐ 精选 Top 清单"，动态选出最有长期投资价值的 8-15 只，优先推荐护城河强、基本面健康、逻辑硬核的个股；同步调整评分基线让候选池分出高分段。

**Architecture:** 改动集中在 `stock_extractor.py`。在 `_enrich_and_score` 内新增 `_long_term_value_score()`/`_selectivity_score()`，把 `logic_strength`/`long_term_value`/`selectivity_score` 落盘到每个 stock；在 `_rebuild_report` 内于"按板块分类"章节前插入精选 Top 清单。评分基线调整涉及 `base_consensus`、`recency_weight` 应用、`_buy_score` 市场惩罚、`_calibrate_recommendation_score` 下限。

**Tech Stack:** Python 3.9+（CI 用 3.12，避免 PEP 604 `dict | None`，用 `Optional[...]`），pytest，PyYAML。

## Global Constraints

- 精选分权重：`score×40% + logic_strength×30% + long_term_value×20% + buy_score×10%`
- 长期价值权重：`moat_score×40% + fundamentals_score×30% + long_term_trend×30%`
- 动态 N：`N = max(8, min(15, round(candidate_count * 0.15)))`，且 `N = min(N, len(candidates))`
- 复用已有信号：`_long_term_trend_score`、`moat_score`、`fundamentals_score`、`buy_score`（不重复造轮子）
- 评分基线调整表（见 Task 1）：`base_consensus` 1作者 2.0→3.5、post_count>=2 3.0→4.0；`recency_weight` 裸乘→`0.85+0.15*recency_weight`；`market_penalty` 全额→`min(penalty, 1.0)`；校准下限 `max(1.0,...)`→`max(1.5,...)`
- 保留现有 `score`/`buy_score`/阈值/回测逻辑；精选分是新增排序键，不替代
- "📋 按板块分类"全量章节保留，精选 Top 清单在其前
- 类型注解用 `Optional[...]`，不用 `|` 联合
- 变更后必须 `python3 -m pytest tests/ -q` 全绿

---

### Task 1: 评分基线调整

**Files:**
- Modify: `stock_extractor.py:1400-1421`（base_consensus + recency_weight）、`2451-2471`（_buy_score 市场惩罚）、`2260-2277`（_calibrate_recommendation_score 下限）
- Test: `tests/test_stock_extractor.py`

**Interfaces:**
- Consumes: `_buy_score`、`_calibrate_recommendation_score`（已有）
- Produces: 评分基线调整后的 `_buy_score`/`_calibrate_recommendation_score` 行为

- [ ] **Step 1: 写失败测试**

```python
class TestScoringBaseline:
    """Tests for scoring baseline adjustments (long-term value feature)."""

    def test_single_author_gets_higher_base_consensus(self):
        # 1 作者、1 帖的推荐，共识基础分从 2.0 提高到 3.5
        # 通过 _calibrate 间接验证：base_score 用较高共识分后校准值上升
        from stock_extractor import _calibrate_recommendation_score
        # 模拟 base_score 只含共识贡献（w_consensus=0.18 * 3.5 ≈ 0.63）
        calibrated = _calibrate_recommendation_score(
            base_score=0.63,
            logic_score=5.0,
            target_precision=5.0,
            post_count=1,
            category="elastic",
            unique_authors=1,
        )
        assert calibrated >= 1.5, "校准下限应从 1.0 提到 1.5"

    def test_market_penalty_capped_at_1(self):
        # 用真实 _buy_score 验证惩罚上限
        from stock_extractor import _buy_score
        stock = {
            "score": 2.0,
            "technical_score": 5.0,
            "risk_display": "",
            "technical": {},
            "change_5d": 1.0,
            "market_filter": {"buy_penalty": 2.0, "buy_bonus": 0.0},
        }
        bs = _buy_score(stock)
        # score*0.52=1.04 + tech*0.36=1.8 + cred*0.12 + 0 - min(2,1)=1.0
        assert bs >= 1.0, "市场惩罚超过 1.0 时应封顶，避免吃掉全部得分"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestScoringBaseline -v`
Expected: FAIL（`test_market_penalty_capped_at_1` 断言失败——当前全额扣 2.0）

- [ ] **Step 3: 调整 base_consensus（1400-1421）**

```python
        # 基础共识分（按独立作者数计分，避免同一作者多篇重复加分）
        if unique_authors >= 4:
            base_consensus = 8.5
        elif unique_authors == 3:
            base_consensus = 7.0
        elif unique_authors == 2:
            base_consensus = 5.5
        elif post_count >= 3:
            # 同一作者多篇推荐：给基础分但不额外加成
            base_consensus = 3.5
        elif post_count >= 2:
            base_consensus = 4.0
        else:
            base_consensus = 3.5

        # 时间加权：最近提及权重更高（温和化，避免陈旧推荐被系数打到极低）
        consensus_score = base_consensus * (0.85 + 0.15 * recency_weight)
```

- [ ] **Step 4: 调整 _buy_score 市场惩罚（2467）**

```python
    market_penalty = min(market_filter.get("buy_penalty", 0.0), 1.0)
```

- [ ] **Step 5: 调整 _calibrate_recommendation_score 下限（2277）**

```python
    return round(max(1.5, min(10.0, calibrated)), 1)
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestScoringBaseline -v`
Expected: PASS（2 passed）

- [ ] **Step 7: 全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿（若有既有断言依赖旧基线，检查是否合理，合理则修正断言）

- [ ] **Step 8: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): 评分基线调整——提高单作者共识基础分、温和化时间衰减、市场惩罚封顶、校准下限上调"
```

---

### Task 2: 新增 `_long_term_value_score` 与 `_selectivity_score`

**Files:**
- Modify: `stock_extractor.py`（在 `_long_term_trend_score` 之后新增，约 2085 行处）
- Test: `tests/test_stock_extractor.py`

**Interfaces:**
- Consumes: `_long_term_trend_score`（已有，2085 行返回 0-10）
- Produces:
  - `_long_term_value_score(stock: dict) -> float`（0-10）
  - `_selectivity_score(stock: dict) -> float`（精选综合分）

- [ ] **Step 1: 写失败测试**

```python
class TestSelectivityScores:
    """Tests for long-term value and selectivity scoring."""

    def test_long_term_value_weighted(self):
        from stock_extractor import _long_term_value_score
        stock = {
            "moat_score": 8.0,          # 高护城河
            "fundamentals_score": 7.0,  # 基本面健康
            "long_term_trend": 9.0,     # 长期景气高
        }
        score = _long_term_value_score(stock)
        expected = round(8.0 * 0.4 + 7.0 * 0.3 + 9.0 * 0.3, 2)
        assert score == expected
        assert score >= 7.0  # 高护城河+景气 → 高长期价值

    def test_selectivity_score_weights(self):
        from stock_extractor import _selectivity_score
        stock = {
            "score": 4.0,
            "logic_strength": 8.0,
            "long_term_value": 7.0,
            "buy_score": 6.0,
        }
        s = _selectivity_score(stock)
        expected = round(4.0 * 0.4 + 8.0 * 0.3 + 7.0 * 0.2 + 6.0 * 0.1, 2)
        assert s == expected
        # 逻辑强度权重高 → 高分逻辑推高精选分
        assert s > stock["score"]

    def test_missing_signals_fallback(self):
        from stock_extractor import _long_term_value_score, _selectivity_score
        empty = {}
        ltv = _long_term_value_score(empty)  # moat缺省5.0, fundamentals缺省5.0, trend用_long_term_trend("")=3.0
        assert 0 <= ltv <= 10
        sel = _selectivity_score(empty)
        assert 0 <= sel <= 10
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestSelectivityScores -v`
Expected: FAIL（`ImportError: cannot import name '_long_term_value_score'`）

- [ ] **Step 3: 实现两个函数**

在 `_long_term_trend_score`（2085 行 `return round(max(0.0, min(10.0, score)), 1)` 之后）新增：

```python
def _long_term_value_score(stock: dict) -> float:
    """长期投资价值（0-10）：护城河40% + 基本面30% + 长期景气30%。"""
    moat = stock.get("moat_score", 5.0)
    fundamentals = stock.get("fundamentals_score", 5.0)
    lt_trend = stock.get("long_term_trend")
    if lt_trend is None:
        lt_trend = _long_term_trend_score(
            stock.get("logic", ""), stock.get("target_str", ""), stock.get("risk_str", "")
        )
    return round(moat * 0.4 + fundamentals * 0.3 + lt_trend * 0.3, 2)


def _selectivity_score(stock: dict) -> float:
    """精选综合分：score×40% + logic_strength×30% + long_term_value×20% + buy_score×10%。"""
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
    return round(score * 0.4 + logic * 0.3 + ltv * 0.2 + buy * 0.1, 2)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestSelectivityScores -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): 新增 _long_term_value_score 与 _selectivity_score"
```

---

### Task 3: `_enrich_and_score` 落盘新字段

**Files:**
- Modify: `stock_extractor.py:1567-1582`（enriched append 块）

**Interfaces:**
- Consumes: `_long_term_value_score`、`_selectivity_score`（Task 2）
- Produces: 每个 enriched stock 含 `logic_strength`、`long_term_value`、`selectivity_score` 三个顶层字段

- [ ] **Step 1: 写失败测试**

```python
class TestEnrichSelectivityFields:
    """Tests for enriched stocks carrying selectivity fields."""

    def test_enriched_stocks_have_selectivity_fields(self):
        from unittest import mock
        from stock_extractor import _enrich_and_score
        stocks_json = {
            "quantitative": [{
                "name": "思泉新材", "code": "301308", "sector": "AIDC液冷",
                "logic": "液冷需求激增，供不应求，国产替代加速", "target": "目标价50元",
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
        assert "logic_strength" in s
        assert "long_term_value" in s
        assert "selectivity_score" in s
        assert 0 <= s["selectivity_score"] <= 10
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestEnrichSelectivityFields -v`
Expected: FAIL（`"logic_strength" in s` 断言——字段不存在）

- [ ] **Step 3: 在 enriched append 前计算并落盘**

在 `_enrich_and_score` 的 `enriched.append(...)`（1567 行）之前、`stock_view` 定义之后（1553 行 `"technical": technical,` 之后）加：

```python
        stock_view["logic_strength"] = round(lt_trend, 1)
        stock_view["long_term_value"] = _long_term_value_score(stock_view)
        stock_view["selectivity_score"] = _selectivity_score(stock_view)
```

注意：`stock_view` 是 dict，`_long_term_value_score`/`_selectivity_score` 从 `stock_view` 读 `moat_score`/`fundamentals_score`/`long_term_trend`/`score`/`buy_score`。其中 `long_term_trend` 不在 stock_view 顶层（在 score_detail 里）——因此 Task 2 的函数里 `stock.get("long_term_trend")` 返回 None，会回退调 `_long_term_trend_score(logic, target, risk)` 重新计算，结果正确。`buy_score` 在 1561 行已算入 stock_view。`fundamentals_score` 在 1545 行已算入。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestEnrichSelectivityFields -v`
Expected: PASS

- [ ] **Step 5: 全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): enriched 股票落盘 logic_strength/long_term_value/selectivity_score"
```

---

### Task 4: `_rebuild_report` 插入"⭐ 精选 Top 清单"章节

**Files:**
- Modify: `stock_extractor.py`（在 3755 行 `# ── 0. 按板块分类主清单` 之前插入）

**Interfaces:**
- Consumes: `passed`（筛选后展示池）、`_selectivity_score`/`_long_term_value_score`（Task 2）、`_display_stock_name`/`_fmt_market_cap`/`_emphasize_cell`/`_format_score_display`（已有）
- Produces: 报告含"⭐ 精选 Top 清单"章节

- [ ] **Step 1: 写失败测试**

```python
class TestSelectivityReportSection:
    """Tests for 精选 Top 清单 section in report."""

    def test_selectivity_section_present_and_ranked(self):
        from unittest import mock
        from stock_extractor import _rebuild_report
        enriched = [
            {"name": "高价值A", "code": "600001", "category": "elastic", "sector": "AI/人工智能",
             "score": 4.0, "buy_score": 6.0, "logic": "涨价+供不应求+国产替代，护城河强",
             "target_str": "目标价50元", "market_cap_yi": 100, "current_price": 10,
             "source": "帖子1", "risk_display": "", "opportunity_type": "趋势",
             "trade_period": "中线", "moat_score": 8.0, "fundamentals_score": 7.0},
            {"name": "低价值B", "code": "600002", "category": "elastic", "sector": "AI/人工智能",
             "score": 2.0, "buy_score": 2.0, "logic": "普通逻辑", "target_str": "",
             "market_cap_yi": 80, "current_price": 8, "source": "帖子1", "risk_display": "",
             "opportunity_type": "观察", "trade_period": "中线", "moat_score": 4.0,
             "fundamentals_score": 4.0},
        ]
        with mock.patch("stock_extractor._apply_liquidity_filter", side_effect=lambda s, **kw: s), \
             mock.patch("stock_extractor.filter_by_correlation", side_effect=lambda s, **kw: s), \
             mock.patch("stock_extractor.compute_concentration", return_value=None):
            trend_data = {"scores": {}, "groups": {}, "logic_map": {}, "market_filter": {},
                          "market_regime": {"label": "中性"}, "style_exposure": {}}
            report = _rebuild_report(enriched, "## 三、细分板块机会\n| 1 | AI/人工智能 | 高价值A | 逻辑 | 帖子1 |\n", trend_data)
        assert "⭐ 精选 Top 清单" in report
        # 高价值A 精选分更高 → 应排在低价值B 前面
        a_idx = report.index("高价值A")
        b_idx = report.index("低价值B")
        assert a_idx < b_idx, "高长期价值股票应排在精选清单前面"
        assert "📋 按板块分类" in report  # 全量章节保留
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestSelectivityReportSection -v`
Expected: FAIL（`"⭐ 精选 Top 清单" in report` 断言——章节不存在）

- [ ] **Step 3: 插入精选 Top 清单章节**

在 3755 行 `# ── 0. 按板块分类主清单` 之前插入：

```python
    # ── 0. 精选 Top 清单（最有长期投资价值，全部候选按精选分取前 N）──
    scored_candidates = list(passed)
    for s in scored_candidates:
        if "selectivity_score" not in s:
            s["selectivity_score"] = _selectivity_score(s)
    scored_candidates.sort(key=lambda s: s.get("selectivity_score", 0), reverse=True)
    n_display = max(8, min(15, round(len(scored_candidates) * 0.15)))
    n_display = min(n_display, len(scored_candidates))
    top_picks = scored_candidates[:n_display]

    parts.append("## ⭐ 精选 Top 清单（最有长期投资价值）\n")
    parts.append(
        f"> 精选依据：推荐指数 40% + 逻辑强度 30% + 长期价值 20% + 买点质量 10%"
        f"；精选 {len(top_picks)} 只 / 全部候选 {len(scored_candidates)} 只。"
    )
    parts.append("")
    if not top_picks:
        parts.append("| 排名 | 股票名称 | 板块 | 精选分 | 备注 |")
        parts.append("|------|----------|------|--------|------|")
        parts.append("| - | 本次无候选个股 | - | - | - |")
    else:
        parts.append(
            "| 排名 | 股票名称 | 板块 | 精选分 | 推荐指数 | 逻辑强度 | 长期价值 | 护城河 | 核心逻辑 | 目标参考 | 风险点 |"
        )
        parts.append(
            "|------|----------|------|--------|----------|----------|----------|--------|----------|----------|--------|"
        )
        for i, s in enumerate(top_picks, 1):
            name = _display_stock_name(s)
            sector = s.get("sector", "-")
            sel = s.get("selectivity_score", 0)
            score_str = _format_score_display(s)
            logic_str = s.get("logic_strength", "-")
            ltv_str = s.get("long_term_value", "-")
            moat_type = s.get("moat_type", "-")
            moat_score = s.get("moat_score", 5.0)
            moat_flag = " 🏰" if moat_score >= 8.0 else ""
            logic = _emphasize_cell(s.get("logic", "")[:70] if s.get("logic") else "")
            target = _emphasize_cell(s.get("target_str", "")[:50])
            risk = s.get("risk_display", "-")[:70]
            parts.append(
                f"| {i} | {name} | {sector} | **{sel:.1f}** | {score_str} | "
                f"{logic_str} | {ltv_str} | {moat_type}{moat_flag} | "
                f"{logic} | {target} | {risk} |"
            )
    parts.append("")

```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k TestSelectivityReportSection -v`
Expected: PASS

- [ ] **Step 5: 全量测试 + 回归**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): 报告新增精选 Top 清单章节（长期投资价值优先，动态 N）"
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

在现有 `TestExtractEndToEnd`（Task 5 of previous plan）追加断言"⭐ 精选 Top 清单"：

```python
    def test_end_to_end_has_selectivity_section(self):
        from unittest import mock
        from stock_extractor import extract_stock_opportunities
        posts = [
            {"title": "AI算力", "author": "张三", "time": "2026-08-01",
             "content": "思泉新材 液冷需求激增，供不应求，目标价50元。买入推荐。"},
        ]
        fake_report = "## 一、有明确量化目标的股票\n| 1 | 思泉新材 | 301308 | 逻辑 | 目标价50元 | 帖子1 |\n" \
                      "## 三、细分板块机会\n| 1 | AIDC液冷 | 思泉新材 | 逻辑 | 帖子1 |\n" \
                      "```json\n{\"quantitative\": [{\"name\": \"思泉新材\", \"code\": \"301308\", " \
                      "\"sector\": \"AIDC液冷\", \"logic\": \"液冷需求激增，供不应求，国产替代\", " \
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
             mock.patch("stock_extractor.filter_by_correlation", side_effect=lambda s, **kw: s), \
             mock.patch("stock_extractor.compute_concentration", return_value=None):
            report = extract_stock_opportunities(posts)
        assert "⭐ 精选 Top 清单" in report
        assert "思泉新材" in report
```

- [ ] **Step 2: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k test_end_to_end_has_selectivity_section -v`
Expected: PASS

- [ ] **Step 3: 真实数据端到端**

Run: `python3 main.py stocks 2>&1 | tail -40`
Expected: 报告顶部出现"⭐ 精选 Top 清单"，含精选分/推荐指数/逻辑强度/长期价值/护城河列；`data/summary/*_stocks_*.md` 新生成。

- [ ] **Step 4: 全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿（原 117 + 新增）

- [ ] **Step 5: 更新 OpenWolf 追踪 + 提交**

向 `.wolf/cerebrum.md` Decision Log 追加 2026-08-05 精选 Top 清单条目；`.wolf/memory.md` 追加操作行。

```bash
git add stock_extractor.py tests/test_stock_extractor.py .wolf/cerebrum.md .wolf/memory.md
git commit -m "test(stocks): 精选 Top 清单端到端验证"
git push origin main
```

- [ ] **Step 6: 验证推送**

Run: `git log --oneline origin/main -1`
Expected: 显示最新提交
