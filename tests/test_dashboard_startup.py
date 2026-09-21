# -*- coding: utf-8 -*-
"""离线历史看板的端口策略与浏览器启动回归。"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

COLLECTOR = os.path.join(os.path.dirname(__file__), "..", "collector")
sys.path.insert(0, os.path.abspath(COLLECTOR))

import dashboard  # noqa: E402
from web import WebServer, open_browser_when_ready  # noqa: E402


class _FakeServer:
    occupied = set()

    def __init__(self, port, output_dir):
        self.port = port
        self.output_dir = output_dir
        self.stopped = False

    def start(self):
        if self.port in self.occupied:
            raise OSError("occupied")
        return self.port

    def stop(self):
        self.stopped = True


class DashboardStartupTests(unittest.TestCase):
    def test_port_candidates_distinguish_default_and_explicit(self):
        implicit = dashboard.port_candidates(8128, explicit=False)
        self.assertEqual([implicit[i] for i in range(3)], [8128, 8129, 8130])
        self.assertEqual(dashboard.port_candidates(8128, explicit=True), (8128,))

    def test_default_port_falls_forward_after_real_bind_failure(self):
        _FakeServer.occupied = {8128}
        server, port = dashboard.start_dashboard_server(
            8128, False, "unused", server_factory=_FakeServer)
        self.assertEqual(port, 8129)
        self.assertEqual(server.port, 8129)

    def test_explicit_port_never_falls_forward(self):
        _FakeServer.occupied = {8128}
        with self.assertRaises(dashboard.DashboardPortError) as raised:
            dashboard.start_dashboard_server(
                8128, True, "unused", server_factory=_FakeServer)
        self.assertEqual(raised.exception.port, 8128)

    def test_parser_preserves_whether_port_was_explicit(self):
        self.assertIsNone(dashboard.build_parser().parse_args([]).port)
        self.assertEqual(
            dashboard.build_parser().parse_args(["--port", "8128"]).port, 8128)
        self.assertTrue(
            dashboard.build_parser().parse_args(["--no-browser"]).no_browser)

    def test_explicit_conflict_is_human_readable_without_traceback(self):
        error = dashboard.DashboardPortError(8128, OSError("occupied"))
        output = io.StringIO()
        with mock.patch.object(dashboard, "start_dashboard_server", side_effect=error):
            with contextlib.redirect_stdout(output):
                code = dashboard.main(["--port", "8128", "--no-browser"])
        text = output.getvalue()
        self.assertEqual(code, 2)
        self.assertIn("[ERROR] 端口 8128 已被占用", text)
        self.assertNotIn("Traceback", text)

    def test_browser_opens_actual_page_only_after_http_response(self):
        with tempfile.TemporaryDirectory() as output_dir:
            server = WebServer(port=0, output_dir=output_dir)
            port = server.start()
            opened = []
            try:
                thread = open_browser_when_ready(
                    port, "/report.html", timeout=2, opener=opened.append)
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
                self.assertEqual(
                    opened, [f"http://localhost:{port}/report.html"])
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
