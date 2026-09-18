# -*- coding: utf-8 -*-
"""CaptureSession 生命周期回归测试。"""

import os
import sys
import threading
import time
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from capture_session import CaptureSession  # noqa: E402


class TestCaptureSession(unittest.TestCase):
    def test_initial_state(self):
        session = CaptureSession()
        self.assertFalse(session.is_stopping)
        self.assertFalse(session.shutdown_requested)

    def test_stop_is_idempotent_and_wakes_wait(self):
        session = CaptureSession()
        woke = threading.Event()

        def waiter():
            if session.wait(5):
                woke.set()

        thread = session.start_worker(waiter, name="waiter")
        session.request_stop()
        session.request_stop()
        thread.join(0.5)
        self.assertTrue(woke.is_set())
        self.assertFalse(thread.is_alive())

    def test_shutdown_also_requests_stop(self):
        session = CaptureSession()
        session.request_shutdown()
        self.assertTrue(session.shutdown_requested)
        self.assertTrue(session.is_stopping)

    def test_worker_cannot_start_after_stop(self):
        session = CaptureSession()
        session.request_stop()
        with self.assertRaises(RuntimeError):
            session.start_worker(lambda: None, name="late")

    def test_join_workers_reports_only_threads_still_alive(self):
        session = CaptureSession()
        release = threading.Event()
        session.start_worker(lambda: release.wait(1), name="blocked")
        alive = session.join_workers(timeout=0.01)
        self.assertEqual(alive, ["blocked"])
        release.set()
        self.assertEqual(session.join_workers(timeout=0.5), [])

    def test_wait_without_stop_observes_timeout(self):
        session = CaptureSession()
        # Windows 3.12 的 monotonic 可由 GetTickCount64 提供，分辨率仅
        # 15.625ms；用它测 10ms 等待会偶发得到 0。测试计时用 QPC。
        started = time.perf_counter()
        self.assertFalse(session.wait(0.01))
        self.assertGreaterEqual(time.perf_counter() - started, 0.005)


if __name__ == "__main__":
    unittest.main()
