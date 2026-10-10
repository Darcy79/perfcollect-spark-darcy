# -*- coding: utf-8 -*-
"""采集运行健康状态机回归。"""

import os
import sys
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from runtime_health import RuntimeHealthTracker, row_has_any_value, backoff_sleep  # noqa: E402


METRICS = ("fps", "cpu", "mem", "net", "therm")


def _row_with_errors(count):
    return {
        key: {"error": f"{key}_fail"}
        for key in METRICS[:count]
    }


class TestRuntimeHealthTracker(unittest.TestCase):
    def test_probe_requested_once_on_third_majority_failure(self):
        tracker = RuntimeHealthTracker(METRICS)

        first = tracker.update(_row_with_errors(4))
        second = tracker.update(_row_with_errors(4))
        third = tracker.update(_row_with_errors(4))
        fourth = tracker.update(_row_with_errors(4))

        self.assertEqual(
            [first["fail_streak"], second["fail_streak"],
             third["fail_streak"], fourth["fail_streak"]],
            [1, 2, 3, 4],
        )
        self.assertEqual(
            [first["probe_required"], second["probe_required"],
             third["probe_required"], fourth["probe_required"]],
            [False, False, True, False],
        )
        self.assertEqual(
            [event["kind"] for event in first["channel_events"]],
            ["disconnect"],
        )
        self.assertEqual(second["channel_events"], [])

    def test_recovery_resets_streak_and_emits_single_status_edge(self):
        tracker = RuntimeHealthTracker(METRICS)
        for _ in range(3):
            tracker.update(_row_with_errors(5))

        recovered = tracker.update({})
        steady = tracker.update({})

        self.assertEqual(recovered["fail_streak"], 0)
        self.assertTrue(recovered["recovered"])
        self.assertFalse(steady["recovered"])
        self.assertEqual(
            [event["kind"] for event in recovered["channel_events"]],
            ["recovered"],
        )

    def test_short_failure_run_resets_without_false_recovery(self):
        tracker = RuntimeHealthTracker(METRICS)
        tracker.update(_row_with_errors(4))
        tracker.update(_row_with_errors(4))

        result = tracker.update(_row_with_errors(2))

        self.assertEqual(result["fail_streak"], 0)
        self.assertFalse(result["probe_required"])
        self.assertFalse(result["recovered"])

    def test_health_check_state_is_owned_and_forwarded(self):
        seen_states = []

        def health_check(row, state):
            seen_states.append(dict(state))
            state["calls"] = state.get("calls", 0) + 1
            return ([f"alert-{state['calls']}"] if state["calls"] == 2 else []), state

        tracker = RuntimeHealthTracker(METRICS, health_check=health_check)
        first = tracker.update({})
        second = tracker.update({})

        self.assertEqual(seen_states, [{}, {"calls": 1}])
        self.assertEqual(first["health_alerts"], [])
        self.assertEqual(second["health_alerts"], ["alert-2"])


class TestIntentionalMetricPause(unittest.TestCase):
    def paused_row(self, legacy=False):
        row = {key: ({"error": f"experiment_{key}_disabled"} if legacy else
                     {"status": "paused", "reason": "experiment_disabled"})
               for key in METRICS if key != "cpu"}
        row["cpu"] = {"cpu_proc_pct": 25}
        return row

    def test_pause_never_triggers_disconnect_or_backoff(self):
        for legacy in (False, True):
            tracker = RuntimeHealthTracker(METRICS)
            for _ in range(10):
                result = tracker.update(self.paused_row(legacy))
                self.assertEqual(result["err_codes"], [])
                self.assertEqual(result["channel_events"], [])
                self.assertEqual(result["fail_streak"], 0)
                self.assertFalse(result["probe_required"])
                self.assertEqual(backoff_sleep(.5, result["fail_streak"]), .5)
                self.assertEqual(result["paused_metrics"], ["fps", "mem", "net", "therm"])

    def test_actual_failure_of_remaining_channel_still_probes(self):
        tracker = RuntimeHealthTracker(METRICS)
        row = self.paused_row()
        row["cpu"] = {"error": "device_offline"}
        results = [tracker.update(row) for _ in range(3)]
        self.assertEqual(results[0]["channel_events"][0]["kind"], "disconnect")
        self.assertTrue(results[-1]["probe_required"])
        self.assertTrue(tracker.update(self.paused_row())["recovered"])

    def test_all_paused_is_recordable_and_not_disconnected(self):
        row = {key: {"status": "paused"} for key in METRICS}
        self.assertTrue(row_has_any_value(row))
        tracker = RuntimeHealthTracker(METRICS)
        for _ in range(4):
            self.assertEqual(tracker.update(row)["fail_streak"], 0)

    def test_similar_error_and_contradictory_pause_are_real_failures(self):
        tracker = RuntimeHealthTracker(METRICS)
        row = {key: {"status": "paused", "error": "experiment_transport_failed"}
               for key in METRICS}
        self.assertEqual(len(tracker.update(row)["err_codes"]), 5)
        row = {key: {"error": "experiment_other_disabled"} for key in METRICS}
        self.assertEqual(len(tracker.update(row)["err_codes"]), 5)
