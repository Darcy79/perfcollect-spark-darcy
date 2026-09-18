# -*- coding: utf-8 -*-
"""采样控制台输出格式回归。"""

from datetime import datetime
import os
import sys
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from console_output import format_sample_status  # noqa: E402


NOW = datetime(2026, 9, 17, 14, 5, 6)


class TestFormatSampleStatus(unittest.TestCase):
    def test_full_sample_keeps_existing_console_contract(self):
        row = {
            "fps": {"fps": 59.8, "jank_rate": 0.02},
            "cpu": {"cpu_total_pct": 31.2, "cpu_proc_pct": 8.4},
            "mem": {"pss_kb": 102400},
            "net": {"rx_kbps": 12.3, "tx_kbps": 4.5},
            "therm": {"temp_c": 38.5},
        }
        self.assertEqual(
            format_sample_status(row, NOW),
            "[14:05:06] FPS=59.8 Jank%=0.02 CPU总%=31.2 CPU进程%=8.4 "
            "PSS=102400kB ↓12.3/↑4.5KB/s 温度=38.5°C",
        )

    def test_known_fps_errors_keep_user_facing_labels(self):
        cases = {
            "no_layer": "无渲染层(游戏请在微信前台)",
            "probe_fail": "渲染层读取失败(链路抖动)",
            "layer_read_fail": "渲染层失效,重匹配中",
        }
        for error, label in cases.items():
            with self.subTest(error=error):
                text = format_sample_status({"fps": {"error": error}}, NOW)
                self.assertIn(f"FPS={label}", text)

        # 历史契约：未识别的 error 若仍带有 fps，优先展示数值。
        text = format_sample_status(
            {"fps": {"error": "future_warning", "fps": 55.0}}, NOW)
        self.assertIn("FPS=55.0", text)

    def test_missing_and_throttled_values_keep_placeholders(self):
        text = format_sample_status({"mem": {"throttled": True}}, NOW)
        self.assertEqual(
            text,
            "[14:05:06] FPS=- Jank%=- CPU总%=- CPU进程%=- "
            "PSS=(节流)kB ↓-/↑-KB/s 温度=-°C",
        )
