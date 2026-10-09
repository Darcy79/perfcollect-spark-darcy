"""Sampling configuration and monotonic scheduling regression tests."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
from sampling_config import DEFAULT_INTERVALS, load_sampling_intervals, resolve_sampling_intervals, sampling_descriptor
from sampling import SamplerScheduler
from sample_recording import SampleRecording

class Session:
    def __init__(self, rounds=2):
        self.is_stopping = False
        self.rounds = rounds
        self.waits = []
    def request_stop(self):
        self.is_stopping = True
    def wait(self, delay):
        self.waits.append(delay)
        if len(self.waits) >= self.rounds:
            self.request_stop()

class TestSamplingConfig(unittest.TestCase):
    def test_baseline_is_independent(self):
        values = load_sampling_intervals()
        self.assertEqual(values, {"fps": .5, "cpu": 1, "mem": 2, "net": 1, "therm": 2})
        values["cpu"] = 50
        self.assertEqual(DEFAULT_INTERVALS["cpu"], 1)

    def test_cpu_override_does_not_change_other_channels(self):
        overrides = {"cpu": .5}
        self.assertEqual(resolve_sampling_intervals(overrides), {**DEFAULT_INTERVALS, "cpu": .5})
        self.assertEqual(overrides, {"cpu": .5})

    def test_invalid_values_are_rejected(self):
        for value in ([], True, {"CPU": 1}, {"cpu": True}, {"cpu": "0.5"}, {"cpu": None},
                      {"cpu": 10 ** 500}, {"cpu": 0}, {"cpu": float("nan")}, {"cpu": float("inf")}, {"cpu": -1}, {"cpu": 61}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                resolve_sampling_intervals(value)

    def test_query_limits(self):
        for key, value in (("cpu", .25), ("fps", .25), ("mem", 1), ("therm", 1), ("net", .5)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                resolve_sampling_intervals({key: value})

    def test_read_only_bom_config_and_failures(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sampling.json"
            path.write_text('{"cpu": 0.5}', encoding="utf-8-sig")
            original = path.read_bytes()
            self.assertEqual(load_sampling_intervals(path)["cpu"], .5)
            self.assertEqual(path.read_bytes(), original)
            path.write_text("null", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_sampling_intervals(path)
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_sampling_intervals(path)
            with self.assertRaises(OSError):
                load_sampling_intervals(Path(folder) / "missing.json")

    def test_capability_descriptor(self):
        info = sampling_descriptor(resolve_sampling_intervals({"cpu": .5}), 2)
        self.assertEqual(info["aggregate_interval_s"], 2)
        self.assertTrue(info["experimental_cpu_cadence"])
        self.assertFalse(info["streaming"])
        self.assertFalse(info["device_clock_aligned"])
        self.assertEqual(info["backend"], "adb_query")
        self.assertEqual(sampling_descriptor(DEFAULT_INTERVALS, 1)["profile"], "baseline")

    def test_invalid_config_rejected_before_device_and_output(self):
        import main
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "sampling.json"
            output = Path(folder) / "output"
            config.write_text('{"cpu": 0.01}', encoding="utf-8")
            with patch.object(sys, "argv", ["main.py", "--sampling-config", str(config), "--output", str(output)]), patch.object(main, "load_config", return_value={}), patch.object(main, "Adb") as adb, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main.main()
                self.assertEqual(raised.exception.code, 2)
                adb.assert_not_called()
            self.assertFalse(output.exists())

    def test_sidecar_metadata_and_final_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "samples.jsonl"
            info = sampling_descriptor(DEFAULT_INTERVALS, 1)
            recording = SampleRecording(path, sampling_info=info).start()
            runtime = {"metrics": {"cpu": {"samples": 2}}, "failures": {}}
            recording.close(sampling_runtime=runtime)
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["sampling"], info)
            self.assertEqual(rows[-1]["sampling_runtime"], runtime)

class TestSchedulerRuntime(unittest.TestCase):
    def test_wall_clock_jump_does_not_affect_schedule(self):
        session = Session()
        wall = iter([1e9, -1e9])
        scheduler = SamplerScheduler(session, None, None, {"cpu": 1}, sample_once=lambda *args: {"started_at": next(wall), "published": True}, clock=iter([100, 100.2, 101.4, 102.8]).__next__)
        scheduler._run_metric("cpu", 1)
        self.assertAlmostEqual(session.waits[0], .8)
        self.assertEqual(session.waits[1], .05)
        stats = scheduler.snapshot()["metrics"]["cpu"]
        self.assertEqual((stats["samples"], stats["published"], stats["overruns"]), (2, 2, 1))
        self.assertEqual(stats["period_count"], 1)
        self.assertAlmostEqual(stats["period_total_s"], 1.4)
        self.assertAlmostEqual(stats["work_total_s"], 1.6)

    def test_worker_failure_stops_and_propagates(self):
        session = Session()
        def broken(*args):
            raise OSError("publication failed")
        scheduler = SamplerScheduler(session, None, None, {"cpu": 1}, sample_once=broken)
        scheduler._run_metric("cpu", 1)
        self.assertTrue(session.is_stopping)
        self.assertEqual(session.waits, [])
        with self.assertRaisesRegex(RuntimeError, "publication failed"):
            scheduler.raise_if_failed()

    def test_snapshot_isolation_and_rejected_sample_count(self):
        session = Session(rounds=1)
        scheduler = SamplerScheduler(session, None, None, {"cpu": 1}, sample_once=lambda *args: {"published": False})
        scheduler._run_metric("cpu", 1)
        first = scheduler.snapshot()
        self.assertEqual(first["metrics"]["cpu"]["published"], 0)
        first["metrics"]["cpu"]["samples"] = 99
        self.assertEqual(scheduler.snapshot()["metrics"]["cpu"]["samples"], 1)

    def test_invalid_scheduler_budgets(self):
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SamplerScheduler(Session(), None, None, {"cpu": value})
        with self.assertRaises(ValueError):
            SamplerScheduler(Session(), None, None, {"cpu": 1}, minimum_delay=0)
