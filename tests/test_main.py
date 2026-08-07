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

    def test_quant_no_execute_end_to_end(self, tmp_path, monkeypatch):
        # 端到端：cmd_quant --no-execute 全链路 mock，验证不崩溃且报告落盘。
        # 说明：cmd_quant 内是函数级 `from X import Y` 导入，patch 源模块才能生效。
        from unittest import mock
        import main
        enriched = [
            {"code": "600001", "name": "A", "score": 8.0, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0,
             "selectivity_score": 5.0, "liquidity_score": 6.0},
        ]
        # 报告保存到 tmp 目录，保持 hermetic（cmd_quant 写 Path("data/quant")）
        monkeypatch.chdir(tmp_path)
        with mock.patch("storage.load_latest_raw", return_value=([{"content": "测试"}], "f")), \
             mock.patch("stock_extractor.extract_stock_opportunities", return_value="ok"), \
             mock.patch("storage.load_latest_stock_data", return_value=(enriched, "")), \
             mock.patch("backtester.run_backtest", return_value={"metrics": {}}), \
             mock.patch("backtester.format_backtest_report", return_value="回测摘要"), \
             mock.patch("auto_trader.TRADE_DIR", tmp_path), \
             mock.patch("auto_trader.DAILY_STATE_FILE", tmp_path / "daily_state.json"):
            class FakeArgs:
                mode = None
                no_execute = True
            main.cmd_quant(FakeArgs())
        # 报告已写入 tmp/data/quant/
        reports = list((tmp_path / "data" / "quant").glob("quant_report_*.md"))
        assert len(reports) == 1, "cmd_quant --no-execute 应生成并保存量化决策报告"
