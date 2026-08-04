# 个股按板块分类展示 + 去掉评分阈值 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 股票报告按具体板块分类展示全部候选个股，移除评分阈值筛选（保留流动性/相关性风控）。

**Architecture:** 改动集中在 `stock_extractor.py` 的 `_rebuild_report()` 与 `_select_report_display_stocks()`。新增 `_group_stocks_by_sector()` 聚合函数，用现有 `_normalize_sector_name()` + config 的 `sector_aliases` 标准化板块名。筛选链路变为：全部 `enriched` → `_apply_liquidity_filter` → `filter_by_correlation` → 按板块分类展示。

**Tech Stack:** Python 3.9+（注意 Do-Not-Repeat：本地 python3 是 3.14，但 CI 用 3.12；避免 PEP 604 `dict | None` 注解，用 `Optional[...]`），pytest，PyYAML。

## Global Constraints

- 板块名用 `_normalize_sector_name()` 标准化，别名映射来自 config 的 `stocks.scoring.sector_aliases`
- 无板块（`sector` 为空）的个股归入"未分类"分组，**不得丢弃**
- 保留 `_apply_liquidity_filter` 与 `filter_by_correlation`（max_corr=0.7），两者可能减少展示数量
- 保留章节：一、量化目标（增强）/ 二、弹性标的（增强）/ 三、细分板块机会（AI 原始）/ 🔥 行业趋势概览 / 快速否决清单
- 移除章节：扁平"快速选股清单"
- 评分/推荐指数只作排序与标注，不作门槛
- 类型注解用 `Optional[...]`，不用 `|` 联合（Do-Not-Repeat 2026-05-15）
- 变更后必须 `python3 -m pytest tests/ -q` 全绿

---

### Task 1: 新增 `_group_stocks_by_sector` 聚合函数

**Files:**
- Modify: `stock_extractor.py`（在 `_normalize_sector_name` 函数之后新增，约 827 行处）
- Test: `tests/test_stock_extractor.py`（追加新测试类）

**Interfaces:**
- Consumes: `_normalize_sector_name(sector: str, sector_aliases: dict) -> str`（已有，810 行）
- Produces: `_group_stocks_by_sector(stocks: list[dict], sector_aliases: dict) -> list[dict]`
  返回板块列表，每项 `{"sector": str, "stocks": list[dict]}`，板块内按 `(buy_score, score)` 降序，板块间按板块内最高分降序；无板块个股归入 `{"sector": "未分类", "stocks": [...]}`（排最后）。

- [ ] **Step 1: 写失败测试**

```python
class TestGroupStocksBySector:
    """Tests for _group_stocks_by_sector aggregation."""

    def test_groups_by_normalized_sector(self):
        from stock_extractor import _group_stocks_by_sector
        aliases = {"AI": "AI/人工智能", "算力": "AI/人工智能", "光模块": "AI/人工智能"}
        stocks = [
            {"name": "A", "sector": "AI", "score": 3.0, "buy_score": 5.0},
            {"name": "B", "sector": "算力", "score": 4.0, "buy_score": 6.0},
            {"name": "C", "sector": "半导体/芯片", "score": 2.0, "buy_score": 4.0},
            {"name": "D", "sector": "", "score": 1.0, "buy_score": 2.0},
        ]
        groups = _group_stocks_by_sector(stocks, aliases)
        assert len(groups) == 3  # AI/人工智能、半导体/芯片、未分类
        ai_group = next(g for g in groups if g["sector"] == "AI/人工智能")
        assert [s["name"] for s in ai_group["stocks"]] == ["B", "A"]  # 板块内降序
        assert groups[0]["sector"] == "AI/人工智能"  # 最高分板块在前
        assert groups[-1]["sector"] == "未分类"  # 未分类排最后

    def test_sector_stock_dedup(self):
        from stock_extractor import _group_stocks_by_sector
        stocks = [
            {"name": "A", "code": "600001", "sector": "AI", "score": 3.0, "buy_score": 5.0},
            {"name": "A", "code": "600001", "sector": "AI", "score": 3.0, "buy_score": 5.0},
        ]
        groups = _group_stocks_by_sector(stocks, {})
        assert len(groups[0]["stocks"]) == 1  # 同代码去重
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k GroupStocksBySector -v`
Expected: FAIL，`ImportError: cannot import name '_group_stocks_by_sector'`

- [ ] **Step 3: 实现函数**

在 `_normalize_sector_name`（827 行 `return ""` 之后）后新增：

```python
def _group_stocks_by_sector(stocks: list[dict], sector_aliases: dict) -> list[dict]:
    """按标准化板块名聚合个股，板块内按 (buy_score, score) 降序。

    板块间按板块内最高分降序；无板块（sector 为空/未命中别名）的个股
    归入"未分类"分组排最后，避免丢票。
    """
    groups: dict[str, list[dict]] = {}
    for s in stocks:
        norm = _normalize_sector_name(s.get("sector", ""), sector_aliases)
        key = norm if norm else "未分类"
        groups.setdefault(key, []).append(s)

    result = []
    for sector_name, stock_list in groups.items():
        seen = set()
        deduped = []
        for s in stock_list:
            code = s.get("code", "")
            ident = code or s.get("name", "")
            if ident in seen:
                continue
            seen.add(ident)
            deduped.append(s)
        deduped.sort(
            key=lambda x: (x.get("buy_score", 0), x.get("score", 0)),
            reverse=True,
        )
        result.append({"sector": sector_name, "stocks": deduped})

    result.sort(
        key=lambda g: (0 if g["sector"] == "未分类" else 1, (g["stocks"][0].get("score", 0) if g["stocks"] else 0)),
        reverse=True,
    )
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k GroupStocksBySector -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): 新增 _group_stocks_by_sector 板块聚合函数"
```

---

### Task 2: `_select_report_display_stocks` 去掉评分截断

**Files:**
- Modify: `stock_extractor.py:3609-3653`（`_select_report_display_stocks` 函数体）
- Test: `tests/test_stock_extractor.py`（追加测试类）

**Interfaces:**
- Consumes: 现有 `REPORT_RECOMMENDATION_THRESHOLD` / `REPORT_OBSERVATION_THRESHOLD`（本任务移除其截断作用，常量保留给 meta 统计）
- Produces: `_select_report_display_stocks(enriched: list[dict]) -> tuple[list[dict], dict]`
  返回全部排序后的股票 + meta。meta 键不变：`candidate_count`（= 全部）、`recommendation_count`（= score≥3.0 数，仅供统计）、`display_count`（= 全部）、`mode`。

- [ ] **Step 1: 写失败测试**

```python
class TestSelectReportDisplayStocks:
    """Tests for _select_report_display_stocks no longer truncating."""

    def test_all_stocks_returned_below_threshold(self):
        from stock_extractor import _select_report_display_stocks
        stocks = [
            {"name": "A", "score": 3.5, "buy_score": 5.0},
            {"name": "B", "score": 2.0, "buy_score": 3.0},
            {"name": "C", "score": 1.0, "buy_score": 2.0},
        ]
        display, meta = _select_report_display_stocks(stocks)
        assert len(display) == 3  # 低分股票也保留
        assert meta["display_count"] == 3
        assert meta["recommendation_count"] == 1  # 仅 A ≥3.0
        assert meta["candidate_count"] == 3

    def test_empty_input(self):
        from stock_extractor import _select_report_display_stocks
        display, meta = _select_report_display_stocks([])
        assert display == []
        assert meta["display_count"] == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k SelectReportDisplayStocks -v`
Expected: FAIL（当前 `display` 只含 A，`display_count == 1`）

- [ ] **Step 3: 替换函数体**

```python
def _select_report_display_stocks(enriched: list[dict]) -> tuple[list[dict], dict]:
    """选择最终报告展示池。

    不再按分数截断：全部候选都进入展示，评分仅作排序依据。
    recommendation_count 仅用于统计展示（score≥3.0 的数量），不参与过滤。
    """
    sorted_stocks = sorted(
        enriched or [],
        key=lambda s: (s.get("buy_score", 0), s.get("score", 0)),
        reverse=True,
    )
    recommendations = [
        s for s in sorted_stocks
        if s.get("score", 0) >= REPORT_RECOMMENDATION_THRESHOLD
    ]
    meta = {
        "candidate_count": len(sorted_stocks),
        "recommendation_count": len(recommendations),
        "display_count": len(sorted_stocks),
        "threshold": REPORT_RECOMMENDATION_THRESHOLD,
        "observation_threshold": REPORT_OBSERVATION_THRESHOLD,
        "mode": "all_candidates",
    }
    return sorted_stocks, meta
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k SelectReportDisplayStocks -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): _select_report_display_stocks 不再按分数截断候选池"
```

---

### Task 3: `_rebuild_report` 筛选链路去掉评分阈值 + 每板块上限

**Files:**
- Modify: `stock_extractor.py:3667-3719`（`_rebuild_report` 开头筛选链路）

**Interfaces:**
- Consumes: `_select_report_display_stocks`（Task 2 返回全部）、`_apply_liquidity_filter`、`filter_by_correlation`
- Produces: `passed`（经流动性+相关性风控后的展示池，不再按分数截断、不再按板块上限裁剪）

- [ ] **Step 1: 写失败测试**

```python
class TestRebuildReportNoScoreThreshold:
    """Tests for _rebuild_report removing score threshold and sector cap."""

    def test_low_score_stock_not_dropped_by_threshold(self):
        from unittest import mock
        from stock_extractor import _rebuild_report
        # 模拟两个候选：一个高分一个低分，均不应被评分阈值丢弃
        enriched = [
            {"name": "A", "code": "600001", "category": "elastic", "sector": "AI/人工智能",
             "score": 3.5, "buy_score": 5.0, "logic": "逻辑A", "target_str": "",
             "market_cap_yi": 100, "current_price": 10, "source": "帖子1",
             "risk_display": "", "opportunity_type": "趋势", "trade_period": "中线"},
            {"name": "B", "code": "600002", "category": "elastic", "sector": "AI/人工智能",
             "score": 1.5, "buy_score": 2.0, "logic": "逻辑B", "target_str": "",
             "market_cap_yi": 80, "current_price": 8, "source": "帖子1",
             "risk_display": "", "opportunity_type": "观察", "trade_period": "中线"},
        ]
        with mock.patch("stock_extractor._apply_liquidity_filter", side_effect=lambda s, **kw: s), \
             mock.patch("stock_extractor._apply_portfolio_constraints", side_effect=lambda s, **kw: s):
            # 用最小 trend_data 避免外部依赖
            trend_data = {"scores": {}, "groups": {}, "logic_map": {}, "market_filter": {},
                          "market_regime": {"label": "中性"}, "style_exposure": {}}
            report = _rebuild_report(enriched, "## 三、细分板块机会\n| 1 | AI/人工智能 | A | 逻辑 | 帖子1 |\n", trend_data)
        assert "按板块分类" in report
        assert "逻辑A" in report  # 高分展示
        assert "逻辑B" in report  # 低分也展示（不再被阈值截断）
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k RebuildReportNoScoreThreshold -v`
Expected: FAIL（当前低分 B 被 `passed` 阈值截断，报告中无"按板块分类"）

- [ ] **Step 3: 修改筛选链路**

将 3667-3719 区域改为（保留流动性/相关性，删除评分阈值与板块上限）：

```python
    all_enriched = list(enriched or [])
    enriched, display_meta = _select_report_display_stocks(all_enriched)
    trend_data["display_meta"] = display_meta
    trend_scores = trend_data.get("scores", {})
    sector_groups = trend_data.get("groups", {})
    sector_logic_map = trend_data.get("logic_map", {})
    market_filter = trend_data.get("market_filter", {})
    # 先移除 JSON 代码块，避免泄露到最终输出
    original_markdown = _strip_json_block(original_markdown)
    parts = []

    # ── 市场状态自适应参数（分数仅作标注，不再截断）──
    regime = trend_data.get("market_regime", {})

    # ── 风控：流动性过滤 + 相关性控制（保留），不做评分阈值截断 ──
    passed = list(enriched)
    passed = _apply_liquidity_filter(passed)

    # ── 滑点与冲击成本估算 ──
    for s in passed:
        _estimate_slippage(s)

    # ── 组合层风控：个股间相关性控制 ──
    try:
        from portfolio_builder import filter_by_correlation, select_allocation_method, format_portfolio_summary
        passed = filter_by_correlation(passed, max_corr=0.7)
        # 智能仓位分配（Kelly/风险平价/波动率反比自动选择）
        passed = select_allocation_method(passed, method="auto")
        portfolio_summary = format_portfolio_summary(passed, regime)
    except Exception:
        portfolio_summary = ""

    # 诊断统计
    total_scored = len(enriched)
    total_passed = len(passed)
    filter_meta = {
        "total_scored": total_scored,
        "total_passed": total_passed,
        "score_threshold": None,
        "ma5_tolerance": 3.0,
    }
```

> 注意：删除 `score_threshold = regime.get(...)`、`max_per_sector = regime.get(...)`、`passed = [s for s in enriched if ...]`、`filtered_out = [...]`、`passed = _apply_portfolio_constraints(...)`。后续代码中对 `filtered_out`、`score_threshold` 的引用也要一并处理（见 Step 5 与 Task 4）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k RebuildReportNoScoreThreshold -v`
Expected: PASS

- [ ] **Step 5: 修正后续对已删除变量的引用**

删除/改写 3766-3778 的过滤概览（Task 4 会重写），以及 3848-3857 的"过滤诊断"中对 `filtered_out`/`score_threshold` 的引用。先跑 `grep -n "filtered_out\|score_threshold" stock_extractor.py` 找出所有引用点，逐个处理（`filtered_out` 相关块可删除；`score_threshold` 在过滤概览处改为 `None`/文案调整）。

- [ ] **Step 6: 运行全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全部通过（若因删除变量失败，继续修正）

- [ ] **Step 7: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): _rebuild_report 移除评分阈值与每板块上限，保留流动性/相关性风控"
```

---

### Task 4: 按板块分类主清单 + 过滤概览文案更新

**Files:**
- Modify: `stock_extractor.py:3815-3846`（"快速选股清单"章节替换）与 `3766-3778`（过滤概览文案）

**Interfaces:**
- Consumes: `_group_stocks_by_sector`（Task 1）、`passed`（Task 3 产出）、`trend_scores`（板块趋势）
- Produces: 报告含"📋 按板块分类"主清单章节；过滤概览反映"不再按分数筛选"

- [ ] **Step 1: 写失败测试**

```python
class TestSectorClassifiedReport:
    """Tests for sector-classified main list in report."""

    def test_sector_classified_section_present(self):
        from unittest import mock
        from stock_extractor import _rebuild_report
        enriched = [
            {"name": "A", "code": "600001", "category": "elastic", "sector": "AI/人工智能",
             "score": 3.5, "buy_score": 5.0, "logic": "逻辑A", "target_str": "目标50元",
             "market_cap_yi": 100, "current_price": 10, "source": "帖子1",
             "risk_display": "", "opportunity_type": "趋势", "trade_period": "中线"},
        ]
        with mock.patch("stock_extractor._apply_liquidity_filter", side_effect=lambda s, **kw: s), \
             mock.patch("stock_extractor._apply_portfolio_constraints", side_effect=lambda s, **kw: s):
            trend_data = {"scores": {}, "groups": {}, "logic_map": {}, "market_filter": {},
                          "market_regime": {"label": "中性"}, "style_exposure": {}}
            report = _rebuild_report(enriched, "## 三、细分板块机会\n| 1 | AI/人工智能 | A | 逻辑 | 帖子1 |\n", trend_data)
        assert "## 📋 按板块分类" in report
        assert "### 板块：AI/人工智能" in report or "AI/人工智能" in report
        assert "全部候选" in report  # 过滤概览文案
        assert "快速选股清单" not in report  # 扁平清单已移除
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_stock_extractor.py -k SectorClassifiedReport -v`
Expected: FAIL（当前无"按板块分类"章节，且过滤概览是"评分 ≥3.0 分入选"）

- [ ] **Step 3: 过滤概览文案更新（3766-3778）**

将原"过滤概览"块替换为：

```python
    # 过滤概览
    parts.append("## 过滤概览\n")
    parts.append(
        f"> 全部候选 **{total_scored}** 只（不再按评分筛选）；"
        f"经流动性/相关性风控后展示 **{total_passed}** 只。"
    )
    parts.append("")
```

- [ ] **Step 4: 快速选股清单替换为按板块分类（3815-3846）**

将 3815 起至 3846 的"快速选股总览"章节整体替换为：

```python
    # ── 0. 按板块分类主清单（全部候选，评分仅作板块内排序）──
    parts.append("## 📋 按板块分类（全部候选，评分仅作板块内排序）\n")
    from stock_extractor import _load_scoring_config
    sector_aliases = _load_scoring_config().get("sector_aliases", {})
    sector_list = _group_stocks_by_sector(passed, sector_aliases)

    if not sector_list:
        parts.append("| 板块 | 备注 |")
        parts.append("|------|------|")
        parts.append("| - | 本次无候选个股 |")
    for group in sector_list:
        sec = group["sector"]
        stocks = group["stocks"]
        parts.append(f"### 板块：{sec}（板块内 {len(stocks)} 只）\n")
        parts.append(
            "| 股票名称 | 机会类型 | 周期 | 当前市值 | 推荐指数 | 核心逻辑 | 目标参考 | 风险点 |"
        )
        parts.append(
            "|----------|----------|------|----------|----------|----------|----------|--------|"
        )
        for s in stocks:
            name = _display_stock_name(s)
            market_cap_str = _fmt_market_cap(s.get("market_cap_yi"))
            target_str = _emphasize_cell(s.get("target_str", ""))
            logic = _emphasize_cell(s.get("logic", "")[:70] if s.get("logic") else "")
            risk = s.get("risk_display", "-")[:70]
            score_str = _format_score_display(s)
            parts.append(
                f"| {name} | {s.get('opportunity_type', '-')} | {s.get('trade_period', '-')} | "
                f"{market_cap_str} | {score_str} | {logic} | {target_str} | {risk} |"
            )
        parts.append("")
```

> 注意：`from stock_extractor import _load_scoring_config` 在模块内部可简写为 `_load_scoring_config()`（同模块函数），直接调用即可，无需再 import。

- [ ] **Step 5: 删除/改写"过滤诊断"中对已删除变量的引用（3848-3857）**

将 `if not passed and enriched:` 的"过滤诊断"块删除（因 `filtered_out` 已不存在），替换为简单的空池提示：

```python
    if not passed and enriched:
        parts.append("## 过滤诊断\n")
        parts.append("- 候选经流动性/相关性风控后为空，无个股可展示。")
        parts.append("")
```

- [ ] **Step 6: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k SectorClassifiedReport -v`
Expected: PASS

- [ ] **Step 7: 全量测试 + 回归**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 8: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "feat(stocks): 报告改为按板块分类展示全部候选，更新过滤概览文案"
```

---

### Task 5: 端到端验证 + 修复 `_append_report_filter_note`

**Files:**
- Modify: `stock_extractor.py`（`_append_report_filter_note`，若仍引用旧 meta 逻辑）
- Test: `tests/test_stock_extractor.py`

**Interfaces:**
- Consumes: `_rebuild_report` 产出报告字符串
- Produces: 完整的按板块分类报告，且 `extract_stock_opportunities` 端到端可跑

- [ ] **Step 1: 确认 `_append_report_filter_note` 是死代码（无需改动）**

Run: `grep -n "_append_report_filter_note(" stock_extractor.py`
Expected: 仅 3650 行定义处命中，**无调用点** → 该函数是死代码，Task 2 移除 `adaptive_observation` mode 不影响任何运行路径，跳过对它的改动。

- [ ] **Step 2: 写端到端回归测试**

```python
class TestExtractEndToEnd:
    """End-to-end regression: extract_stock_opportunities produces sector-classified report."""

    def test_full_pipeline_no_zero_candidates(self):
        from unittest import mock
        from stock_extractor import extract_stock_opportunities
        posts = [
            {"title": "AI算力", "author": "张三", "time": "2026-08-01",
             "content": "思泉新材 液冷需求激增，目标价50元。买入推荐。"},
        ]
        fake_report = "## 一、有明确量化目标的股票\n| 1 | 思泉新材 | 301308 | 逻辑 | 目标价50元 | 帖子1 |\n" \
                      "## 三、细分板块机会\n| 1 | AIDC液冷 | 思泉新材 | 逻辑 | 帖子1 |\n" \
                      "```json\n{\"quantitative\": [{\"name\": \"思泉新材\", \"code\": \"301308\", " \
                      "\"sector\": \"AIDC液冷\", \"logic\": \"液冷需求激增\", \"target\": \"目标价50元\", " \
                      "\"source\": \"帖子1\"}], \"elastic\": [], \"sectors\": [], \"risks\": []}\n```\n"
        fake_client = mock.Mock()
        fake_client.create.return_value = fake_report
        weights = {"upside": 0.2, "quality": 0.22, "consensus": 0.18, "sector": 0.14,
                   "trend": 0.12, "fundamentals": 0.14, "capital_flow": 0.0, "volume_confirm": 0.0}
        with mock.patch("stock_extractor.get_client", return_value=(fake_client, "deepseek-v4-flash", "deepseek-v4-flash")), \
             mock.patch("price_fetcher.fetch_prices", return_value={"301308": {"price": 40.0, "pe": 30, "pb": 4, "market_cap_yi": 150}}), \
             mock.patch("price_fetcher.fetch_5day_changes", return_value={"301308": 3.0}), \
             mock.patch("price_fetcher.fetch_technical_indicators", return_value={}), \
             mock.patch("price_fetcher.fetch_market_environment", return_value={}), \
             mock.patch("price_fetcher.fetch_money_flow", return_value={}), \
             mock.patch("market_review.fetch_lhb_details", return_value={}), \
             mock.patch("adaptive_weights.get_latest_weights", return_value=None), \
             mock.patch("market_regime.detect_market_regime", return_value=("中性", {})), \
             mock.patch("market_regime.get_scoring_weights", return_value=weights), \
             mock.patch("stock_extractor._apply_liquidity_filter", side_effect=lambda s, **kw: s):
            report = extract_stock_opportunities(posts)
        assert "按板块分类" in report
        assert "思泉新材" in report
```

- [ ] **Step 3: 运行测试确认通过**

Run: `python3 -m pytest tests/test_stock_extractor.py -k ExtractEndToEnd -v`
Expected: PASS

- [ ] **Step 4: 真实数据端到端**

Run: `python3 main.py stocks 2>&1 | tail -30`
Expected: 报告含"📋 按板块分类"章节，候选个股按板块聚合展示，无"无股票通过筛选"；`data/summary/*_stocks_*.md` 新生成。

- [ ] **Step 5: 全量测试**

Run: `python3 -m pytest tests/ -q`
Expected: 全绿（原 110 个 + 新增）

- [ ] **Step 6: 提交**

```bash
git add stock_extractor.py tests/test_stock_extractor.py
git commit -m "test(stocks): 端到端回归验证按板块分类报告"
```

---

### Task 6: 文档 + 推送

**Files:**
- Modify: `.wolf/cerebrum.md`、`.wolf/memory.md`、`.wolf/buglog.json`（按 OpenWolf 协议）

- [ ] **Step 1: 更新 OpenWolf 追踪**

向 `.wolf/cerebrum.md` Decision Log 追加一条：2026-08-04 个股按板块分类展示 + 去掉评分阈值（方案3：保留流动性/相关性）。`.wolf/memory.md` 追加本次会话操作行。

- [ ] **Step 2: 提交并推送**

```bash
git add .wolf/cerebrum.md .wolf/memory.md
git commit -m "docs: 记录按板块分类展示 + 去评分阈值实现"
git push origin main
```

- [ ] **Step 3: 验证推送**

Run: `git log --oneline origin/main -1`
Expected: 显示最新提交
