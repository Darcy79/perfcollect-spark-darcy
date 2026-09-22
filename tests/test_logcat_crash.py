# -*- coding: utf-8 -*-
"""目标关联的 Java/Native 崩溃、ANR 与系统回收证据解析。"""

import os
import sys
import unittest
from datetime import datetime

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from logcat import LogcatMonitor  # noqa: E402


class _Adb:
    serial = "serial"


class TestLogcatCrashEvidence(unittest.TestCase):
    def setUp(self):
        self.monitor = LogcatMonitor(
            _Adb(), target_package="com.example.game", target_pid=123)
        self.monitor._anchor = datetime(2026, 9, 22, 10, 0, 0).timestamp()
        self.monitor._anchor_year = 2026

    def parse(self, pid, level, tag, text, millis="100"):
        return self.monitor._parse(
            f"09-22 10:00:00.{millis} {pid:5d} {pid:5d} {level} {tag}: {text}\n")

    def test_java_fatal_and_following_stack_are_captured(self):
        fatal = self.parse(123, "E", "AndroidRuntime", "FATAL EXCEPTION: main")
        stack = self.parse(123, "E", "AndroidRuntime", "java.lang.IllegalStateException: boom")
        self.assertEqual((fatal["kind"], fatal["crash_type"]),
                         ("confirmed_crash", "java"))
        self.assertEqual(stack["kind"], "crash_log")
        self.assertEqual(fatal["target"], "com.example.game")

    def test_other_process_fatal_is_rejected(self):
        self.assertIsNone(self.parse(
            999, "E", "AndroidRuntime", "FATAL EXCEPTION: main"))

    def test_native_fatal_signal_is_confirmed_for_target_pid(self):
        event = self.parse(
            123, "F", "libc", "Fatal signal 11 (SIGSEGV), code 1, fault addr 0x0")
        self.assertEqual((event["kind"], event["crash_type"]),
                         ("confirmed_crash", "native"))

    def test_package_matched_anr_and_lmk_kill_are_distinct(self):
        anr = self.parse(1000, "E", "ActivityManager", "ANR in com.example.game")
        killed = self.parse(
            1000, "I", "lmkd", "Killing 'com.example.game' (123), oom_score_adj 900")
        self.assertEqual(anr["kind"], "anr")
        self.assertEqual(killed["kind"], "system_kill")

    def test_process_died_is_evidence_but_not_labeled_crash(self):
        event = self.parse(
            1000, "I", "ActivityManager", "Process com.example.game (pid 123) has died")
        self.assertEqual(event["kind"], "process_log")
        self.assertNotEqual(event["kind"], "confirmed_crash")

    def test_console_events_remain_wechat_only(self):
        line = "[INFO:CONSOLE(1)] scene entered"
        self.assertIsNone(self.parse(123, "I", "chromium", line))
        self.monitor.update_target("com.tencent.mm", 321)
        event = self.parse(321, "I", "chromium", line)
        self.assertEqual(event["kind"], "app_log")

    def test_wechat_system_event_must_match_selected_appbrand_process(self):
        self.monitor.update_target(
            "com.tencent.mm", 321, "com.tencent.mm:appbrand1")
        other = self.parse(
            1000, "E", "ActivityManager", "ANR in com.tencent.mm:appbrand0")
        selected = self.parse(
            1000, "E", "ActivityManager", "ANR in com.tencent.mm:appbrand1")
        self.assertIsNone(other)
        self.assertEqual(selected["kind"], "anr")

    def test_identical_diagnostic_lines_are_never_throttled(self):
        first = self.parse(123, "E", "AndroidRuntime", "FATAL EXCEPTION: main")
        second = self.parse(123, "E", "AndroidRuntime", "FATAL EXCEPTION: main")
        self.assertEqual(first["kind"], "confirmed_crash")
        self.assertEqual(second["kind"], "confirmed_crash")

    def test_identical_wechat_console_lines_remain_throttled(self):
        self.monitor.update_target("com.tencent.mm", 321)
        line = "[INFO:CONSOLE(1)] repeated scene log"
        self.assertIsNotNone(self.parse(321, "I", "chromium", line))
        self.assertIsNone(self.parse(321, "I", "chromium", line))


if __name__ == "__main__":
    unittest.main()
