"""Unit tests for pure functions in portfolio_builder.py."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio_builder import _correlation, calculate_volatility, kelly_criterion, allocate_risk_budget


class TestCorrelation:
    """Tests for Pearson correlation calculation."""

    def test_perfect_positive(self):
        xs = [1, 2, 3, 4, 5]
        ys = [2, 4, 6, 8, 10]
        assert _correlation(xs, ys) == 1.0

    def test_perfect_negative(self):
        xs = [1, 2, 3, 4, 5]
        ys = [10, 8, 6, 4, 2]
        assert _correlation(xs, ys) == -1.0

    def test_no_correlation(self):
        xs = [1, 2, 3, 4, 5]
        ys = [5, 1, 4, 2, 3]
        r = _correlation(xs, ys)
        assert -1.0 <= r <= 1.0

    def test_too_few_points(self):
        assert _correlation([1, 2], [3, 4]) == 0.0

    def test_empty_input(self):
        assert _correlation([], []) == 0.0

    def test_constant_input(self):
        assert _correlation([1, 1, 1], [1, 2, 3]) == 0.0

    def test_different_length(self):
        xs = [1, 2, 3, 4, 5, 6, 7]
        ys = [2, 4, 6, 8, 10]
        r = _correlation(xs, ys)
        assert r == 1.0


class TestVolatility:
    """Tests for volatility calculation."""

    def test_basic(self):
        returns = [0.01, -0.02, 0.015, -0.01, 0.005]
        vol = calculate_volatility(returns)
        assert vol > 0

    def test_constant_returns(self):
        assert calculate_volatility([0.01, 0.01, 0.01]) == 0.0

    def test_empty_returns(self):
        assert calculate_volatility([]) == 0.0


class TestKellyCriterion:
    """Tests for Kelly criterion position sizing."""

    def test_basic(self):
        k = kelly_criterion(0.6, 0.1, 0.05)
        assert 0 < k < 1

    def test_unfavorable(self):
        k = kelly_criterion(0.3, 0.05, 0.1)
        assert k <= 0

    def test_edge_zero(self):
        k = kelly_criterion(0.5, 0.1, 0.1)
        assert k == 0


class TestAllocateRiskBudget:
    """Tests for equal-weight risk budget allocation."""

    def test_equal_split(self):
        stocks = [
            {"code": "600000", "name": "A", "score": 5},
            {"code": "600001", "name": "B", "score": 5},
            {"code": "600002", "name": "C", "score": 5},
        ]
        result = allocate_risk_budget(stocks, method="equal")
        weights = [s.get("position_pct", 0) for s in result]
        assert abs(sum(weights) - 100.0) < 1.0

    def test_inverse_vol(self):
        stocks = [
            {"code": "600000", "name": "A", "score": 5},
            {"code": "600001", "name": "B", "score": 5},
        ]
        result = allocate_risk_budget(stocks, method="inverse_vol")
        weights = [s.get("position_pct", 0) for s in result]
        assert all(0 <= w <= 40 for w in weights)

