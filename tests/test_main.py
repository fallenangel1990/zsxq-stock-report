"""main.py quant 命令（量化交易闭环编排器）单元测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestQuantCommand:
    """Tests for quant closed-loop command."""

    def test_quant_orchestrator_exists(self):
        import main
        assert hasattr(main, "cmd_quant"), "main 应有 cmd_quant 编排函数"

    def test_quant_report_generation(self, tmp_path):
        # 用 mock 数据验证 quant 能产出决策报告
        from unittest import mock
        import main
        enriched = [
            {"code": "600001", "name": "A", "score": 8.0, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0,
             "selectivity_score": 5.0, "liquidity_score": 6.0},
        ]
        with mock.patch("auto_trader.TRADE_DIR", tmp_path), \
             mock.patch("auto_trader.DAILY_STATE_FILE", tmp_path / "daily_state.json"), \
             mock.patch("main.extract_stock_opportunities", create=True, return_value="mock report"), \
             mock.patch("storage.save_enriched_stocks", return_value=""), \
             mock.patch("storage.append_recommendation_history", return_value=""), \
             mock.patch("auto_trader.AutoTrader.run", return_value={
                 "signals": {"buy_list": [], "sell_list": [], "buy_count": 0, "sell_count": 0,
                             "hold_count": 0, "skip_count": 1},
                 "executed": [], "risk_status": {},
                 "mode": "semi", "time": "2026-08-07T09:30:00"}), \
             mock.patch("backtester.run_backtest", return_value={"error": "无推荐历史数据"}):
            report = main._build_quant_report(enriched, None, "2026-08-07")
        assert "量化交易决策报告" in report
        assert "精选候选" in report
