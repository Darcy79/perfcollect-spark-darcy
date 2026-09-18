# -*- coding: utf-8 -*-
"""唯一 JSONL writer 的并发、flush 与关闭语义测试。"""

import json
import os
import sys
import tempfile
import threading
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from jsonl_writer import JsonlWriter  # noqa: E402


class TestJsonlWriter(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "capture.jsonl")

    def tearDown(self):
        self.tempdir.cleanup()

    def _rows(self):
        with open(self.path, encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    def test_meta_remains_first_and_unicode_is_preserved(self):
        writer = JsonlWriter(self.path).start()
        writer.write({"event": "meta", "name": "荣耀"})
        writer.write({"value": 1})
        writer.close()
        self.assertEqual(self._rows(), [
            {"event": "meta", "name": "荣耀"}, {"value": 1}])

    def test_close_drains_all_concurrent_writes(self):
        writer = JsonlWriter(self.path).start()

        def publish(worker):
            for index in range(50):
                writer.write({"worker": worker, "index": index})

        threads = [threading.Thread(target=publish, args=(worker,))
                   for worker in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        writer.close()
        rows = self._rows()
        self.assertEqual(len(rows), 200)
        self.assertEqual(
            {(row["worker"], row["index"]) for row in rows},
            {(worker, index) for worker in range(4) for index in range(50)},
        )

    def test_flush_makes_rows_visible_before_close(self):
        writer = JsonlWriter(self.path).start()
        writer.write({"value": 1})
        writer.flush()
        self.assertEqual(self._rows(), [{"value": 1}])
        writer.close()

    def test_flush_flag_preserves_order(self):
        writer = JsonlWriter(self.path).start()
        first = writer.write({"value": 1})
        second = writer.write({"value": 2}, flush=True)
        # flush=True 返回时两条记录已经对其他读取者可见。
        self.assertEqual(self._rows(), [{"value": 1}, {"value": 2}])
        writer.close()
        self.assertEqual((first, second), (1, 2))
        self.assertEqual(self._rows(), [{"value": 1}, {"value": 2}])
        self.assertEqual(writer.written_count, 2)

    def test_write_after_close_is_rejected(self):
        writer = JsonlWriter(self.path).start()
        writer.close()
        with self.assertRaises(RuntimeError):
            writer.write({"late": True})
        writer.close()

    def test_invalid_record_fails_before_queueing(self):
        writer = JsonlWriter(self.path).start()
        with self.assertRaises(TypeError):
            writer.write({"bad": object()})
        writer.write({"valid": True})
        writer.close()
        self.assertEqual(self._rows(), [{"valid": True}])

    def test_open_failure_does_not_leave_writer_thread_running(self):
        bad_path = os.path.join(self.tempdir.name, "missing", "capture.jsonl")
        writer = JsonlWriter(bad_path)
        with self.assertRaises(OSError):
            writer.start()
        self.assertFalse(writer._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
