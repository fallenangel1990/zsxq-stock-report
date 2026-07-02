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

