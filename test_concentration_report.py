"""集中度报告集成测试。"""


class TestConcentrationGauge:
    def _snapshot(self, level, flow_top3=None, turnover_val=0.3,
                  breadth_ratio=1.2, index_change=0.5):
        return {
            "level": level,
            "timestamp": "2026-07-29T14:30:00+08:00",
            "signals": {
                "flow_concentration": {
                    "value": 0.54,
                    "level": "elevated",
                    "top3": flow_top3 or [
                        {"name": "半导体", "share": 0.28},
                        {"name": "AI", "share": 0.16},
                        {"name": "机器人", "share": 0.10},
                    ],
                },
                "turnover_concentration": {
                    "value": turnover_val,
                    "level": "normal",
                    "top3": [],
                },
                "breadth_divergence": {
                    "value": breadth_ratio,
                    "level": "normal",
                    "index_change": index_change,
                    "advance_decline_ratio": breadth_ratio,
                },
            },
            "summary": "资金集中度偏高",
        }

    def test_normal_not_shown(self):
        """normal 等级不展示。"""
        from stock_extractor import _append_concentration_gauge
        parts = []
        _append_concentration_gauge(parts, self._snapshot("normal"))
        assert parts == []

    def test_unavailable_shown(self):
        """unavailable 展示数据缺失提示。"""
        from stock_extractor import _append_concentration_gauge
        parts = []
        _append_concentration_gauge(parts, self._snapshot("unavailable"))
        assert any("暂缺" in p for p in parts)

    def test_elevated_format(self):
        """elevated 展示黄色警告 + 数据行。"""
        from stock_extractor import _append_concentration_gauge
        parts = []
        _append_concentration_gauge(parts, self._snapshot("elevated"))
        text = "\n".join(parts)
        assert "偏高" in text
        assert "54%" in text
        assert "半导体" in text

    def test_breadth_line_shows_percent_correctly(self):
        """index_change 已是百分比量级，不应再乘 100。

        eg. index_change=0.8 应显示 "0.8%" 而非 "80.0%"。
        """
        from stock_extractor import _append_concentration_gauge
        parts = []
        snap = self._snapshot("elevated", index_change=0.8)
        snap["signals"]["breadth_divergence"]["level"] = "danger"
        _append_concentration_gauge(parts, snap)
        text = "\n".join(parts)
        assert "0.8%" in text
        assert "80.0%" not in text

    def test_danger_format_with_position_advice(self):
        """danger 展示红色警告 + 仓位建议。"""
        from stock_extractor import _append_concentration_gauge
        parts = []
        snap = self._snapshot("danger")
        snap["signals"]["breadth_divergence"]["level"] = "danger"
        snap["signals"]["breadth_divergence"]["advance_decline_ratio"] = 0.55
        _append_concentration_gauge(parts, snap)
        text = "\n".join(parts)
        assert "危险" in text
        assert "仓位" in text
