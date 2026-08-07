"""paper_trader.py 单元测试。"""

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ──────────────────────────────────────────────────────────────
# auto_trade_from_recommendations 精选/流动性测试
# ──────────────────────────────────────────────────────────────
class TestPaperTraderSelectivity:
    """Tests for paper_trader using selectivity/liquidity data."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """每个测试使用临时目录，避免写库副作用。"""
        with mock.patch("paper_trader.PORTFOLIO_FILE", tmp_path / "portfolio.json"), \
             mock.patch("paper_trader.TRADES_FILE", tmp_path / "trades.jsonl"), \
             mock.patch("paper_trader.NAV_HISTORY_FILE", tmp_path / "nav_history.json"):
            yield

    def test_low_liquidity_not_simulated(self):
        from paper_trader import auto_trade_from_recommendations
        enriched = [
            {"code": "600001", "name": "低流动性", "score": 8.0, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 30.0},  # <50亿
            {"code": "600002", "name": "高流动性", "score": 8.0, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 200.0},
        ]
        with mock.patch("paper_trader.fetch_single_price", return_value={"price": 10.0}), \
             mock.patch("paper_trader.fetch_prices", return_value={}), \
             mock.patch("paper_trader._append_trade", return_value=None):
            trades = auto_trade_from_recommendations(enriched, verbose=False)
        trade_codes = [t.get("code") for t in trades]
        assert "600001" not in trade_codes, "低流动性票不应模拟买入"
        assert "600002" in trade_codes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
