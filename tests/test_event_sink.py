# -*- coding: utf-8 -*-
"""logcat 事件 sink 与 monitor 停止收口回归。"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from event_sink import JsonlEventSink, stop_and_drain_event_capture  # noqa: E402
from logcat import LogcatMonitor  # noqa: E402


class TestJsonlEventSink(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "capture.events.jsonl")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_zero_events_do_not_create_file(self):
        sink = JsonlEventSink(self.path)
        self.assertEqual(sink.write_many([]), 0)
        sink.close()
        self.assertFalse(os.path.exists(self.path))

    def test_batch_write_flushes_unicode_and_tracks_count(self):
        sink = JsonlEventSink(self.path)
        events = [
            {"t_ms": 1, "text": "进入商城"},
            {"t_ms": 2, "text": "battle"},
        ]
        self.assertEqual(sink.write_many(events), 2)
        self.assertEqual(sink.count, 2)
        with open(self.path, encoding="utf-8") as stream:
            self.assertEqual([json.loads(line) for line in stream], events)
        sink.close()

    def test_close_is_idempotent_and_rejects_late_write(self):
        sink = JsonlEventSink(self.path)
        sink.close()
        sink.close()
        with self.assertRaises(RuntimeError):
            sink.write_many([{"text": "late"}])


class _FakeAdb:
    serial = "serial"


class _FakeProcess:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


class TestLogcatMonitorStop(unittest.TestCase):
    def test_stop_terminates_process_and_joins_reader(self):
        monitor = LogcatMonitor(_FakeAdb())
        process = _FakeProcess()
        monitor._proc = process

        def reader():
            while not monitor._stop:
                time.sleep(0.001)

        thread = threading.Thread(target=reader)
        monitor._thread = thread
        monitor.started = True
        thread.start()

        monitor.stop(timeout=0.5)

        self.assertTrue(process.terminated)
        self.assertFalse(thread.is_alive())
        self.assertFalse(monitor.started)

    def test_stop_then_final_drain_preserves_tail_event(self):
        order = []

        class Monitor:
            def stop(self):
                order.append("stop")

            def get_events(self):
                order.append("drain")
                return [{"text": "tail"}]

        class Sink:
            def write_many(self, events):
                order.append(("write", events))

            def close(self):
                order.append("close")

        stop_and_drain_event_capture(Monitor(), Sink())
        self.assertEqual(order, [
            "stop", "drain", ("write", [{"text": "tail"}]), "close",
        ])
