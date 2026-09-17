# -*- coding: utf-8 -*-
"""main.py 配置优先级与采集节奏校验。"""

import os
import sys
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from main import resolve_capture_timing  # noqa: E402


class TestResolveCaptureTiming(unittest.TestCase):
    def test_config_values_are_used_when_cli_is_absent(self):
        interval, duration = resolve_capture_timing(
            None, None, {"interval_ms": 500, "duration_s": 120})
        self.assertEqual(interval, 0.5)
        self.assertEqual(duration, 120.0)

    def test_cli_values_override_config(self):
        interval, duration = resolve_capture_timing(
            2.0, 30.0, {"interval_ms": 500, "duration_s": 120})
        self.assertEqual(interval, 2.0)
        self.assertEqual(duration, 30.0)

    def test_defaults_match_existing_behavior(self):
        self.assertEqual(resolve_capture_timing(None, None, {}), (1.0, 0.0))

    def test_zero_duration_means_manual_stop(self):
        self.assertEqual(resolve_capture_timing(None, None, {"duration_s": 0})[1], 0.0)

    def test_non_positive_interval_is_rejected(self):
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "采样间隔必须大于 0"):
                    resolve_capture_timing(None, None, {"interval_ms": value})

    def test_negative_duration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "采集时长不能小于 0"):
            resolve_capture_timing(None, None, {"duration_s": -1})

    def test_non_numeric_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "采样间隔必须是数字"):
            resolve_capture_timing(None, None, {"interval_ms": "fast"})
        with self.assertRaisesRegex(ValueError, "采集时长必须是数字"):
            resolve_capture_timing(None, None, {"duration_s": "forever"})


if __name__ == "__main__":
    unittest.main()
