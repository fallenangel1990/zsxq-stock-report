"""市场资金集中度模块单元测试。"""
import pytest
from unittest.mock import patch
import concentration_monitor as cm


def _board(name, main_net_yi, amount_yi):
    """构造 mock 板块数据。"""
    return {"name": name, "main_net_yi": main_net_yi, "amount_yi": amount_yi}


class TestFlowConcentration:
    @patch("concentration_monitor.fetch_boards")
    def test_normal_when_dispersed(self, mock_boards):
        """资金分散 → normal。"""
        mock_boards.return_value = [
            _board("半导体", 10, 100),
            _board("AI", 8, 90),
            _board("机器人", 7, 80),
            _board("消费", 6, 70),
            _board("医药", 5, 60),
            _board("金融", 4, 50),
            _board("地产", 4, 40),
            _board("汽车", 3, 30),
            _board("农业", 3, 20),
            _board("纺织", 2, 10),
            _board("化工", 2, 10),
            _board("建材", 2, 10),
            _board("有色", 2, 10),
            _board("钢铁", 2, 10),
        ]
        snap = cm.compute_concentration()
        flow = snap["signals"]["flow_concentration"]
        assert flow["level"] == cm.LEVEL_NORMAL

    @patch("concentration_monitor.fetch_boards")
    def test_elevated_at_55_percent(self, mock_boards):
        """top3 = 55% → elevated。"""
        mock_boards.return_value = [
            _board("半导体", 20, 100),
            _board("AI", 20, 90),
            _board("机器人", 15, 80),
            _board("消费", 15, 70),
            _board("医药", 15, 60),
            _board("金融", 15, 50),
        ]
        snap = cm.compute_concentration()
        flow = snap["signals"]["flow_concentration"]
        assert flow["level"] == cm.LEVEL_ELEVATED
        assert flow["value"] == pytest.approx(0.55)

    @patch("concentration_monitor.fetch_boards")
    def test_danger_at_75_percent(self, mock_boards):
        """top3 = 75% → danger。"""
        mock_boards.return_value = [
            _board("半导体", 40, 100),
            _board("AI", 25, 90),
            _board("机器人", 10, 80),
            _board("消费", 10, 70),
            _board("医药", 8, 60),
            _board("金融", 7, 50),
        ]
        snap = cm.compute_concentration()
        flow = snap["signals"]["flow_concentration"]
        assert flow["level"] == cm.LEVEL_DANGER
        assert flow["value"] == pytest.approx(0.75)
        assert len(flow["top3"]) == 3
        assert flow["top3"][0]["name"] == "半导体"

    @patch("concentration_monitor.fetch_boards")
    def test_unavailable_on_fetch_failure(self, mock_boards):
        """fetch 异常 → unavailable。"""
        mock_boards.side_effect = RuntimeError("502")
        snap = cm.compute_concentration()
        assert snap["signals"]["flow_concentration"]["level"] == cm.LEVEL_UNAVAILABLE


class TestTurnoverConcentration:
    @patch("concentration_monitor.fetch_boards")
    def test_normal_when_dispersed(self, mock_boards):
        mock_boards.return_value = [
            _board("半导体", 10, 80),
            _board("AI", 8, 70),
            _board("机器人", 7, 60),
            _board("消费", 5, 60),
            _board("医药", 3, 55),
            _board("金融", 2, 55),
            _board("地产", 2, 50),
            _board("汽车", 2, 50),
        ]
        snap = cm.compute_concentration()
        to = snap["signals"]["turnover_concentration"]
        assert to["level"] == cm.LEVEL_NORMAL

    @patch("concentration_monitor.fetch_boards")
    def test_elevated_at_47_percent(self, mock_boards):
        """top3 成交额占 47% → elevated。"""
        mock_boards.return_value = [
            _board("半导体", 10, 200),
            _board("AI", 8, 150),
            _board("机器人", 7, 120),
            _board("消费", 5, 100),
            _board("医药", 3, 90),
            _board("金融", 2, 80),
            _board("地产", 2, 70),
            _board("汽车", 2, 60),
            _board("农业", 2, 50),
            _board("纺织", 2, 40),
            _board("化工", 2, 40),
        ]
        snap = cm.compute_concentration()
        to = snap["signals"]["turnover_concentration"]
        assert to["level"] == cm.LEVEL_ELEVATED
        assert to["value"] == pytest.approx(0.47, abs=0.01)

    @patch("concentration_monitor.fetch_boards")
    def test_danger_at_62_percent(self, mock_boards):
        """top3 成交额占 62% → danger。"""
        mock_boards.return_value = [
            _board("半导体", 10, 300),
            _board("AI", 8, 200),
            _board("机器人", 7, 120),
            _board("消费", 5, 100),
            _board("医药", 3, 80),
            _board("金融", 2, 70),
        ]
        snap = cm.compute_concentration()
        to = snap["signals"]["turnover_concentration"]
        assert to["level"] == cm.LEVEL_DANGER


class TestBreadthDivergence:
    @patch("concentration_monitor.fetch_market_indices")
    def test_normal_when_breadth_healthy(self, mock_indices):
        """指数涨 + 涨跌比 > 1 → normal。"""
        mock_indices.return_value = [
            {"code": "1000001", "name": "上证指数", "change_pct": 0.8,
             "up_count": 1500, "down_count": 800},
        ]
        snap = cm.compute_concentration()
        bd = snap["signals"]["breadth_divergence"]
        assert bd["level"] == cm.LEVEL_NORMAL

    @patch("concentration_monitor.fetch_market_indices")
    def test_elevated_when_ratio_below_1(self, mock_indices):
        """指数涨 + 涨跌比 < 1 → elevated。"""
        mock_indices.return_value = [
            {"code": "1000001", "name": "上证指数", "change_pct": 0.8,
             "up_count": 800, "down_count": 1000},
        ]
        snap = cm.compute_concentration()
        bd = snap["signals"]["breadth_divergence"]
        assert bd["level"] == cm.LEVEL_ELEVATED
        assert bd["advance_decline_ratio"] == pytest.approx(0.8)

    @patch("concentration_monitor.fetch_market_indices")
    def test_danger_when_ratio_below_06(self, mock_indices):
        """指数涨 + 涨跌比 < 0.6 → danger。"""
        mock_indices.return_value = [
            {"code": "1000001", "name": "上证指数", "change_pct": 1.2,
             "up_count": 550, "down_count": 1200},
        ]
        snap = cm.compute_concentration()
        bd = snap["signals"]["breadth_divergence"]
        assert bd["level"] == cm.LEVEL_DANGER

    @patch("concentration_monitor.fetch_market_indices")
    def test_no_trigger_when_index_falls(self, mock_indices):
        """指数跌 → 不触发背离（下跌集中 ≠ 追高风险）。"""
        mock_indices.return_value = [
            {"code": "1000001", "name": "上证指数", "change_pct": -0.5,
             "up_count": 600, "down_count": 1500},
        ]
        snap = cm.compute_concentration()
        bd = snap["signals"]["breadth_divergence"]
        assert bd["level"] == cm.LEVEL_NORMAL

    @patch("concentration_monitor.fetch_market_indices")
    def test_unavailable_on_fetch_failure(self, mock_indices):
        mock_indices.side_effect = RuntimeError("cookie expired")
        snap = cm.compute_concentration()
        assert snap["signals"]["breadth_divergence"]["level"] == cm.LEVEL_UNAVAILABLE
