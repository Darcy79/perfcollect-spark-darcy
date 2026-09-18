# -*- coding: utf-8 -*-
"""目标进程与渲染层错配状态机回归。"""

import os
import sys
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from target_monitor import TargetMismatchTracker  # noqa: E402


class TestTargetMismatchTracker(unittest.TestCase):
    def test_mismatch_emits_complete_event_and_status(self):
        tracker = TargetMismatchTracker()
        action = tracker.update(
            "com.tencent.mm", 123, "SurfaceView[AppBrandUI1]#7", 1, 0)

        self.assertEqual(action["kind"], "mismatch")
        self.assertEqual(action["event"], {
            "event": "target_mismatch",
            "layer": "SurfaceView[AppBrandUI1]#7",
            "layer_index": 1,
            "pid_index": 0,
            "pid": 123,
        })
        self.assertEqual(action["status"]["message"], action["message"])
        self.assertIn("AppBrandUI1", action["message"])
        self.assertIn("appbrand0", action["message"])

    def test_same_mismatch_is_deduplicated(self):
        tracker = TargetMismatchTracker()
        args = ("com.tencent.mm", 123, "AppBrandUI1#7", 1, 0)
        self.assertIsNotNone(tracker.update(*args))
        self.assertIsNone(tracker.update(*args))

    def test_matching_or_unknown_layer_clears_once(self):
        tracker = TargetMismatchTracker()
        tracker.update("com.tencent.mm", 123, "layer", 1, 0)

        self.assertEqual(
            tracker.update("com.tencent.mm", 123, "layer", 0, 0),
            {"kind": "recovered"})
        self.assertIsNone(
            tracker.update("com.tencent.mm", 123, None, None, 0))

    def test_switch_to_non_wechat_clears_stale_mismatch(self):
        tracker = TargetMismatchTracker()
        tracker.update("com.tencent.mm", 123, "layer", 1, 0)

        self.assertEqual(
            tracker.update("com.example.game", 456),
            {"kind": "recovered"})
        self.assertIsNone(
            tracker.update("com.example.game", 456))
