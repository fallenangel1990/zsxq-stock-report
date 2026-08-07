"""auto_trader.py 单元测试。"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from auto_trader import (
    RiskController,
    SignalGenerator,
    BrokerClient,
    AutoTrader,
    BLACKLIST_KEYWORDS,
    DEFAULT_BUY_SCORE_THRESHOLD,
    DEFAULT_BUY_TOTAL_SCORE,
    DEFAULT_SELL_SCORE_THRESHOLD,
    DEFAULT_MAX_DAILY_TRADES,
    DEFAULT_MAX_DAILY_LOSS_PCT,
    DEFAULT_MAX_SINGLE_POSITION_PCT,
    TRADE_DIR,
    DAILY_STATE_FILE,
)


# ──────────────────────────────────────────────────────────────
# RiskController 测试
# ──────────────────────────────────────────────────────────────
class TestRiskController:
    """风控控制器测试。"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """每个测试使用临时目录。"""
        self.tmp_dir = tmp_path
        with patch("auto_trader.TRADE_DIR", tmp_path), \
             patch("auto_trader.DAILY_STATE_FILE", tmp_path / "daily_state.json"):
            self.config = {
                "max_daily_trades": 3,
                "max_daily_loss_pct": 5.0,
                "max_single_position_pct": 20.0,
                "max_sector_pct": 40.0,
                "buy_score_threshold": 7.4,
                "buy_total_score": 7.0,
                "sell_score_threshold": 4.0,
            }
            self.risk = RiskController(self.config)

    def test_initial_state(self):
        """初始状态应允许交易。"""
        can_trade, reason = self.risk.can_trade()
        assert can_trade is True
        assert "允许" in reason

    def test_blacklist_st(self):
        """ST 股票应在黑名单中。"""
        assert self.risk.is_blacklisted("ST康美") is True
        assert self.risk.is_blacklisted("*ST华泽") is True

    def test_blacklist_normal(self):
        """正常股票不应在黑名单中。"""
        assert self.risk.is_blacklisted("贵州茅台") is False
        assert self.risk.is_blacklisted("比亚迪") is False

    def test_blacklist_empty(self):
        """空名称不应在黑名单中。"""
        assert self.risk.is_blacklisted("") is False

    def test_daily_trade_limit(self):
        """达到日内交易限额后应拒绝。"""
        for i in range(3):
            self.risk.record_trade({"action": "buy", "code": f"60000{i}"})
        can_trade, reason = self.risk.can_trade()
        assert can_trade is False
        assert "上限" in reason

    def test_circuit_breaker_drawdown(self):
        """回撤超过阈值应触发熔断。"""
        triggered = self.risk.check_drawdown(950_000, 1_000_000)
        assert triggered is True
        assert self.risk.daily_state["circuit_breaker_triggered"] is True

    def test_circuit_breaker_not_triggered(self):
        """回撤未达阈值不应触发熔断。"""
        triggered = self.risk.check_drawdown(960_000, 1_000_000)
        assert triggered is False
        assert self.risk.daily_state["circuit_breaker_triggered"] is False

    def test_circuit_breaker_blocks_trading(self):
        """熔断触发后应拒绝交易。"""
        self.risk.check_drawdown(900_000, 1_000_000)
        can_trade, reason = self.risk.can_trade()
        assert can_trade is False
        assert "熔断" in reason

    def test_get_status(self):
        """状态报告应包含关键字段。"""
        status = self.risk.get_status()
        assert "trade_count" in status
        assert "max_trades" in status
        assert "circuit_breaker" in status
        assert status["max_trades"] == 3


# ──────────────────────────────────────────────────────────────
# SignalGenerator 测试
# ──────────────────────────────────────────────────────────────
class TestSignalGenerator:
    """信号生成器测试。"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.tmp_dir = tmp_path
        with patch("auto_trader.TRADE_DIR", tmp_path), \
             patch("auto_trader.DAILY_STATE_FILE", tmp_path / "daily_state.json"):
            self.risk = RiskController({
                "buy_score_threshold": 7.4,
                "buy_total_score": 7.0,
                "sell_score_threshold": 4.0,
            })
            self.gen = SignalGenerator(self.risk)

    def test_buy_signal(self):
        """可执行清单 + 高 buy_score 应生成买入信号。"""
        stocks = [{
            "code": "600519",
            "name": "贵州茅台",
            "score": 7.5,
            "buy_score": 8.0,
            "decision_tier": "可执行清单",
        }]
        signals = self.gen.generate_signals(stocks, [])
        assert len(signals["buy"]) == 1
        assert signals["buy"][0]["code"] == "600519"

    def test_buy_signal_low_score(self):
        """评分不足的买入候选应被跳过。"""
        stocks = [{
            "code": "600519",
            "name": "贵州茅台",
            "score": 5.0,
            "buy_score": 5.0,
            "decision_tier": "观察清单",
        }]
        signals = self.gen.generate_signals(stocks, [])
        assert len(signals["buy"]) == 0

    def test_sell_signal_low_score(self):
        """持仓中评分低于阈值应生成卖出信号。"""
        stocks = [{
            "code": "600519",
            "name": "贵州茅台",
            "score": 3.5,
            "buy_score": 3.0,
            "decision_tier": "信息清单",
        }]
        positions = [{"code": "600519", "usable": 100}]
        signals = self.gen.generate_signals(stocks, positions)
        assert len(signals["sell"]) == 1

    def test_hold_signal(self):
        """持仓中评分达标应持有。"""
        stocks = [{
            "code": "600519",
            "name": "贵州茅台",
            "score": 6.5,
            "buy_score": 6.0,
            "decision_tier": "观察清单",
        }]
        positions = [{"code": "600519", "usable": 100}]
        signals = self.gen.generate_signals(stocks, positions)
        assert len(signals["hold"]) == 1

    def test_blacklist_skipped(self):
        """黑名单股票应被跳过。"""
        stocks = [{
            "code": "600519",
            "name": "ST康美",
            "score": 8.0,
            "buy_score": 8.5,
            "decision_tier": "可执行清单",
        }]
        signals = self.gen.generate_signals(stocks, [])
        assert len(signals["skip"]) == 1
        assert "黑名单" in signals["skip"][0].get("skip_reason", "")

    def test_sell_excluded_stock(self):
        """决策层级降为剔除应生成卖出信号。"""
        stocks = [{
            "code": "600519",
            "name": "贵州茅台",
            "score": 5.0,
            "buy_score": 5.0,
            "decision_tier": "剔除/暂不买入",
        }]
        positions = [{"code": "600519", "usable": 100}]
        signals = self.gen.generate_signals(stocks, positions)
        assert len(signals["sell"]) == 1

    def test_buy_sorted_by_buy_score(self):
        """买入信号应按 buy_score 降序排列。"""
        stocks = [
            {"code": "000001", "name": "A", "score": 7.0, "buy_score": 7.5, "decision_tier": "可执行清单"},
            {"code": "000002", "name": "B", "score": 8.0, "buy_score": 8.5, "decision_tier": "可执行清单"},
            {"code": "000003", "name": "C", "score": 7.5, "buy_score": 7.4, "decision_tier": "可执行清单"},
        ]
        signals = self.gen.generate_signals(stocks, [])
        assert len(signals["buy"]) == 3
        assert signals["buy"][0]["buy_score"] == 8.5
        assert signals["buy"][-1]["buy_score"] == 7.4


# ──────────────────────────────────────────────────────────────
# SignalGenerator 精选/流动性测试
# ──────────────────────────────────────────────────────────────
class TestSignalGeneratorSelectivity:
    """Tests for SignalGenerator using selectivity/liquidity data."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """每个测试使用临时目录，避免写库副作用。"""
        with patch("auto_trader.TRADE_DIR", tmp_path), \
             patch("auto_trader.DAILY_STATE_FILE", tmp_path / "daily_state.json"):
            yield

    def _make_risk(self):
        from auto_trader import RiskController
        return RiskController({
            "buy_score_threshold": 7.4, "buy_total_score": 7.0,
            "sell_score_threshold": 4.0, "liquidity_gate": True,
        })

    def test_low_liquidity_skipped_from_buy(self):
        from auto_trader import SignalGenerator
        risk = self._make_risk()
        gen = SignalGenerator(risk)
        stock = {
            "code": "600001", "name": "低流动性股", "score": 7.5, "buy_score": 8.0,
            "decision_tier": "可执行清单", "market_cap_yi": 30.0,  # <50亿
        }
        signals = gen.generate_signals([stock], [])
        assert len(signals["buy"]) == 0, "低流动性票不应进买入"
        assert len(signals["skip"]) == 1
        assert "流动性" in signals["skip"][0].get("skip_reason", "")

    def test_buy_sorted_by_selectivity_score(self):
        from auto_trader import SignalGenerator
        risk = self._make_risk()
        gen = SignalGenerator(risk)
        stocks = [
            {"code": "600001", "name": "A", "score": 7.5, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0, "selectivity_score": 3.0},
            {"code": "600002", "name": "B", "score": 7.6, "buy_score": 7.5,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0, "selectivity_score": 4.5},
        ]
        signals = gen.generate_signals(stocks, [])
        buy_names = [s["name"] for s in signals["buy"]]
        assert buy_names == ["B", "A"], "精选分高的应排前面（B 4.5 > A 3.0）"

    def test_selectivity_missing_falls_back_to_buy_score(self):
        from auto_trader import SignalGenerator
        risk = self._make_risk()
        gen = SignalGenerator(risk)
        stocks = [
            {"code": "600001", "name": "A", "score": 7.5, "buy_score": 8.0,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0},  # 无 selectivity
            {"code": "600002", "name": "B", "score": 7.6, "buy_score": 7.5,
             "decision_tier": "可执行清单", "market_cap_yi": 100.0},
        ]
        signals = gen.generate_signals(stocks, [])
        buy_names = [s["name"] for s in signals["buy"]]
        assert buy_names == ["A", "B"], "无精选分时回退 buy_score 排序（A 8.0 > B 7.5）"


# ──────────────────────────────────────────────────────────────
# BrokerClient 测试（mock）
# ──────────────────────────────────────────────────────────────
class TestBrokerClient:
    """券商客户端测试（mock）。"""

    def test_connect_without_easytrader(self):
        """未安装 easytrader 时应返回 False。"""
        client = BrokerClient("eb")
        with patch.dict("sys.modules", {"easytrader": None}):
            result = client.connect()
            assert result is False

    def test_buy_without_connection(self):
        """未连接时买入应返回失败。"""
        client = BrokerClient("eb")
        result = client.buy("600519", 1800, 100)
        assert result["success"] is False
        assert "未连接" in result["message"]

    def test_sell_without_connection(self):
        """未连接时卖出应返回失败。"""
        client = BrokerClient("eb")
        result = client.sell("600519", 1800, 100)
        assert result["success"] is False


# ──────────────────────────────────────────────────────────────
# 配置加载测试
# ──────────────────────────────────────────────────────────────
class TestConfig:
    """配置加载测试。"""

    def test_default_config(self):
        """默认配置应有所有必要字段。"""
        from auto_trader import load_trading_config
        config = load_trading_config()
        assert "broker" in config
        assert "mode" in config
        assert "risk" in config
        assert config["mode"] == "semi"
        assert config["risk"]["max_daily_trades"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
