# -*- coding: utf-8 -*-
"""WebServer 真实 HTTP/SSE 端到端回归（仅本机随机端口）。"""

import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from web import RING_SIZE, WebServer  # noqa: E402


class TestWebHttp(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.server = WebServer(port=0, output_dir=self.tempdir.name)
        self.port = self.server.start()

    def tearDown(self):
        self.server.stop()
        if self.server._httpd:
            self.server._httpd.server_close()
        if self.server._thread:
            self.server._thread.join(1)
        self.tempdir.cleanup()

    def request(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request(method, path, headers=headers or {})
        response = conn.getresponse()
        body = response.read()
        status = response.status
        content_type = response.getheader("Content-Type")
        conn.close()
        return status, content_type, body

    def request_json(self, method, path, headers=None):
        status, content_type, body = self.request(method, path, headers)
        self.assertIn("application/json", content_type)
        return status, json.loads(body.decode("utf-8"))

    def test_status_and_latest_round_trip(self):
        self.server.set_status(running=True, target="com.example.game", pid=123)
        self.server.add_sample({"t_ms": 1000, "fps": {"fps": 60}})
        status_code, status = self.request_json("GET", "/api/status")
        latest_code, latest = self.request_json("GET", "/api/latest")
        self.assertEqual(status_code, 200)
        self.assertEqual((status["running"], status["target"], status["pid"]),
                         (True, "com.example.game", 123))
        self.assertEqual(latest_code, 200)
        self.assertEqual(latest[0]["fps"]["fps"], 60)
        self.assertEqual(latest[0]["_seq"], 1)

    def test_dashboard_report_assets_and_favicon_are_served(self):
        index_code, index_type, index = self.request("GET", "/")
        report_code, report_type, report = self.request("GET", "/report.html")
        asset_code, asset_type, asset = self.request("GET", "/assets/app.js")
        report_js_code, report_js_type, report_js = self.request(
            "GET", "/assets/report.js")
        icon_code, icon_type, icon = self.request("GET", "/favicon.ico")
        self.assertEqual(
            (index_code, report_code, asset_code, report_js_code, icon_code),
            (200, 200, 200, 200, 200))
        self.assertIn("text/html", index_type)
        self.assertIn("text/html", report_type)
        self.assertIn("javascript", asset_type)
        self.assertIn("javascript", report_js_type)
        self.assertIn("image/svg+xml", icon_type)
        self.assertIn(b"<!DOCTYPE html", index)
        self.assertIn(b"<!DOCTYPE html", report)
        self.assertTrue(asset)
        self.assertIn(b"PerfCollect", report_js)
        self.assertIn(b"<svg", icon)

    def test_stop_and_shutdown_callbacks_are_reached_over_http(self):
        stopped = threading.Event()
        shutdown = threading.Event()
        self.server.set_stop_callback(stopped.set)
        self.server.set_shutdown_callback(shutdown.set)
        stop_code, stop_body = self.request_json("POST", "/api/stop")
        shutdown_code, shutdown_body = self.request_json("POST", "/api/shutdown")
        self.assertEqual((stop_code, shutdown_code), (200, 200))
        self.assertTrue(stop_body["ok"])
        self.assertTrue(shutdown_body["ok"])
        self.assertTrue(stopped.is_set())
        self.assertTrue(shutdown.is_set())

    def test_switch_target_validates_and_calls_callback(self):
        calls = []

        def switch(package, pattern):
            calls.append((package, pattern))
            return True, "switched"

        self.server.set_switch_callback(switch)
        query = urlencode({"package": "com.example.game", "process_pattern": ":render"})
        code, body = self.request_json("POST", "/api/switch-target?" + query)
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(calls, [("com.example.game", ":render")])

    def test_offline_target_device_apps_candidates_and_start(self):
        self.server.set_status(
            running=False, target="com.example.game", process_pattern=":render",
            device="offline-serial")
        target_code, target = self.request_json("GET", "/api/target")
        apps_code, apps = self.request_json("GET", "/api/device-apps")
        candidates_code, candidates = self.request_json("GET", "/api/candidates")
        start_code, start = self.request_json("POST", "/api/start?pid=123")

        self.assertEqual((target_code, apps_code, candidates_code, start_code),
                         (200, 200, 200, 200))
        self.assertEqual(target, {
            "running": False,
            "package": "com.example.game",
            "process_pattern": ":render",
            "device": "offline-serial",
        })
        self.assertFalse(apps["ok"])
        self.assertEqual(apps["apps"], [])
        self.assertFalse(candidates["ok"])
        self.assertEqual(candidates["candidates"], [])
        self.assertFalse(start["ok"])

    def test_cross_origin_post_is_rejected_without_callback(self):
        called = threading.Event()
        self.server.set_stop_callback(called.set)
        code, body = self.request_json(
            "POST", "/api/stop", headers={"Origin": "https://attacker.example"})
        self.assertEqual(code, 403)
        self.assertIn("cross-origin", body["error"])
        self.assertFalse(called.is_set())

    def test_runs_and_report_use_real_files(self):
        run_dir = os.path.join(self.tempdir.name, "run1")
        os.makedirs(run_dir)
        path = os.path.join(run_dir, "capture.jsonl")
        rows = [
            {"event": "meta", "schema_version": 3},
            {"t_ms": 0, "fps": {"fps": 60}},
            {"t_ms": 1000, "fps": {"fps": 59}},
        ]
        with open(path, "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")

        _, runs = self.request_json("GET", "/api/runs")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["name"], "run1/capture.jsonl")
        self.assertEqual(runs[0]["points"], 2)
        _, report = self.request_json(
            "GET", "/api/report?" + urlencode({"name": "run1/capture.jsonl"}))
        self.assertEqual(report, rows)

    def test_event_sidecar_is_not_listed_or_loadable_as_report(self):
        run_dir = os.path.join(self.tempdir.name, "run1")
        os.makedirs(run_dir)
        report_path = os.path.join(run_dir, "capture.jsonl")
        events_path = os.path.join(run_dir, "capture.events.jsonl")
        with open(report_path, "w", encoding="utf-8") as stream:
            stream.write('{"t_ms": 1}\n')
        with open(events_path, "w", encoding="utf-8") as stream:
            stream.write('{"type": "jank", "t_ms": 1}\n')

        _, runs = self.request_json("GET", "/api/runs")
        self.assertEqual([run["name"] for run in runs], ["run1/capture.jsonl"])
        _, report = self.request_json(
            "GET", "/api/report?" + urlencode({"name": "run1/capture.events.jsonl"}))
        self.assertEqual(report, {"error": "bad name"})

    def test_rename_events_and_raw_report_round_trip(self):
        run_dir = os.path.join(self.tempdir.name, "run1")
        os.makedirs(run_dir)
        jsonl_path = os.path.join(run_dir, "capture.jsonl")
        events_path = os.path.join(run_dir, "capture.events.jsonl")
        html_path = os.path.join(run_dir, "capture.html")
        with open(jsonl_path, "w", encoding="utf-8") as stream:
            stream.write('{"t_ms": 1}\n')
        with open(events_path, "w", encoding="utf-8") as stream:
            stream.write('{"type": "jank", "t_ms": 1}\n')
        with open(html_path, "w", encoding="utf-8") as stream:
            stream.write("<!doctype html><title>capture</title>")

        rename_path = "/api/rename?" + urlencode({
            "name": "run1/capture.jsonl", "newname": " 场景/A\nB "})
        rename_code, renamed = self.request_json("POST", rename_path)
        self.assertEqual(rename_code, 200)
        self.assertTrue(renamed["ok"])
        with open(jsonl_path + ".remark.txt", encoding="utf-8") as stream:
            self.assertEqual(stream.read(), "场景AB")

        _, runs = self.request_json("GET", "/api/runs")
        capture_run = next(
            run for run in runs if run["name"] == "run1/capture.jsonl")
        self.assertEqual(capture_run["remark"], "场景AB")
        _, events = self.request_json(
            "GET", "/api/events?" + urlencode({"name": "run1/capture.jsonl"}))
        self.assertEqual(events, [{"type": "jank", "t_ms": 1}])
        raw_code, raw_type, raw = self.request(
            "GET", "/api/raw?" + urlencode({"name": "run1/capture.html"}))
        self.assertEqual(raw_code, 200)
        self.assertIn("text/html", raw_type)
        self.assertIn(b"<title>capture</title>", raw)

        _, cleared = self.request_json(
            "POST", "/api/rename?" + urlencode({
                "name": "run1/capture.jsonl", "newname": ""}))
        self.assertTrue(cleared["ok"])
        self.assertFalse(os.path.exists(jsonl_path + ".remark.txt"))

    def test_annotation_create_load_delete_round_trip(self):
        run_dir = os.path.join(self.tempdir.name, "run-ann")
        os.makedirs(run_dir)
        report = os.path.join(run_dir, "capture.jsonl")
        with open(report, "w", encoding="utf-8") as stream:
            stream.write('{"t_ms":0}\n{"t_ms":5000}\n')
        name = "run-ann/capture.jsonl"
        create = urlencode({
            "name": name, "start_ms": 1000, "end_ms": 4000,
            "text": "Boss 战", "color": "#ff7043"})
        code, result = self.request_json("POST", "/api/annotations?" + create)
        self.assertEqual(code, 200)
        self.assertTrue(result["ok"])
        annotation_id = result["annotation"]["id"]
        _, items = self.request_json(
            "GET", "/api/annotations?" + urlencode({"name": name}))
        self.assertEqual(items[0]["text"], "Boss 战")
        delete = urlencode({"name": name, "action": "delete", "id": annotation_id})
        _, deleted = self.request_json("POST", "/api/annotations?" + delete)
        self.assertTrue(deleted["ok"])
        _, items = self.request_json(
            "GET", "/api/annotations?" + urlencode({"name": name}))
        self.assertEqual(items, [])

    def test_annotation_pin_and_rename_workflow(self):
        run_dir = os.path.join(self.tempdir.name, "run-label")
        os.makedirs(run_dir)
        report = os.path.join(run_dir, "capture.jsonl")
        with open(report, "w", encoding="utf-8") as stream:
            stream.write('{"t_ms":0}\n{"t_ms":5000}\n')
        name = "run-label/capture.jsonl"
        _, first = self.request_json("POST", "/api/annotations?" + urlencode({
            "name": name, "action": "pin", "at_ms": 1000}))
        _, second = self.request_json("POST", "/api/annotations?" + urlencode({
            "name": name, "action": "pin", "at_ms": 4000}))
        self.assertTrue(first["ok"])
        self.assertEqual(first["annotations"][0]["text"], "Label1")
        self.assertEqual(first["annotations"][0]["end_ms"], 1000)
        self.assertEqual(second["annotations"][1]["end_ms"], 4000)
        self.assertEqual(second["annotation"]["text"], "Label3")
        _, renamed = self.request_json("POST", "/api/annotations?" + urlencode({
            "name": name, "action": "rename",
            "id": first["annotations"][0]["id"], "text": "Boss 战"}))
        self.assertEqual(renamed["annotation"]["text"], "Boss 战")
    def test_report_and_raw_reject_path_traversal(self):
        report_code, report = self.request_json(
            "GET", "/api/report?" + urlencode({"name": "../outside.jsonl"}))
        raw_code, _, raw = self.request(
            "GET", "/api/raw?" + urlencode({"name": "../outside.html"}))
        self.assertEqual(report_code, 200)
        self.assertEqual(report["error"], "bad path")
        self.assertEqual(raw_code, 400)
        self.assertEqual(json.loads(raw.decode("utf-8"))["error"], "bad path")

    def test_latest_ring_is_bounded_over_http(self):
        for index in range(RING_SIZE + 5):
            self.server.add_sample({"t_ms": index})
        _, latest = self.request_json("GET", "/api/latest")
        self.assertEqual(len(latest), RING_SIZE)
        self.assertEqual(latest[0]["t_ms"], 5)
        self.assertEqual(latest[-1]["_seq"], RING_SIZE + 5)

    def test_unknown_and_traversal_paths_return_404(self):
        code, _, _ = self.request("GET", "/does-not-exist")
        traversal_code, _, _ = self.request("GET", "/assets/../README.md")
        self.assertEqual(code, 404)
        self.assertEqual(traversal_code, 404)

    def test_sse_pushes_incremental_sample(self):
        handler_errors = []
        self.server._httpd.handle_error = (
            lambda request, client: handler_errors.append(sys.exc_info()[1]))
        self.server.add_sample({"t_ms": 1234, "fps": {"fps": 58}})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request("GET", "/api/stream")
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn("text/event-stream", response.getheader("Content-Type"))
        line = response.readline().decode("utf-8").strip()
        self.assertTrue(line.startswith("data: "), line)
        payload = json.loads(line[len("data: "):])
        self.assertEqual(payload["t_ms"], 1234)
        self.assertEqual(payload["_seq"], 1)
        conn.close()
        time.sleep(0.45)  # 让 handler 在下一次写入时观察到客户端已断开。
        self.assertEqual(handler_errors, [])

    def test_stop_releases_port_and_is_idempotent(self):
        port = self.port
        thread = self.server._thread
        self.server.stop()
        self.assertFalse(thread.is_alive())
        self.assertIsNone(self.server._httpd)
        self.assertIsNone(self.server._thread)
        self.server.stop()

        replacement = WebServer(port=port, output_dir=self.tempdir.name)
        try:
            self.assertEqual(replacement.start(), port)
        finally:
            replacement.stop()

    def test_second_server_cannot_silently_share_listening_port(self):
        duplicate = WebServer(port=self.port, output_dir=self.tempdir.name)
        with self.assertRaises(OSError):
            duplicate.start()
        duplicate.stop()


if __name__ == "__main__":
    unittest.main()
