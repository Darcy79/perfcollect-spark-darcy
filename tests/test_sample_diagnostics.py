import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
import sample_diagnostics as diag


class TestSampleDiagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.report = Path(self.tmp.name) / "run.jsonl"
        self.sample = Path(self.tmp.name) / "run.samples.jsonl"
        self.report.write_text(json.dumps({"ts": 101, "t_ms": 1000}) + "\n", encoding="utf-8")

    def load(self, rows):
        self.sample.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return diag.load_diagnostics(self.report, self.sample)

    def cpu(self, **kwargs):
        return {"event": "metric_sample", "metric": "cpu", "backend": "cpu_probe", "target_generation": 2,
                "timing": {"host_received_at": 100.25}, "data": {"pid": 3, "cpu_proc_pct": 88, "cpu_total_pct": 12}, **kwargs}

    def test_host_time_and_values(self):
        row = self.load([self.cpu()])["cpu"][0]
        self.assertEqual(row["t_ms"], 250)
        self.assertEqual(row["cpu_proc_pct"], 88)
        self.assertEqual(row["pid"], 3)

    def test_query_completion_and_missing_values(self):
        row = self.load([self.cpu(timing={"host_completed_at": 101}, data={"cpu_proc_pct": float("nan"), "cpu_total_pct": True})])["cpu"][0]
        self.assertEqual(row["t_ms"], 1000)
        self.assertIsNone(row["cpu_proc_pct"])
        self.assertIsNone(row["cpu_total_pct"])
        row = self.load([self.cpu(seq={"malformed": "x" * 1000}, target_generation=[], backend="x" * 1000)])["cpu"][0]
        self.assertIsNone(row["seq"])
        self.assertIsNone(row["target_generation"])
        self.assertEqual(len(row["backend"]), 64)

    def test_no_anchor_is_explicit(self):
        self.report.write_text('{"event":"meta","ts":100}\n', encoding="utf-8")
        result = self.load([self.cpu()])
        self.assertFalse(result["anchor_available"])
        self.assertIsNone(result["cpu"][0]["t_ms"])
        self.assertEqual(result["counts"]["untimed_samples"], 1)

    def test_long_frames_keep_ns_as_strings(self):
        result = self.load([{"event": "metric_sample", "metric": "fps", "timing": {"host_completed_at": 102},
            "frames": {"available": True, "timestamps_ns": ["9007199254740999", "9007199314740999", "9007199334740999"],
                       "intervals_ns": [None, "60000000", "20000000"], "coverage_flags": ["buffer_no_overlap"]}}])
        self.assertEqual(len(result["long_frames"]), 1)
        self.assertEqual(result["long_frames"][0]["device_present_ns"], "9007199314740999")
        self.assertEqual(result["long_frames"][0]["interval_ms"], 60)
        self.assertEqual(result["counts"]["coverage_windows"], 1)

    def test_retains_latest_cpu_and_reports_total(self):
        with patch.object(diag, "CPU_LIMIT", 2):
            result = self.load([self.cpu(seq=i) for i in range(4)])
        self.assertEqual([r["seq"] for r in result["cpu"]], [2, 3])
        self.assertEqual(result["counts"]["cpu"], 4)

    def test_partial_tail_is_visible(self):
        self.sample.write_bytes(b'{"event":"meta"}\n{"event":')
        result = diag.load_diagnostics(self.report, self.sample)
        self.assertFalse(result["scan"]["complete"])

    def test_corruption_is_counted_and_scan_continues(self):
        self.sample.write_text('invalid\n' + json.dumps(self.cpu()) + '\n', encoding="utf-8")
        result = diag.load_diagnostics(self.report, self.sample)
        self.assertEqual(result["scan"]["invalid_rows"], 1)
        self.assertEqual(len(result["cpu"]), 1)

    def test_scan_budget_is_bounded(self):
        self.sample.write_bytes(b'{"event":"meta"}\n' * 10)
        state = {"bytes": 0, "complete": True, "invalid_rows": 0}
        list(diag.records(self.sample, state, budget=40))
        self.assertLessEqual(state["bytes"], 40)
        self.assertFalse(state["complete"])

    def test_line_budget_is_bounded(self):
        self.sample.write_bytes(b' ' * 100 + b'\n')
        state = {"bytes": 0, "complete": True, "invalid_rows": 0}
        with patch.object(diag, "LINE_LIMIT", 32):
            list(diag.records(self.sample, state))
        self.assertLessEqual(state["bytes"], 33)
        self.assertFalse(state["complete"])
