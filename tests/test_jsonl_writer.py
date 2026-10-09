# -*- coding: utf-8 -*-
"""唯一 JSONL writer 的并发、flush 与关闭语义测试。"""

import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

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


    def test_bounded_queue_times_out_without_deadlock_or_silent_loss(self):
        entered, release = threading.Event(), threading.Event()
        class SlowStream:
            lines = []
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def write(self, line):
                entered.set()
                release.wait(3)
                self.lines.append(line)
            def flush(self):
                pass
        stream = SlowStream()
        with patch("jsonl_writer.open", return_value=stream):
            writer = JsonlWriter(self.path, capacity=1, enqueue_timeout=.02).start()
            try:
                writer.write({"n": 1})
                self.assertTrue(entered.wait(2))
                writer.write({"n": 2})
                with self.assertRaisesRegex(OSError, "capacity exceeded"):
                    writer.write({"n": 3})
                self.assertEqual(writer.stats["overflow_count"], 1)
                self.assertEqual(writer.stats["accepted"], 2)
                self.assertEqual(writer.stats["queued"], 1)
            finally:
                release.set()
                with self.assertRaises(OSError):
                    writer.close()
        self.assertFalse(writer._thread.is_alive())
        self.assertEqual([json.loads(line)["n"] for line in stream.lines], [1, 2])

    def test_flush_failure_releases_the_waiter(self):
        class BrokenStream:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def flush(self):
                raise OSError("flush failed")
        failures = []
        with patch("jsonl_writer.open", return_value=BrokenStream()):
            writer = JsonlWriter(self.path).start()
            def flush():
                try:
                    writer.flush()
                except OSError as error:
                    failures.append(str(error))
            waiter = threading.Thread(target=flush, daemon=True)
            waiter.start()
            waiter.join(2)
            with self.assertRaises(OSError):
                writer.close()
            self.assertFalse(waiter.is_alive())
        self.assertIn("flush failed", failures[0])

    def test_byte_budget_failure_is_fatal_and_visible(self):
        writer = JsonlWriter(self.path, max_record_bytes=32).start()
        with self.assertRaisesRegex(OSError, "byte budget"):
            writer.write({"text": "大" * 32})
        with self.assertRaises(OSError):
            writer.flush()
        with self.assertRaises(OSError):
            writer.close()
        self.assertEqual(self._rows(), [])

    def test_small_queue_drains_concurrent_producers_in_sequence(self):
        writer = JsonlWriter(self.path, capacity=2).start()
        accepted = []
        lock = threading.Lock()
        def publish(worker):
            for i in range(20):
                value = {"worker": worker, "i": i}
                seq = writer.write(value)
                with lock:
                    accepted.append((seq, value))
        threads = [threading.Thread(target=publish, args=(i,)) for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
        writer.close()
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(len(accepted), 60)
        self.assertEqual(self._rows(), [value for seq, value in sorted(accepted)])
        self.assertEqual(writer.stats["overflow_count"], 0)


if __name__ == "__main__":
    unittest.main()
