# -*- coding: utf-8 -*-
"""时间轴区间标注持久化回归。"""

import json
import os
import sys
import tempfile
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from timeline_annotations import AnnotationStore  # noqa: E402
from export_report import export_html  # noqa: E402


class TestAnnotationStore(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.report = os.path.join(self.tempdir.name, "run", "capture.jsonl")
        os.makedirs(os.path.dirname(self.report))
        with open(self.report, "w", encoding="utf-8") as stream:
            stream.write('{"t_ms":0}\n')
        self.store = AnnotationStore(self.tempdir.name)
        self.name = "run/capture.jsonl"

    def test_create_load_and_delete_round_trip(self):
        item = self.store.create(self.name, 1000, 3500, "  进入 战斗\n阶段 ", "#ff7043")
        self.assertEqual((item["start_ms"], item["end_ms"]), (1000, 3500))
        self.assertEqual(item["text"], "进入 战斗 阶段")
        self.assertEqual(self.store.load(self.name), [item])
        self.assertTrue(self.store.delete(self.name, item["id"]))
        self.assertEqual(self.store.load(self.name), [])

    def test_one_click_pin_rotates_color_closes_previous_and_renames(self):
        first, items = self.store.pin(self.name, 1000)
        self.assertEqual(items[0]["text"], "Label1")
        self.assertEqual(items[0]["color"], "#ef5664")
        self.assertEqual((items[0]["start_ms"], items[0]["end_ms"]), (0, 1000))
        self.assertEqual(first["text"], "Label2")
        self.assertEqual(first["color"], "#4fc3f7")
        self.assertIsNone(first["end_ms"])

        second, items = self.store.pin(self.name, 3500)
        self.assertEqual(items[1]["end_ms"], 3500)
        self.assertEqual(second["text"], "Label3")
        self.assertEqual(second["color"], "#66bb6a")
        self.assertIsNone(second["end_ms"])

        renamed, items = self.store.rename(self.name, items[0]["id"], "  Boss 战\n阶段 ")
        self.assertEqual(renamed["text"], "Boss 战 阶段")
        self.assertEqual(items[0]["text"], "Boss 战 阶段")

    def test_pin_rejects_same_sample_and_unknown_rename(self):
        self.store.pin(self.name, 1000)
        with self.assertRaises(ValueError):
            self.store.pin(self.name, 1000)
        with self.assertRaises(ValueError):
            self.store.rename(self.name, "missing", "name")

    def test_sidecar_uses_versioned_object_and_atomic_result(self):
        self.store.create(self.name, 1, 2, "A", "#4fc3f7")
        path = os.path.splitext(self.report)[0] + ".annotations.json"
        with open(path, encoding="utf-8") as stream:
            payload = json.load(stream)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(len(payload["annotations"]), 1)
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_invalid_input_and_path_traversal_are_rejected(self):
        bad = [
            (0, 0, "x", "#ffffff"),
            (10, 5, "x", "#ffffff"),
            (0, 1, "", "#ffffff"),
            (0, 1, "x", "red"),
        ]
        for args in bad:
            with self.assertRaises(ValueError):
                self.store.create(self.name, *args)
        with self.assertRaises(ValueError):
            self.store.load("../escape.jsonl")

    def test_corrupt_sidecar_is_treated_as_empty(self):
        path = os.path.splitext(self.report)[0] + ".annotations.json"
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("{broken")
        self.assertEqual(self.store.load(self.name), [])

    def test_self_contained_report_embeds_annotations(self):
        item = self.store.create(self.name, 1000, 3500, "Boss 战", "#ff7043")
        html_path = os.path.splitext(self.report)[0] + ".html"
        rows = [
            {"t_ms": 0, "fps": {"fps": 60, "frame_p50_ms": 16.7}},
            {"t_ms": 4000, "fps": {"fps": 55, "frame_p50_ms": 18.0}},
        ]
        export_html(rows, html_path)
        with open(html_path, encoding="utf-8") as stream:
            html = stream.read()
        self.assertIn(item["text"], html)
        self.assertIn(item["color"], html)
        self.assertIn("renderAnnotations", html)
        self.assertIn("renderLabelTimeline", html)


if __name__ == "__main__":
    unittest.main()
