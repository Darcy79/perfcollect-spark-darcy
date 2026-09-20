# -*- coding: utf-8 -*-
"""TargetSwitcher 热切换编排回归测试。"""

import json
import os
import sys
import tempfile
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from sampling import MetricMailbox  # noqa: E402
from target_context import TargetContext  # noqa: E402
from target_switcher import TargetSwitcher  # noqa: E402


class _Resolver:
    def __init__(self, adb, package, pattern):
        self.proc_name = package + ":proc"
        self.pid = 202 if package != "missing.pkg" else None

    def resolve(self):
        return self.pid


class _Writer:
    def __init__(self):
        self.events = []
        self.flushed = 0

    def write(self, event):
        self.events.append(event)

    def flush(self):
        self.flushed += 1


class _Web:
    def __init__(self):
        self.cleared = 0
        self.status = None

    def clear_latest(self):
        self.cleared += 1

    def set_status(self, **status):
        self.status = status


class TestTargetSwitcher(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.config_path = os.path.join(self.tempdir.name, "config.json")
        self.therm = object()
        self.context = TargetContext(
            "old.pkg", "old", _Resolver(None, "old.pkg", "old"), 101,
            {"fps": object(), "therm": self.therm})
        self.mailbox = MetricMailbox({"fps": 8})
        self.writer = _Writer()
        self.web = _Web()
        self.logs = []

    def _switcher(self):
        return TargetSwitcher(
            object(), {"package": "old.pkg"}, self.config_path,
            self.context, self.mailbox, self.writer, self.web,
            resolver_factory=_Resolver,
            collectors_factory=lambda adb, pkg, pat, resolver: {"fps": pkg},
            clock=lambda: 123.4567, log=self.logs.append)

    def test_success_updates_config_context_web_and_event(self):
        ok, message = self._switcher().apply(" new.pkg ", "appbrand2")
        self.assertTrue(ok)
        self.assertEqual(message, "目标已切换为 new.pkg")
        with open(self.config_path, encoding="utf-8") as stream:
            config = json.load(stream)
        self.assertEqual(config["package"], "new.pkg")
        self.assertEqual(config["process_pattern"], "appbrand2")
        self.assertEqual(self.context.state().package, "new.pkg")
        self.assertIs(self.context.capture_sampler("therm")[0], self.therm)
        self.assertEqual(self.writer.events, [{
            "ts": 123.457, "event": "target_switch", "to": "new.pkg",
            "process_pattern": "appbrand2"}])
        self.assertEqual(self.writer.flushed, 1)
        self.assertEqual(self.web.cleared, 1)
        self.assertEqual(self.web.status["target"], "new.pkg")

    def test_empty_package_has_no_side_effect(self):
        ok, message = self._switcher().apply("  ")
        self.assertFalse(ok)
        self.assertEqual(message, "包名为空")
        self.assertFalse(os.path.exists(self.config_path))
        self.assertEqual(self.context.state().package, "old.pkg")
        self.assertEqual(self.writer.events, [])

    def test_missing_process_switches_but_returns_actionable_message(self):
        ok, message = self._switcher().apply("missing.pkg")
        self.assertTrue(ok)
        self.assertIn("未找到进程", message)
        self.assertIsNone(self.web.status["pid"])

    def test_factory_failure_is_reported_without_web_mutation(self):
        switcher = self._switcher()
        switcher.collectors_factory = lambda *args: (_ for _ in ()).throw(RuntimeError("boom"))
        ok, message = switcher.apply("new.pkg")
        self.assertFalse(ok)
        self.assertEqual(message, "切换失败: boom")
        self.assertEqual(self.web.cleared, 0)
        self.assertEqual(self.writer.events, [])


if __name__ == "__main__":
    unittest.main()
