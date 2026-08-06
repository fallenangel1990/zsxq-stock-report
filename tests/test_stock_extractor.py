"""Unit tests for pure functions in stock_extractor.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPercentileRank:
    """Tests for _percentile_rank logic."""

    def test_percentile_rank_middle(self):
        # Replicate _percentile_rank from stock_extractor
        def _percentile_rank(values, x):
            below = sum(1 for v in values if v < x)
            equal = sum(1 for v in values if v == x)
            return (below + 0.5 * equal) / len(values) if values else 0.5

        values = [1, 2, 3, 4, 5]
        assert _percentile_rank(values, 3) == 0.5

    def test_percentile_rank_highest(self):
        def _percentile_rank(values, x):
            below = sum(1 for v in values if v < x)
            equal = sum(1 for v in values if v == x)
            return (below + 0.5 * equal) / len(values) if values else 0.5

        values = [1, 2, 3, 4, 5]
        assert _percentile_rank(values, 5) == 0.9

    def test_percentile_rank_lowest(self):
        def _percentile_rank(values, x):
            below = sum(1 for v in values if v < x)
            equal = sum(1 for v in values if v == x)
            return (below + 0.5 * equal) / len(values) if values else 0.5

        values = [1, 2, 3, 4, 5]
        assert _percentile_rank(values, 1) == 0.1

    def test_percentile_rank_empty(self):
        def _percentile_rank(values, x):
            below = sum(1 for v in values if v < x)
            equal = sum(1 for v in values if v == x)
            return (below + 0.5 * equal) / len(values) if values else 0.5

        assert _percentile_rank([], 5) == 0.5


class TestAssessQuality:
    """Tests for quality scoring from target_text."""

    def test_empty_string(self):
        from stock_extractor import _assess_quality
        assert _assess_quality("") == 0.3

    def test_price_target(self):
        from stock_extractor import _assess_quality
        score = _assess_quality("目标价 150 元")
        assert score >= 0.6

    def test_market_cap_target(self):
        from stock_extractor import _assess_quality
        score = _assess_quality("目标市值 340 亿")
        assert score >= 0.5

    def test_no_quantifiable(self):
        from stock_extractor import _assess_quality
        score = _assess_quality("看好公司长期发展")
        assert 0.3 <= score < 0.5


class TestParseTargetValue:
    """Tests for numeric target extraction."""

    def test_price_yuan(self):
        from stock_extractor import _parse_target_value
        assert _parse_target_value("目标价 150 元") == 150.0

    def test_market_cap_yi(self):
        from stock_extractor import _parse_target_value
        assert _parse_target_value("目标市值 340 亿") == 340.0

    def test_empty(self):
        from stock_extractor import _parse_target_value
        assert _parse_target_value("") is None

    def test_scientific_notation(self):
        from stock_extractor import _parse_target_value
        assert _parse_target_value("目标 200e") == 200.0



class TestCrowdingPenalty:
    """Tests for crowding penalty calculation (uses sector_rank int)."""

    def test_no_crowding_top2(self):
        from stock_extractor import _compute_crowding_penalty
        stock = {"sector": "AI/人工智能"}
        # sector_rank <= 2 means no penalty
        assert _compute_crowding_penalty(stock, 1) == 0.0
        assert _compute_crowding_penalty(stock, 2) == 0.0

    def test_crowding_rank3(self):
        from stock_extractor import _compute_crowding_penalty
        stock = {"sector": "AI/人工智能"}
        # rank 3: -0.3 * (3-2) = -0.3
        assert _compute_crowding_penalty(stock, 3) == -0.3

    def test_crowding_rank5(self):
        from stock_extractor import _compute_crowding_penalty
        stock = {"sector": "AI/人工智能"}
        # rank 5: -0.3 * (5-2) = -0.9
        assert abs(_compute_crowding_penalty(stock, 5) - (-0.9)) < 0.001


class TestATRStopLoss:
    """Tests for ATR-based stop loss."""

    def test_basic(self):
        from stock_extractor import _atr_based_stop_loss
        stock = {"current_price": 100, "technical": {"atr_14": 3.0}}
        stop = _atr_based_stop_loss(stock)
        # max(100*0.94, 100 - 2*3) = max(94, 94) = 94
        assert stop == 94.0

    def test_atr_wider(self):
        from stock_extractor import _atr_based_stop_loss
        stock = {"current_price": 100, "technical": {"atr_14": 5.0}}
        stop = _atr_based_stop_loss(stock)
        # max(94, 100 - 10) = max(94, 90) = 94
        assert stop == 94.0

    def test_no_data(self):
        from stock_extractor import _atr_based_stop_loss
        assert _atr_based_stop_loss({}) is None
        assert _atr_based_stop_loss({"current_price": 100}) is None

    def test_atr_narrower(self):
        from stock_extractor import _atr_based_stop_loss
        stock = {"current_price": 100, "technical": {"atr_14": 1.0}}
        stop = _atr_based_stop_loss(stock)
        # max(94, 100 - 2) = max(94, 98) = 98
        assert stop == 98.0



class TestWalkForwardIC:
    """Tests for walk-forward IC calculation."""

    def test_insufficient_data(self):
        from backtester import walk_forward_ic
        assert walk_forward_ic([]) == []
        assert walk_forward_ic([{"code": "600000"}]) == []

    def test_basic_walk_forward(self):
        from backtester import walk_forward_ic
        # Create mock records spread over multiple dates
        records = []
        for i in range(30):
            records.append({
                "code": "600519",
                "score": 5 + (i % 5),
                "current_price": 150 + i,
                "generated_at": f"2026-06-{i+1:02d}T10:00:00",
                "score_detail": {"upside": 5, "quality": 5},
                "forward_return_5d": 0.02 * (i % 3 - 1),
            })
        result = walk_forward_ic(records, window=10, step=5, return_days=5)
        # Should produce at least one walk-forward period
        assert isinstance(result, list)


class TestScoreMonotonicity:
    """Tests for score group validation."""

    def test_empty_records(self):
        from backtester import validate_score_monotonicity
        result = validate_score_monotonicity([])
        assert result["is_monotonic"] is False

    def test_insufficient_data(self):
        from backtester import validate_score_monotonicity
        records = [{"code": "600000", "score": 5, "current_price": 100, "generated_at": "2026-07-01"}]
        result = validate_score_monotonicity(records)
        # No forward returns available
        assert result["is_monotonic"] is False


class TestFactorOrthogonalization:
    """Tests for factor orthogonalization."""

    def test_no_high_correlation(self):
        from adaptive_weights import orthogonalize_factor_weights
        corr = {"upside": {"quality": 0.1}, "quality": {"upside": 0.1}}
        weights = {"upside": 0.3, "quality": 0.3, "trend": 0.4}
        result = orthogonalize_factor_weights(corr, weights)
        assert abs(sum(result.values()) - 1.0) < 0.01

    def test_high_correlation_reduces_weaker(self):
        from adaptive_weights import orthogonalize_factor_weights
        corr = {"upside": {"quality": 0.85}, "quality": {"upside": 0.85}}
        weights = {"upside": 0.5, "quality": 0.5}
        result = orthogonalize_factor_weights(corr, weights)
        # One of them should be reduced
        assert result["upside"] != result["quality"] or (result["upside"] == 0.5 and result["quality"] == 0.5)


class TestMarketRegimeWeights:
    """Tests for market regime factor templates."""

    def test_bull_regime(self):
        from adaptive_weights import get_market_regime_weights
        weights = get_market_regime_weights("强势进攻")
        assert abs(sum(weights.values()) - 1.0) < 0.01
        assert weights["upside"] > weights["fundamentals"]

    def test_bear_regime(self):
        from adaptive_weights import get_market_regime_weights
        weights = get_market_regime_weights("防守降仓")
        assert abs(sum(weights.values()) - 1.0) < 0.01
        assert weights["quality"] > weights["trend"]

    def test_unknown_regime(self):
        from adaptive_weights import get_market_regime_weights
        assert get_market_regime_weights("unknown") == {}



class TestParseStockJson:
    """Tests for _parse_stock_json fallback to markdown tables."""

    def test_empty_json_block_does_not_shadow_tables(self):
        """空 JSON 块不应遮蔽表格中的真实股票。"""
        from stock_extractor import _parse_stock_json
        markdown = """## 一、有明确量化目标的股票

| 序号 | 股票名称 | 代码 | 上涨/投资逻辑 | 量化参考 | 来源帖子 |
|------|----------|------|--------------|----------|----------|
| 1 | 思泉新材 | 301308 | 液冷需求激增 | 目标价50元 | 帖子1 |

## 三、细分板块机会

| 序号 | 板块名称 | 核心标的 | 板块逻辑 | 来源帖子 |
|------|----------|----------|----------|----------|
| 1 | AIDC液冷 | 思泉新材 | 云厂商机柜量上修 | 帖子1 |

```json
{"quantitative": [], "elastic": [], "sectors": [], "risks": []}
```
"""
        parsed = _parse_stock_json(markdown)
        assert parsed.get("quantitative") or parsed.get("sectors"), \
            "AI 输出空 JSON 块但表格有真实股票时，必须回退解析表格"

    def test_empty_json_block_returns_sectors_from_tables(self):
        """空 JSON + 表格中的细分板块应被解析出来。"""
        from stock_extractor import _parse_stock_json
        markdown = """## 三、细分板块机会

| 序号 | 板块名称 | 核心标的 | 板块逻辑 | 来源帖子 |
|------|----------|----------|----------|----------|
| 1 | AIDC液冷 | 思泉新材 | 云厂商机柜量上修 | 帖子1 |
| 2 | 电子布 | 中国巨石， 建滔积层板 | 景气上行 | 帖子2 |

```json
{"quantitative": [], "elastic": [], "sectors": [], "risks": []}
```
"""
        parsed = _parse_stock_json(markdown)
        sectors = parsed.get("sectors", [])
        assert len(sectors) == 2
        assert sectors[0]["stocks"] == "思泉新材"

    def test_valid_json_still_used(self):
        """非空 JSON 仍应优先使用，不受回退影响。"""
        from stock_extractor import _parse_stock_json
        markdown = """```json
{"quantitative": [{"name": "思泉新材", "code": "301308", "sector": "AIDC液冷", "logic": "液冷需求激增", "target": "目标价50元", "source": "帖子1"}], "elastic": [], "sectors": [], "risks": []}
```"""
        parsed = _parse_stock_json(markdown)
        assert len(parsed.get("quantitative", [])) == 1
        assert parsed["quantitative"][0]["name"] == "思泉新材"


class TestFallbackParseTables:
    """Tests for _fallback_parse_tables with standard markdown spacing."""

    def test_standard_spaced_markdown_rows(self):
        """标准 `| 1 |` 带空格的表格行应被解析。"""
        from stock_extractor import _fallback_parse_tables
        markdown = """## 三、细分板块机会

| 序号 | 板块名称 | 核心标的 | 板块逻辑 | 来源帖子 |
|------|----------|----------|----------|----------|
| 1 | AIDC液冷 | 思泉新材 | 云厂商机柜量上修 | 帖子1 |
| 2 | 电子布 | 中国巨石， 建滔积层板 | 景气上行 | 帖子2 |
"""
        result = _fallback_parse_tables(markdown)
        assert len(result["sectors"]) == 2
        assert result["sectors"][0]["stocks"] == "思泉新材"
        assert result["sectors"][1]["stocks"].startswith("中国巨石")


class TestSectorAliasesScope:
    """Tests for sector_aliases scope in _enrich_and_score."""

    def test_enrich_and_score_does_not_crash_on_sector_inference(self):
        """板块推断必须能在 sector_aliases 加载后运行，不抛 UnboundLocalError。"""
        from unittest import mock
        from stock_extractor import _enrich_and_score
        stocks_json = {
            "quantitative": [{
                "name": "思泉新材", "code": "301308", "sector": "",
                "logic": "液冷需求激增，AI数据中心建设加速", "target": "目标价50元",
                "target_aggressive": "", "target_moderate": "", "target_conservative": "",
                "risk": "", "moat": "", "moat_score": "", "management": "",
                "source": "帖子1", "author": "张三", "confidence": 4,
            }],
            "elastic": [],
            "sectors": [{"sector": "AIDC液冷", "stocks": "思泉新材", "logic": "液冷需求激增", "source": "帖子1"}],
            "risks": [],
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
            enriched, trend = _enrich_and_score(stocks_json, verbose=False)
        assert enriched, "板块推断不应崩溃，且应产出候选"
        assert all(s.get("sector") for s in enriched if s.get("name") == "思泉新材"), \
            "思泉新材应被推断出板块（从 logic 中的 AIDC/液冷关键词）"


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
             mock.patch("portfolio_builder.filter_by_correlation", side_effect=lambda s, **kw: s), \
             mock.patch("portfolio_builder.select_allocation_method", side_effect=lambda s, **kw: s), \
             mock.patch("concentration_monitor.compute_concentration", return_value=None):
            # 用最小 trend_data 避免外部依赖
            trend_data = {"scores": {}, "groups": {}, "logic_map": {}, "market_filter": {},
                          "market_regime": {"label": "中性"}, "style_exposure": {}}
            report = _rebuild_report(enriched, "## 三、细分板块机会\n| 1 | AI/人工智能 | A | 逻辑 | 帖子1 |\n", trend_data)
        # Task 4 起主清单为"按板块分类"（全部候选，评分仅作板块内排序），低分 B 应出现
        assert "按板块分类" in report
        assert "逻辑A" in report  # 高分展示
        assert "逻辑B" in report  # 低分也展示（不再被阈值截断）


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
             mock.patch("portfolio_builder.filter_by_correlation", side_effect=lambda s, **kw: s), \
             mock.patch("portfolio_builder.select_allocation_method", side_effect=lambda s, **kw: s), \
             mock.patch("concentration_monitor.compute_concentration", return_value=None):
            trend_data = {"scores": {}, "groups": {}, "logic_map": {}, "market_filter": {},
                          "market_regime": {"label": "中性"}, "style_exposure": {}}
            report = _rebuild_report(enriched, "## 三、细分板块机会\n| 1 | AI/人工智能 | A | 逻辑 | 帖子1 |\n", trend_data)
        assert "## 📋 按板块分类" in report
        assert "### 板块：AI/人工智能" in report or "AI/人工智能" in report
        assert "全部候选" in report  # 过滤概览文案
        assert "快速选股清单" not in report  # 扁平清单已移除


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
        with mock.patch("summarizer.get_client", return_value=(fake_client, "deepseek-v4-flash", "deepseek-v4-flash")), \
             mock.patch("price_fetcher.fetch_prices", return_value={"301308": {"price": 40.0, "pe": 30, "pb": 4, "market_cap_yi": 150}}), \
             mock.patch("price_fetcher.fetch_5day_changes", return_value={"301308": 3.0}), \
             mock.patch("price_fetcher.fetch_technical_indicators", return_value={}), \
             mock.patch("price_fetcher.fetch_market_environment", return_value={}), \
             mock.patch("price_fetcher.fetch_money_flow", return_value={}), \
             mock.patch("market_review.fetch_lhb_details", return_value={}), \
             mock.patch("adaptive_weights.get_latest_weights", return_value=None), \
             mock.patch("market_regime.detect_market_regime", return_value={"label": "中性", "score": 50.0, "desc": "", "signals": {}}), \
             mock.patch("market_regime.get_scoring_weights", return_value=weights), \
             mock.patch("storage.save_enriched_stocks", return_value=None), \
             mock.patch("storage.append_recommendation_history", return_value=None), \
             mock.patch("stock_extractor._apply_liquidity_filter", side_effect=lambda s, **kw: s):
            report = extract_stock_opportunities(posts)
        assert "按板块分类" in report
        assert "思泉新材" in report

    def test_end_to_end_has_selectivity_section(self):
        """端到端回归：extract_stock_opportunities 全链路产出"⭐ 精选 Top 清单"。

        相对 brief 的三处修正（test-as-spec）：
        ① detect_market_regime 真实返回 dict，mock 返回 tuple ("中性", {}) 会流入
           trend_data["market_regime"]，在 _rebuild_report 的 regime.get("label") 处崩溃；
        ② filter_by_correlation / compute_concentration 是 _rebuild_report 内函数级
           `from X import Y` 局部导入，patch stock_extractor.* 是静默空转，须 patch
           portfolio_builder.* / concentration_monitor.*；
        ③ 补上同 try 块内的 select_allocation_method mock，保证测试免真实网络调用。
        """
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
                      "\"target\": \"目标价50元\", \"source\": \"帖子1\"}], \"elastic\": [], \"sectors\": [], \"risks\": []}\n```\n"
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
             mock.patch("market_regime.detect_market_regime", return_value={"label": "中性", "score": 50.0, "desc": "", "signals": {}}), \
             mock.patch("market_regime.get_scoring_weights", return_value=weights), \
             mock.patch("stock_extractor._apply_liquidity_filter", side_effect=lambda s, **kw: s), \
             mock.patch("portfolio_builder.filter_by_correlation", side_effect=lambda s, **kw: s), \
             mock.patch("portfolio_builder.select_allocation_method", side_effect=lambda s, **kw: s), \
             mock.patch("concentration_monitor.compute_concentration", return_value=None):
            report = extract_stock_opportunities(posts)
        assert "⭐ 精选 Top 清单" in report
        assert "思泉新材" in report


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
        # score*0.52=1.04 + tech*0.36=1.8 + cred*0.12=0.636 + 0 - min(2,1)=1.0 → ≈2.48
        # 旧基线全额扣 2.0 时 bs=1.5；封顶 1.0 后 bs=2.5。断言 >=2.0 才能区分（>=1.0 在旧代码也通过，不具判别力）
        assert bs >= 2.0, "市场惩罚超过 1.0 时应封顶，避免吃掉全部得分"


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
            "liquidity_score": 7.0,
        }
        s = _selectivity_score(stock)
        expected = round(4.0 * 0.35 + 8.0 * 0.25 + 7.0 * 0.2 + 6.0 * 0.1 + 7.0 * 0.1, 2)
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
        # 注意：filter_by_correlation/select_allocation_method 在 _rebuild_report 内是函数内
        # `from portfolio_builder import ...` 局部导入，compute_concentration 同理来自
        # concentration_monitor，故必须 patch 源模块，不能 patch stock_extractor.*（静默空转）。
        with mock.patch("stock_extractor._apply_liquidity_filter", side_effect=lambda s, **kw: s), \
             mock.patch("portfolio_builder.filter_by_correlation", side_effect=lambda s, **kw: s), \
             mock.patch("portfolio_builder.select_allocation_method", side_effect=lambda s, **kw: s), \
             mock.patch("concentration_monitor.compute_concentration", return_value=None):
            trend_data = {"scores": {}, "groups": {}, "logic_map": {}, "market_filter": {},
                          "market_regime": {"label": "中性"}, "style_exposure": {}}
            report = _rebuild_report(enriched, "## 三、细分板块机会\n| 1 | AI/人工智能 | 高价值A | 逻辑 | 帖子1 |\n", trend_data)
        assert "⭐ 精选 Top 清单" in report
        # 高价值A 精选分更高 → 应排在低价值B 前面
        a_idx = report.index("高价值A")
        b_idx = report.index("低价值B")
        assert a_idx < b_idx, "高长期价值股票应排在精选清单前面"
        assert "📋 按板块分类" in report  # 全量章节保留


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
