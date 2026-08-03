"""auction_model.py 单元测试。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from auction_model import (
    _check_veto,
    _score_auction_volume,
    _score_auction_change,
    _score_auction_turnover,
    _score_auction_trend,
    _score_auction_amount,
    _score_auction_participation,
    _score_sector_resonance,
    score_auction_stock,
    select_auction_stocks,
    MAX_AUCTION_CHANGE_PCT,
    MIN_AUCTION_CHANGE_PCT,
    MIN_AUCTION_AMOUNT_YI,
    MIN_MARKET_CAP_YI,
)


# ──────────────────────────────────────────────────────────────
# 否决过滤器测试
# ──────────────────────────────────────────────────────────────
class TestVeto:
    def test_normal_stock_pass(self):
        stock = {"name": "贵州茅台", "change_pct": 2.0, "auction_amount": 1e8, "market_cap_yi": 2000}
        vetoed, reason = _check_veto(stock)
        assert vetoed is False
        assert reason == ""

    def test_st_blacklist(self):
        stock = {"name": "ST康美", "change_pct": 1.0, "auction_amount": 1e8, "market_cap_yi": 100}
        vetoed, reason = _check_veto(stock)
        assert vetoed is True
        assert "黑名单" in reason

    def test_change_too_high(self):
        stock = {"name": "测试", "change_pct": 6.0, "auction_amount": 1e8, "market_cap_yi": 100}
        vetoed, reason = _check_veto(stock)
        assert vetoed is True
        assert "涨幅过高" in reason

    def test_change_too_low(self):
        stock = {"name": "测试", "change_pct": -4.0, "auction_amount": 1e8, "market_cap_yi": 100}
        vetoed, reason = _check_veto(stock)
        assert vetoed is True
        assert "涨幅过低" in reason

    def test_amount_too_small(self):
        stock = {"name": "测试", "change_pct": 1.0, "auction_amount": 1e6, "market_cap_yi": 100}
        vetoed, reason = _check_veto(stock)
        assert vetoed is True
        assert "金额不足" in reason

    def test_market_cap_too_small(self):
        stock = {"name": "测试", "change_pct": 1.0, "auction_amount": 1e8, "market_cap_yi": 10}
        vetoed, reason = _check_veto(stock)
        assert vetoed is True
        assert "市值过小" in reason

    def test_no_volume_rise(self):
        stock = {"name": "测试", "change_pct": 3.0, "volume_ratio": 0.3, "auction_amount": 1e8, "market_cap_yi": 100}
        vetoed, reason = _check_veto(stock)
        assert vetoed is True
        assert "无量空涨" in reason


# ──────────────────────────────────────────────────────────────
# 单因子评分测试
# ──────────────────────────────────────────────────────────────
class TestScoreVolume:
    def test_high_ratio(self):
        assert _score_auction_volume({"volume_ratio": 5.0}) == 2.0

    def test_medium_ratio(self):
        score = _score_auction_volume({"volume_ratio": 2.5})
        assert 1.0 < score < 2.0

    def test_low_ratio(self):
        score = _score_auction_volume({"volume_ratio": 0.5})
        assert 0 < score < 0.5

    def test_zero_ratio(self):
        assert _score_auction_volume({"volume_ratio": 0}) == 0.0

    def test_none_ratio(self):
        assert _score_auction_volume({"volume_ratio": None}) == 0.0


class TestScoreChange:
    def test_optimal_range(self):
        assert _score_auction_change({"change_pct": 2.0}) == 2.0

    def test_zero_change(self):
        score = _score_auction_change({"change_pct": 0.0})
        assert 0.5 < score < 1.5

    def test_high_change(self):
        score = _score_auction_change({"change_pct": 4.5})
        assert 0 < score < 1.0

    def test_negative_change(self):
        score = _score_auction_change({"change_pct": -0.5})
        assert 0 < score < 0.5

    def test_none_change(self):
        assert _score_auction_change({"change_pct": None}) == 0.0


class TestScoreTurnover:
    def test_high_turnover(self):
        assert _score_auction_turnover({"auction_turnover_rate": 2.0}) == 1.5

    def test_medium_turnover(self):
        score = _score_auction_turnover({"auction_turnover_rate": 1.0})
        assert 0.5 < score < 1.0

    def test_low_turnover(self):
        score = _score_auction_turnover({"auction_turnover_rate": 0.3})
        assert 0 < score < 0.3

    def test_zero_turnover(self):
        assert _score_auction_turnover({"auction_turnover_rate": 0}) == 0.0


class TestScoreTrend:
    def test_empty_trend(self):
        assert _score_auction_trend({}, []) == 0.5

    def test_uptrend(self):
        trend = [
            {"time": "09:15", "price": 10.0, "volume": 100},
            {"time": "09:18", "price": 10.1, "volume": 120},
            {"time": "09:21", "price": 10.2, "volume": 150},
            {"time": "09:24", "price": 10.3, "volume": 180},
        ]
        score = _score_auction_trend({}, trend)
        assert score > 1.0

    def test_downtrend(self):
        trend = [
            {"time": "09:15", "price": 10.3, "volume": 180},
            {"time": "09:18", "price": 10.2, "volume": 150},
            {"time": "09:21", "price": 10.1, "volume": 120},
            {"time": "09:24", "price": 10.0, "volume": 100},
        ]
        score = _score_auction_trend({}, trend)
        assert score < 0.5


class TestScoreAmount:
    def test_high_amount(self):
        assert _score_auction_amount({"auction_amount": 3e8}) == 1.0

    def test_medium_amount(self):
        score = _score_auction_amount({"auction_amount": 1.5e8})
        assert 0.7 < score < 1.0

    def test_low_amount(self):
        score = _score_auction_amount({"auction_amount": 3e7})
        assert 0 < score < 0.3

    def test_zero_amount(self):
        assert _score_auction_amount({"auction_amount": 0}) == 0.0


class TestScoreParticipation:
    def test_high_participation(self):
        assert _score_auction_participation({"auction_volume_ratio": 35}) == 1.0

    def test_medium_participation(self):
        score = _score_auction_participation({"auction_volume_ratio": 15})
        assert 0.4 < score < 0.7

    def test_low_participation(self):
        score = _score_auction_participation({"auction_volume_ratio": 5})
        assert 0 < score < 0.3


class TestScoreSector:
    def test_no_sector(self):
        assert _score_sector_resonance({}, {}) == 0.3

    def test_strong_resonance(self):
        stock = {"code": "600519", "sector": "白酒"}
        sectors = {"白酒": [{"code": "600519"}, {"code": "000858"}, {"code": "002304"}, {"code": "000568"}, {"code": "603369"}, {"code": "600809"}, {"code": "000799"}]}
        score = _score_sector_resonance(stock, sectors)
        assert score == 1.0

    def test_medium_resonance(self):
        stock = {"code": "600519", "sector": "白酒"}
        sectors = {"白酒": [{"code": "600519"}, {"code": "000858"}, {"code": "002304"}, {"code": "000568"}, {"code": "603369"}]}
        score = _score_sector_resonance(stock, sectors)
        assert score == 0.8

    def test_weak_resonance(self):
        stock = {"code": "600519", "sector": "白酒"}
        sectors = {"白酒": [{"code": "600519"}]}
        score = _score_sector_resonance(stock, sectors)
        assert score == 0.3


# ──────────────────────────────────────────────────────────────
# 综合评分测试
# ──────────────────────────────────────────────────────────────
class TestScoreAuctionStock:
    def test_strong_buy(self):
        stock = {
            "code": "600519",
            "name": "贵州茅台",
            "volume_ratio": 3.5,
            "auction_change_pct": 2.0,
            "auction_turnover_rate": 1.5,
            "auction_amount": 2e8,
            "auction_volume_ratio": 25,
            "float_market_cap_yi": 2000,
        }
        result = score_auction_stock(stock)
        assert result["signal"] in ("强烈买入", "买入关注")
        assert result["total_score"] >= 5.5

    def test_vetoed_stock(self):
        stock = {
            "code": "600001",
            "name": "ST测试",
            "volume_ratio": 3.0,
            "auction_change_pct": 2.0,
            "auction_amount": 1e8,
            "market_cap_yi": 100,
        }
        result = score_auction_stock(stock)
        assert result["signal"] == "排除"
        assert result["is_vetoed"] is True
        assert result["total_score"] <= 4.0

    def test_weak_stock(self):
        stock = {
            "code": "600001",
            "name": "测试",
            "volume_ratio": 0.5,
            "auction_change_pct": -1.0,
            "auction_amount": 6e7,
            "market_cap_yi": 50,
        }
        result = score_auction_stock(stock)
        assert result["total_score"] < 5.5

    def test_scores_structure(self):
        stock = {
            "code": "600519",
            "name": "贵州茅台",
            "volume_ratio": 2.0,
            "auction_change_pct": 1.5,
            "auction_amount": 1e8,
            "market_cap_yi": 2000,
        }
        result = score_auction_stock(stock)
        assert "scores" in result
        assert "volume" in result["scores"]
        assert "change" in result["scores"]
        assert "turnover" in result["scores"]
        assert "trend" in result["scores"]
        assert "amount" in result["scores"]
        assert "participation" in result["scores"]
        assert "sector" in result["scores"]
        assert "raw_data" in result


class TestSelectAuctionStocks:
    def test_sorting(self):
        stocks = [
            {"code": "001", "name": "A", "volume_ratio": 1.0, "auction_change_pct": 0.5, "auction_amount": 6e7, "market_cap_yi": 100},
            {"code": "002", "name": "B", "volume_ratio": 4.0, "auction_change_pct": 2.0, "auction_amount": 2e8, "market_cap_yi": 500},
            {"code": "003", "name": "C", "volume_ratio": 2.0, "auction_change_pct": 1.0, "auction_amount": 1e8, "market_cap_yi": 200},
        ]
        results = select_auction_stocks(stocks, verbose=False)
        assert len(results) == 3
        # Should be sorted by total_score descending
        assert results[0]["total_score"] >= results[1]["total_score"]
        assert results[1]["total_score"] >= results[2]["total_score"]
        # B should be first (highest scores)
        assert results[0]["code"] == "002"

    def test_empty_list(self):
        results = select_auction_stocks([], verbose=False)
        assert results == []

    def test_vetoed_sorted_to_bottom(self):
        stocks = [
            {"code": "001", "name": "ST测试", "volume_ratio": 3.0, "auction_change_pct": 2.0, "auction_amount": 1e8, "market_cap_yi": 100},
            {"code": "002", "name": "正常", "volume_ratio": 3.0, "auction_change_pct": 2.0, "auction_amount": 1e8, "market_cap_yi": 100},
        ]
        results = select_auction_stocks(stocks, verbose=False)
        assert results[0]["code"] == "002"
        assert results[1]["code"] == "001"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
