# -*- coding: utf-8 -*-
"""高频指标 mailbox 回归测试。"""

import os
import sys
import threading
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from sampling import (MetricMailbox, SampleAggregator, SamplerScheduler,
                      describe_metric_item, sample_metric_once, sampler_error,
                      serialize_windows, summarize_fps_windows)  # noqa: E402


class TestMetricMailbox(unittest.TestCase):
    def test_previous_jank_window_is_not_overwritten_by_latest(self):
        box = MetricMailbox({"fps": 4})
        first = {"fps": 30.0, "jank_rate": 0.5, "frame_p95_ms": 80.0}
        second = {"fps": 60.0, "jank_rate": 0.0, "frame_p95_ms": 16.7}
        box.publish("fps", first, 100.5)
        box.publish("fps", second, 101.0)

        latest, pending, dropped = box.snapshot(["fps"], drain_keys=("fps",))
        self.assertEqual(latest["fps"]["value"], second)
        self.assertEqual([item["value"] for item in pending["fps"]], [first, second])
        self.assertEqual(dropped["fps"], 0)


class TestSummarizeFpsWindows(unittest.TestCase):
    def test_jank_is_weighted_and_frame_percentiles_use_window_peak(self):
        items = [
            {"value": {"jank_count": 5, "jank_total": 10,
                       "frame_p95_ms": 80.0, "frame_max_ms": 120.0}},
            {"value": {"jank_count": 0, "jank_total": 20,
                       "frame_p95_ms": 16.7, "frame_max_ms": 20.0}},
        ]
        self.assertEqual(summarize_fps_windows(items), {
            "window_count": 2,
            "fresh_window_count": 2,
            "jank_count": 5,
            "jank_total": 30,
            "jank_rate": 0.1667,
            "frame_p95_peak_ms": 80.0,
            "frame_max_peak_ms": 120.0,
        })

    def test_reused_frame_stats_are_not_counted_as_fresh_window(self):
        items = [
            {"value": {"jank_count": 1, "jank_total": 4,
                       "frame_p95_ms": 50.0, "frame_max_ms": 90.0}},
            {"value": {"jank_count": 0, "jank_total": 0,
                       "frame_p95_ms": 50.0, "frame_max_ms": 90.0}},
        ]
        summary = summarize_fps_windows(items)
        self.assertEqual(summary["fresh_window_count"], 1)
        self.assertEqual(summary["jank_rate"], 0.25)
        self.assertEqual(summary["frame_p95_peak_ms"], 50.0)

    def test_empty_windows_have_no_summary(self):
        self.assertIsNone(summarize_fps_windows([]))

    def test_drain_clears_pending_but_keeps_latest(self):
        box = MetricMailbox({"fps": 4})
        box.publish("fps", {"fps": 60.0}, 1.0)
        box.snapshot(["fps"], drain_keys=("fps",))
        latest, pending, _ = box.snapshot(["fps"], drain_keys=("fps",))
        self.assertEqual(latest["fps"]["value"]["fps"], 60.0)
        self.assertEqual(pending["fps"], [])

    def test_bounded_queue_reports_dropped_count(self):
        box = MetricMailbox({"fps": 2})
        for i in range(4):
            box.publish("fps", {"fps": i}, float(i))
        _, pending, dropped = box.snapshot(["fps"], drain_keys=("fps",))
        self.assertEqual([item["value"]["fps"] for item in pending["fps"]], [2, 3])
        self.assertEqual(dropped["fps"], 2)

    def test_clear_drops_old_target_values_without_resetting_sequence(self):
        box = MetricMailbox({"fps": 4})
        self.assertEqual(box.publish("fps", {"target": "old"}, 1.0), 1)
        box.clear()
        latest, pending, _ = box.snapshot(["fps"], drain_keys=("fps",))
        self.assertEqual(latest, {})
        self.assertEqual(pending["fps"], [])
        self.assertEqual(box.publish("fps", {"target": "new"}, 2.0), 2)

    def test_serialized_windows_have_relative_time_and_data(self):
        items = [{"seq": 7, "sampled_at": 10.25, "value": {"jank_rate": 0.5}}]
        self.assertEqual(serialize_windows(items, 10.0), [{
            "seq": 7,
            "sampled_t_ms": 250.0,
            "data": {"jank_rate": 0.5},
        }])

    def test_concurrent_publish_keeps_every_sequence(self):
        box = MetricMailbox({"fps": 500})

        def publish_batch(worker_id):
            for i in range(100):
                box.publish("fps", {"worker": worker_id, "i": i}, worker_id + i / 1000.0)

        threads = [threading.Thread(target=publish_batch, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        latest, pending, dropped = box.snapshot(["fps"], drain_keys=("fps",))
        seqs = [item["seq"] for item in pending["fps"]]
        self.assertEqual(len(seqs), 400)
        self.assertEqual(sorted(seqs), list(range(1, 401)))
        self.assertEqual(latest["fps"]["seq"], 400)
        self.assertEqual(dropped["fps"], 0)


class TestMetricFreshness(unittest.TestCase):
    def test_fresh_item_has_relative_sample_time_and_age(self):
        meta = describe_metric_item(
            {"seq": 7, "sampled_at": 10.25, "value": {}}, 10.5, 10.0)
        self.assertEqual(meta, {
            "seq": 7, "sampled_at_ms": 250.0, "age_ms": 250.0,
            "is_reused": False,
        })

    def test_same_sequence_is_marked_reused(self):
        meta = describe_metric_item(
            {"seq": 7, "sampled_at": 10.25, "value": {}}, 11.0, 10.0,
            previous_seq=7)
        self.assertTrue(meta["is_reused"])
        self.assertEqual(meta["age_ms"], 750.0)

    def test_clock_skew_does_not_create_negative_times(self):
        meta = describe_metric_item(
            {"seq": 1, "sampled_at": 9.0, "value": {}}, 8.0, 10.0)
        self.assertEqual(meta["sampled_at_ms"], 0.0)
        self.assertEqual(meta["age_ms"], 0.0)


class _Sampler:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.received_at = None

    def sample(self, sampled_at):
        self.received_at = sampled_at
        if self.error is not None:
            raise self.error
        return self.value


class _TargetContext:
    def __init__(self, sampler, generation=3):
        self.sampler = sampler
        self.generation = generation

    def capture_sampler(self, key):
        return self.sampler, self.generation

    def publish_if_current(self, mailbox, key, value, sampled_at, generation):
        if generation != self.generation:
            return False
        mailbox.publish(key, value, sampled_at)
        return True


class TestSampleMetricOnce(unittest.TestCase):
    def test_collector_receives_start_but_mailbox_uses_completion_time(self):
        sampler = _Sampler({"fps": 60})
        clock = iter((10.0, 10.25)).__next__
        box = MetricMailbox()
        result = sample_metric_once(_TargetContext(sampler), box, "fps", clock)
        latest, _, _ = box.snapshot(("fps",))
        self.assertEqual(sampler.received_at, 10.0)
        self.assertEqual(latest["fps"]["sampled_at"], 10.25)
        self.assertEqual(result["completed_at"], 10.25)

    def test_unexpected_exception_uses_standard_error_contract(self):
        sampler = _Sampler(error=RuntimeError("adb exploded"))
        clock = iter((20.0, 20.1)).__next__
        box = MetricMailbox()
        result = sample_metric_once(_TargetContext(sampler), box, "mem", clock)
        self.assertEqual(result["value"], {
            "error": "mem_exception", "detail": "adb exploded"})
        latest, _, _ = box.snapshot(("mem",))
        self.assertEqual(latest["mem"]["value"]["error"], "mem_exception")

    def test_sampler_error_is_visible_to_row_error_checks(self):
        error = sampler_error("net", ValueError("bad output"))
        self.assertEqual(error["error"], "net_exception")
        self.assertEqual(error["detail"], "bad output")


class _RecordingSession:
    def __init__(self):
        self.stopping = False
        self.workers = []
        self.waits = []

    @property
    def is_stopping(self):
        return self.stopping

    def start_worker(self, target, args=(), name=None):
        self.workers.append((target, args, name))
        return name

    def wait(self, timeout):
        self.waits.append(timeout)
        return self.stopping


class TestSamplerScheduler(unittest.TestCase):
    def test_start_registers_one_named_worker_per_metric(self):
        session = _RecordingSession()
        scheduler = SamplerScheduler(
            session, object(), object(), {"fps": 0.5, "cpu": 1.0})

        threads = scheduler.start()

        self.assertEqual(threads, ["sampler-fps", "sampler-cpu"])
        self.assertEqual(
            [(args, name) for _, args, name in session.workers],
            [(('fps', 0.5), 'sampler-fps'), (('cpu', 1.0), 'sampler-cpu')],
        )

    def test_worker_preserves_elapsed_time_compensation(self):
        session = _RecordingSession()

        def sample_once(target_context, mailbox, key):
            session.stopping = True
            return {"started_at": 10.0}

        scheduler = SamplerScheduler(
            session, object(), object(), {"fps": 1.0},
            sample_once=sample_once, clock=lambda: 10.3)
        scheduler._run_metric("fps", 1.0)

        self.assertEqual(len(session.waits), 1)
        self.assertAlmostEqual(session.waits[0], 0.7)

    def test_start_does_nothing_after_stop(self):
        session = _RecordingSession()
        session.stopping = True
        scheduler = SamplerScheduler(
            session, object(), object(), {"fps": 0.5})

        self.assertEqual(scheduler.start(), [])
        self.assertEqual(session.workers, [])


class TestSampleAggregator(unittest.TestCase):
    def test_build_marks_reused_latest_without_changing_metric_value(self):
        aggregator = SampleAggregator(10.0)
        latest = {
            "mem": {"seq": 3, "sampled_at": 10.5,
                    "value": {"pss_kb": 1024}},
        }

        first = aggregator.build(11.0, "com.example.game", latest)
        second = aggregator.build(12.0, "com.example.game", latest)

        self.assertEqual(first["ts"], 11.0)
        self.assertEqual(first["t_ms"], 1000.0)
        self.assertEqual(first["target"], "com.example.game")
        self.assertEqual(first["mem"], {"pss_kb": 1024})
        self.assertFalse(first["metric_meta"]["mem"]["is_reused"])
        self.assertTrue(second["metric_meta"]["mem"]["is_reused"])
        self.assertEqual(second["metric_meta"]["mem"]["age_ms"], 1500.0)

    def test_build_preserves_fps_windows_summary_and_dropped_count(self):
        aggregator = SampleAggregator(100.0)
        windows = [
            {"seq": 1, "sampled_at": 100.5,
             "value": {"jank_count": 2, "jank_total": 10,
                       "frame_p95_ms": 40.0, "frame_max_ms": 70.0}},
            {"seq": 2, "sampled_at": 101.0,
             "value": {"jank_count": 1, "jank_total": 20,
                       "frame_p95_ms": 30.0, "frame_max_ms": 50.0}},
        ]

        row = aggregator.build(
            101.0, "pkg", {}, {"fps": windows}, {"fps": 4})

        self.assertEqual([item["sampled_t_ms"] for item in row["fps_windows"]],
                         [500.0, 1000.0])
        self.assertEqual(row["fps_window_summary"]["jank_count"], 3)
        self.assertEqual(row["fps_window_summary"]["jank_total"], 30)
        self.assertEqual(row["fps_window_summary"]["frame_p95_peak_ms"], 40.0)
        self.assertEqual(row["fps_windows_dropped"], 4)


if __name__ == "__main__":
    unittest.main()
