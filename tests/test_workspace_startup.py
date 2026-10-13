# -*- coding: utf-8 -*-
"""统一工作台的端口策略与浏览器启动回归。"""

import contextlib
import errno
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

COLLECTOR = os.path.join(os.path.dirname(__file__), "..", "collector")
sys.path.insert(0, os.path.abspath(COLLECTOR))

import workspace  # noqa: E402
from types import SimpleNamespace
from web import WebServer, open_browser_when_ready  # noqa: E402


class _FakeServer:
    occupied = set()

    def __init__(self, port, output_dir):
        self.port = port
        self.output_dir = output_dir
        self.stopped = False

    def start(self):
        if self.port in self.occupied:
            raise OSError(errno.EADDRINUSE, "occupied")
        return self.port

    def stop(self):
        self.stopped = True


class WorkspaceStartupTests(unittest.TestCase):
    def test_port_candidates_distinguish_default_and_explicit(self):
        implicit = workspace.port_candidates(8128, explicit=False)
        self.assertEqual([implicit[i] for i in range(3)], [8128, 8129, 8130])
        self.assertEqual(workspace.port_candidates(8128, explicit=True), (8128,))

    def test_default_port_falls_forward_after_real_bind_failure(self):
        _FakeServer.occupied = {8128}
        server, port = workspace.start_workspace_server(
            8128, False, "unused", server_factory=_FakeServer)
        self.assertEqual(port, 8129)
        self.assertEqual(server.port, 8129)

    def test_explicit_port_never_falls_forward(self):
        _FakeServer.occupied = {8128}
        with self.assertRaises(workspace.WorkspacePortError) as raised:
            workspace.start_workspace_server(
                8128, True, "unused", server_factory=_FakeServer)
        self.assertEqual(raised.exception.port, 8128)

    def test_parser_preserves_whether_port_was_explicit(self):
        import main
        for arguments, expected in [([], None), (["--port", "8128"], 8128)]:
            with mock.patch.object(sys, "argv", ["main.py", "--web", "--no-browser", *arguments]), \
                 mock.patch("workspace.run_workspace") as start:
                main.main()
                self.assertEqual(start.call_args.args[0].port, expected)
                self.assertTrue(start.call_args.args[0].no_browser)

    def test_explicit_conflict_is_human_readable_without_traceback(self):
        error = workspace.WorkspacePortError(8128)
        output = io.StringIO()
        args = SimpleNamespace(port=8128, output="unused", no_browser=True)
        with mock.patch.object(workspace, "start_workspace_server", side_effect=error):
            with contextlib.redirect_stdout(output):
                code = workspace.run_workspace(args, mock.Mock())
        self.assertEqual(code, 2)
        self.assertIn("8128", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_permission_failure_does_not_scan_more_ports(self):
        factory = mock.Mock()
        factory.return_value.start.side_effect = PermissionError(errno.EACCES, "denied")
        with self.assertRaises(PermissionError):
            workspace.start_workspace_server(8080, False, "unused", server_factory=factory)
        factory.assert_called_once()
        factory.return_value.stop.assert_called_once()

    def test_real_occupied_port_falls_forward_and_keeps_actual_port(self):
        with tempfile.TemporaryDirectory() as directory:
            occupied = WebServer(port=0, output_dir=directory)
            port = occupied.start()
            if port == 65535:
                occupied.stop()
                self.skipTest("No subsequent port")
            second = None
            try:
                second, actual = workspace.start_workspace_server(port, False, directory)
                self.assertGreater(actual, port)
                self.assertEqual(second.port, actual)
            finally:
                if second:
                    second.stop()
                occupied.stop()

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
