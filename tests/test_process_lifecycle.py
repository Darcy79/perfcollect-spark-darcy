# -*- coding: utf-8 -*-
"""目标进程生命周期的保守判定回归。"""

import os
import sys
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from process_lifecycle import ProcessLifecycleTracker  # noqa: E402


class TestProcessLifecycleTracker(unittest.TestCase):
    def test_stable_pid_and_initial_missing_do_not_emit(self):
        tracker = ProcessLifecycleTracker("pkg", 10)
        self.assertIsNone(tracker.update("pkg", 10, 1000))
        empty = ProcessLifecycleTracker("pkg", None)
        self.assertIsNone(empty.update("pkg", None, 1000))
        self.assertIsNone(empty.update("pkg", 10, 2000))

    def test_exit_is_reported_once_without_claiming_crash(self):
        tracker = ProcessLifecycleTracker("pkg", 10)
        event = tracker.update("pkg", None, 1000)
        self.assertEqual(event["kind"], "process_exit")
        self.assertEqual(event["reason"], "unknown")
        self.assertNotIn("闪退", event["text"])
        self.assertIsNone(tracker.update("pkg", None, 2000))

    def test_reappearance_and_direct_pid_change_are_restarts(self):
        tracker = ProcessLifecycleTracker("pkg", 10)
        tracker.update("pkg", None, 1000)
        event = tracker.update("pkg", 20, 2000)
        self.assertEqual((event["kind"], event["old_pid"], event["pid"]),
                         ("process_restart", 10, 20))
        direct = tracker.update("pkg", 30, 3000)
        self.assertEqual((direct["old_pid"], direct["pid"]), (20, 30))

    def test_target_switch_resets_baseline_without_false_restart(self):
        tracker = ProcessLifecycleTracker("old.pkg", 10)
        self.assertIsNone(tracker.update("new.pkg", 20, 1000))
        self.assertIsNone(tracker.update("new.pkg", 20, 2000))


if __name__ == "__main__":
    unittest.main()
