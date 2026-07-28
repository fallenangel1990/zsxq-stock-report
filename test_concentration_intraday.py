"""集中度盘中预警测试。"""
import pytest
from unittest.mock import patch
import concentration_monitor as cm


class TestConcentrationIntraday:
    def _snapshot(self, level):
        return {
            "level": level,
            "timestamp": "2026-07-29T14:30:00+08:00",
            "signals": {
                "flow_concentration": {
                    "value": 0.73,
                    "level": "danger",
                    "top3": [
                        {"name": "半导体", "share": 0.35},
                        {"name": "AI", "share": 0.22},
                    ],
                },
                "turnover_concentration": {"value": 0.58, "level": "elevated", "top3": []},
                "breadth_divergence": {"value": 0.55, "level": "danger",
                                       "index_change": 0.012, "advance_decline_ratio": 0.55},
            },
            "summary": "资金高度集中",
        }

    @patch("concentration_monitor.compute_concentration")
    def test_no_alert_on_normal(self, mock_compute):
        mock_compute.return_value = self._snapshot("normal")
        from intraday_monitor import _check_concentration
        alerts = _check_concentration({"concentration_last": {"level": "normal"}})
        assert alerts == []

    @patch("concentration_monitor.compute_concentration")
    def test_alert_on_upgrade(self, mock_compute):
        """等级从 normal 升到 danger → 推送预警。"""
        mock_compute.return_value = self._snapshot("danger")
        from intraday_monitor import _check_concentration
        alerts = _check_concentration({"concentration_last": {"level": "normal"}})
        assert len(alerts) == 1
        assert alerts[0]["type"] == "concentration"
        assert "半导体" in alerts[0]["msg"]

    @patch("concentration_monitor.compute_concentration")
    def test_no_repeat_same_level(self, mock_compute):
        """同等级不重复推送。"""
        mock_compute.return_value = self._snapshot("danger")
        from intraday_monitor import _check_concentration
        state = {"concentration_last": {"level": "danger"}}
        alerts = _check_concentration(state)
        assert alerts == []

    @patch("concentration_monitor.compute_concentration")
    def test_state_updated(self, mock_compute):
        """调用后 state 更新为当前等级。"""
        mock_compute.return_value = self._snapshot("elevated")
        from intraday_monitor import _check_concentration
        state = {"concentration_last": {"level": "normal"}}
        _check_concentration(state)
        assert state["concentration_last"]["level"] == "elevated"

    @patch("concentration_monitor.compute_concentration")
    def test_daily_dedup(self, mock_compute):
        """同等级当天已推送过 → 不重复推送。"""
        mock_compute.return_value = self._snapshot("danger")
        from intraday_monitor import _check_concentration
        state = {"concentration_last": {
            "level": "elevated",
            "last_push_level": "danger",
            "last_push_date": "2026-07-29",  # 今天
        }}
        alerts = _check_concentration(state)
        assert alerts == []
