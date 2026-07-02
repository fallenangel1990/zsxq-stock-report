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

