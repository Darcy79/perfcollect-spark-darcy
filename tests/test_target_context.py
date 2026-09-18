# -*- coding: utf-8 -*-
"""TargetContext 热切换一致性回归测试。"""

import os
import sys
import threading
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from sampling import MetricMailbox  # noqa: E402
from target_context import TargetContext  # noqa: E402


class _Resolver:
    def __init__(self, proc_name, pid=None):
        self.proc_name = proc_name
        self.pid = pid


class TestTargetContext(unittest.TestCase):
    def setUp(self):
        self.old_sampler = object()
        self.therm = object()
        self.mailbox = MetricMailbox({"fps": 8})
        self.context = TargetContext(
            "old.pkg", "old-pattern", _Resolver("old.proc", pid=101), 101,
            {"fps": self.old_sampler, "therm": self.therm},
        )

    def test_initial_state_and_sampler_share_generation(self):
        sampler, generation = self.context.capture_sampler("fps")
        state = self.context.state()
        self.assertIs(sampler, self.old_sampler)
        self.assertEqual(generation, state.generation)
        self.assertEqual((state.package, state.pid, state.proc_name),
                         ("old.pkg", 101, "old.proc"))

    def test_late_old_target_sample_is_rejected_after_switch(self):
        _, old_generation = self.context.capture_sampler("fps")
        self.context.switch_target(
            "new.pkg", "new-pattern", _Resolver("new.proc", pid=202), 202,
            {"fps": object()}, self.mailbox,
        )
        accepted = self.context.publish_if_current(
            self.mailbox, "fps", {"fps": 12}, 1.0, old_generation)
        latest, _, _, state = self.context.snapshot_mailbox(
            self.mailbox, ("fps",), drain_keys=("fps",))
        self.assertFalse(accepted)
        self.assertEqual(latest, {})
        self.assertEqual((state.package, state.pid), ("new.pkg", 202))

    def test_switch_clears_old_mailbox_and_keeps_device_collectors(self):
        self.mailbox.publish("fps", {"fps": 30}, 1.0)
        new_sampler = object()
        state = self.context.switch_target(
            "new.pkg", "", _Resolver("new.proc"), None,
            {"fps": new_sampler}, self.mailbox,
        )
        latest, pending, _, snap = self.context.snapshot_mailbox(
            self.mailbox, ("fps",), drain_keys=("fps",))
        self.assertEqual(latest, {})
        self.assertEqual(pending["fps"], [])
        self.assertEqual(state.generation, 1)
        self.assertEqual(snap.package, "new.pkg")
        self.assertIs(self.context.capture_sampler("fps")[0], new_sampler)
        self.assertIs(self.context.capture_sampler("therm")[0], self.therm)

    def test_current_generation_sample_is_published_with_same_target(self):
        _, generation = self.context.capture_sampler("fps")
        self.assertTrue(self.context.publish_if_current(
            self.mailbox, "fps", {"fps": 60}, 2.0, generation))
        latest, _, _, state = self.context.snapshot_mailbox(
            self.mailbox, ("fps",), drain_keys=("fps",))
        self.assertEqual(latest["fps"]["value"]["fps"], 60)
        self.assertEqual(state.package, "old.pkg")

    def test_empty_package_is_rejected_without_state_change(self):
        with self.assertRaises(ValueError):
            self.context.switch_target(
                "  ", "", _Resolver("new.proc"), 202,
                {"fps": object()}, self.mailbox,
            )
        self.assertEqual(self.context.state().package, "old.pkg")

    def test_state_follows_pid_changes_detected_by_resolver(self):
        resolver = _Resolver("proc", pid=303)
        context = TargetContext("pkg", "", resolver, 101, {"fps": object()})
        self.assertEqual(context.state().pid, 303)
        resolver.pid = 404
        self.assertEqual(context.state().pid, 404)

    def test_before_switch_runs_before_new_target_becomes_visible(self):
        observed = []

        def marker(old_state):
            observed.append(("marker", old_state.package))

        state = self.context.switch_target(
            "new.pkg", "", _Resolver("new.proc", pid=202), 202,
            {"fps": object()}, self.mailbox, before_switch=marker)
        self.assertEqual(observed, [("marker", "old.pkg")])
        self.assertEqual(state.package, "new.pkg")

    def test_new_target_is_hidden_until_switch_marker_finishes(self):
        marker_entered = threading.Event()
        release_marker = threading.Event()
        snapshot_done = threading.Event()
        observed = []

        def marker(_old_state):
            marker_entered.set()
            release_marker.wait(1)

        def switch():
            self.context.switch_target(
                "new.pkg", "", _Resolver("new.proc", pid=202), 202,
                {"fps": object()}, self.mailbox, before_switch=marker)

        def snapshot():
            observed.append(self.context.state().package)
            snapshot_done.set()

        switch_thread = threading.Thread(target=switch)
        switch_thread.start()
        self.assertTrue(marker_entered.wait(0.5))
        snapshot_thread = threading.Thread(target=snapshot)
        snapshot_thread.start()
        self.assertFalse(snapshot_done.wait(0.02))
        release_marker.set()
        switch_thread.join(0.5)
        snapshot_thread.join(0.5)
        self.assertEqual(observed, ["new.pkg"])


if __name__ == "__main__":
    unittest.main()
