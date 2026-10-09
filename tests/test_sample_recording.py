# -*- coding: utf-8 -*-
"""Evidence retention and frame boundaries without a device or user data."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
from capture_session import CaptureSession
from metrics.fps import FpsCollector
from sample_recording import SampleRecording, finish_sample_recording, iter_samples
from sampling import MetricMailbox, sample_metric_once
from target_context import TargetContext


class Resolver:
    pid = 42
    proc_name = "game"


class Sample:
    def sample(self, ts):
        return {"cpu_proc_pct": 25}


class TestRawFrames(unittest.TestCase):
    def setUp(self):
        self.fps = FpsCollector(None, "game")
        self.fps._set_layer("SurfaceView[game]#1")

    def test_overlap_keeps_cross_window_interval_and_deduplicates(self):
        first = self.fps._raw_frames([100, 200, 200])
        second = self.fps._raw_frames([200, 300, 500])
        self.assertEqual(first["timestamps_ns"], ["100", "200"])
        self.assertEqual(first["intervals_ns"], [None, "100"])
        self.assertEqual(second["timestamps_ns"], ["300", "500"])
        self.assertEqual(second["intervals_ns"], ["100", "200"])
        self.assertEqual(self.fps._raw_frames([200, 300, 500])["timestamps_ns"], [])

    def test_no_overlap_does_not_invent_a_measured_interval(self):
        self.fps._raw_frames([100, 200])
        batch = self.fps._raw_frames([400, 500])
        self.assertEqual(batch["intervals_ns"], [None, "100"])
        self.assertIn("buffer_no_overlap", batch["coverage_flags"])
        self.assertEqual(batch["omitted_frames"], 0)

    def test_layer_change_and_timestamp_regression_start_new_epochs(self):
        first = self.fps._raw_frames([100, 200])
        self.fps._set_layer("SurfaceView[game]#2")
        second = self.fps._raw_frames([300, 400])
        third = self.fps._raw_frames([100, 200])
        self.assertGreater(second["layer_generation"], first["layer_generation"])
        self.assertGreater(third["layer_generation"], second["layer_generation"])
        self.assertEqual(second["intervals_ns"][0], None)
        self.assertIn("timestamp_regression", third["coverage_flags"])

    def test_empty_buffer_preserves_deduplication_baseline(self):
        self.fps._raw_frames([100, 200])
        self.assertIn("empty_buffer", self.fps._raw_frames([])["coverage_flags"])
        self.assertEqual(self.fps._raw_frames([100, 200])["timestamps_ns"], [])

    def test_large_batches_are_bounded_and_loss_is_counted(self):
        batch = self.fps._raw_frames(list(range(1, 5001)))
        self.assertEqual(len(batch["timestamps_ns"]), 4096)
        self.assertEqual(batch["omitted_frames"], 904)
        self.assertEqual(batch["intervals_ns"][0], None)

    def test_native_timestamp_precision_is_preserved(self):
        stamp = 2 ** 53 + 1
        batch = self.fps._raw_frames([stamp, stamp + 16_666_667])
        self.assertEqual(batch["timestamps_ns"][0], str(stamp))
        self.assertIsNone(batch["clock_alignment"])

    def test_failed_replacement_query_cannot_relabel_old_frames(self):
        self.fps._set_layer("game/Activity#1")
        with patch.object(self.fps, "_try_resolve", side_effect=lambda ts: self.fps._set_layer("SurfaceView[game]#2")), \
                patch.object(self.fps, "_gfx_read", return_value=None):
            self.fps.adb = type("Adb", (), {})()
            self.fps.adb.shell = unittest.mock.Mock(side_effect=["16666666\n0 100000000 0", OSError("disconnected")])
            frames = self.fps.sample(1)["_raw_frames"]
        self.assertFalse(frames["available"])
        self.assertNotIn("timestamps_ns", frames)

    def test_sample_keeps_existing_jank_window_semantics(self):
        class Adb:
            output = ""
            def shell(self, command):
                return self.output
        adb = Adb()
        self.fps.adb = adb
        def read(stamps, ts):
            adb.output = "16666666\n" + "\n".join(f"0 {v} 0" for v in stamps)
            return self.fps.sample(ts)
        read([100_000_000, 116_666_667], 1)
        result = read([116_666_667, 133_333_334, 150_000_001], 2)
        self.assertEqual(result["jank_total"], 1)
        self.assertEqual(result["_raw_frames"]["intervals_ns"], ["16666667", "16666667"])


class TestSampleRecording(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "capture.samples.jsonl")
        self.box = MetricMailbox({"fps": 2})
        self.context = TargetContext("old", "", Resolver(), 42, {"cpu": Sample()})
        self.recording = SampleRecording(self.path).start()
        self.context.set_record_sink(self.recording.record)

    def finish(self):
        finish_sample_recording(CaptureSession(), self.context, self.recording)

    def rows(self):
        return [json.loads(line) for line in Path(self.path).read_text(encoding="utf-8").splitlines()]

    def test_every_sample_survives_latest_overwrite(self):
        for i in range(4):
            sample_metric_once(self.context, self.box, "cpu", iter([i, i + .1]).__next__)
        self.finish()
        rows = list(iter_samples(self.path))
        self.assertEqual([row["seq"] for row in rows], [1, 2, 3, 4])
        self.assertEqual(rows[-1]["timing"]["host_completed_at"], 3.1)
        self.assertGreaterEqual(int(rows[-1]["timing"]["duration_ns"]), 0)
        self.assertEqual(self.box.snapshot(["cpu"])[0]["cpu"]["sampled_at"], 3.1)
        self.assertEqual(self.rows()[-1]["event"], "capture_end")

    def test_target_switch_preserves_old_samples_and_rejects_stale_result(self):
        sample_metric_once(self.context, self.box, "cpu")
        self.context.switch_target("new", "", Resolver(), 42, {"cpu": Sample()}, self.box)
        self.assertFalse(self.context.publish_if_current(self.box, "cpu", {}, 1, 0))
        sample_metric_once(self.context, self.box, "cpu")
        self.finish()
        rows = list(iter_samples(self.path))
        self.assertEqual([(r["target"], r["target_generation"]) for r in rows], [("old", 0), ("new", 1)])
        self.assertEqual(self.rows()[-1]["publication"]["stale_samples_rejected"], 1)

    def test_raw_frames_do_not_enter_display_mailbox(self):
        class FpsSample:
            def sample(self, ts):
                return {"fps": 60, "_raw_frames": {"available": True, "timestamps_ns": ["123"]}}
        self.context = TargetContext("game", "", Resolver(), 42, {"fps": FpsSample()})
        self.context.set_record_sink(self.recording.record)
        sample_metric_once(self.context, self.box, "fps")
        self.finish()
        self.assertEqual(self.box.snapshot(["fps"])[0]["fps"]["value"], {"fps": 60})
        self.assertEqual(list(iter_samples(self.path))[0]["frames"]["timestamps_ns"], ["123"])

    def test_gfx_or_error_is_explicitly_unavailable_for_raw_frames(self):
        self.context = TargetContext("game", "", Resolver(), 42, {"fps": Sample()})
        self.context.set_record_sink(self.recording.record)
        sample_metric_once(self.context, self.box, "fps")
        self.finish()
        self.assertFalse(list(iter_samples(self.path))[0]["frames"]["available"])

    def test_stop_waits_for_tail_sample(self):
        session = CaptureSession()
        session.start_worker(sample_metric_once, args=(self.context, self.box, "cpu"))
        finish_sample_recording(session, self.context, self.recording)
        self.assertEqual(len(list(iter_samples(self.path))), 1)
        self.assertEqual(self.rows()[-1]["unfinished_workers"], [])

    def test_late_worker_is_sealed_and_timeout_is_recorded(self):
        started, release = threading.Event(), threading.Event()
        class Slow:
            def sample(self, ts):
                started.set()
                release.wait(3)
                return {"cpu_proc_pct": 50}
        self.context = TargetContext("game", "", Resolver(), 42, {"cpu": Slow()})
        self.context.set_record_sink(self.recording.record)
        session = CaptureSession()
        worker = session.start_worker(sample_metric_once, args=(self.context, self.box, "cpu"), name="slow")
        self.assertTrue(started.wait(2))
        try:
            finish_sample_recording(session, self.context, self.recording, timeout=.01)
        finally:
            release.set()
            worker.join(2)
        self.assertEqual(self.rows()[-1]["unfinished_workers"], ["slow"])
        self.assertEqual(list(iter_samples(self.path)), [])
        self.assertEqual(self.box.snapshot(["cpu"])[0], {})

    def test_recording_failure_reaches_main_and_close(self):
        with patch.object(self.recording.writer, "write", side_effect=OSError("disk full")):
            sample_metric_once(self.context, self.box, "cpu")
        with self.assertRaisesRegex(OSError, "disk full"):
            self.recording.check()
        with self.assertRaisesRegex(OSError, "disk full"):
            self.finish()
        self.assertFalse(self.recording.writer._thread.is_alive())
