# -*- coding: utf-8 -*-
"""采集收尾 CSV 与 HTML 产物的离线回归。"""

import csv
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from report_outputs import finalize_capture_outputs  # noqa: E402
from export_report import export_csv_atomic  # noqa: E402


class TestReportOutputs(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.report = os.path.join(self.tempdir.name, "perfcollect_20260928_120000.jsonl")
        with open(self.report, "w", encoding="utf-8") as stream:
            for row in (
                {"event": "meta", "cores": 8},
                {"t_ms": 0, "fps": {"fps": 60}},
                {"t_ms": 1000, "fps": {"fps": 58}},
            ):
                stream.write(json.dumps(row) + "\n")

    def test_auto_csv_has_header_plus_sample_rows_and_is_repeatable(self):
        first = finalize_capture_outputs(self.report, emit=lambda _: None)
        self.assertTrue(os.path.isfile(first["html"]))
        with open(first["csv"], encoding="utf-8-sig", newline="") as stream:
            before = list(csv.reader(stream))
        self.assertEqual(len(before), 3)
        second = finalize_capture_outputs(self.report, emit=lambda _: None)
        with open(second["csv"], encoding="utf-8-sig", newline="") as stream:
            self.assertEqual(list(csv.reader(stream)), before)
        self.assertFalse(any(name.endswith(".tmp") for name in os.listdir(self.tempdir.name)))

    def test_no_csv_skips_automatic_generation(self):
        result = finalize_capture_outputs(
            self.report, generate_csv=False, emit=lambda _: None)
        self.assertIsNone(result["csv"])
        self.assertFalse(os.path.exists(self.report[:-6] + ".csv"))
        self.assertTrue(os.path.isfile(result["html"]))

    def test_csv_failure_keeps_existing_file_and_marks_html(self):
        csv_path = self.report[:-6] + ".csv"
        with open(csv_path, "wb") as stream:
            stream.write(b"existing")
        messages = []
        with patch("report_outputs.export_csv_atomic", side_effect=OSError("磁盘不可写")):
            result = finalize_capture_outputs(self.report, emit=messages.append)
        self.assertTrue(result["csv_failed"])
        with open(csv_path, "rb") as stream:
            self.assertEqual(stream.read(), b"existing")
        with open(result["html"], encoding="utf-8") as stream:
            self.assertIn("CSV 未生成", stream.read())
        self.assertTrue(any("CSV 未生成: 磁盘不可写" in msg for msg in messages))

    def test_partial_csv_write_cannot_replace_previous_complete_file(self):
        csv_path = self.report[:-6] + ".csv"
        with open(csv_path, "wb") as stream:
            stream.write(b"previous complete")

        def partial_write(_rows, temporary):
            with open(temporary, "wb") as stream:
                stream.write(b"partial")
            raise OSError("写入中断")

        with patch("export_report.export_csv", side_effect=partial_write):
            with self.assertRaisesRegex(OSError, "写入中断"):
                export_csv_atomic([{"t_ms": 0}], csv_path)
        with open(csv_path, "rb") as stream:
            self.assertEqual(stream.read(), b"previous complete")
        self.assertEqual(sorted(os.listdir(self.tempdir.name)),
                         ["perfcollect_20260928_120000.csv",
                          "perfcollect_20260928_120000.jsonl"])


if __name__ == "__main__":
    unittest.main()
