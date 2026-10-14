"""Android 查询边界：固定时钟、并发缓存与 Web 注入，不连接手机。"""
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
from android_queries import AndroidQueries
from web import WebServer


class TestAndroidQueries(unittest.TestCase):
    def test_app_cache_ttl_force_and_device_isolation(self):
        now = [100.0]
        service = AndroidQueries(clock=lambda: now[0])
        adb = Mock(serial="USB")
        adb.shell.return_value = "package:/z.apk=com.z\npackage:/a.apk=com.a\npackage:/duplicate.apk=com.z\n"
        expected = [{"pkg": "com.a", "apk": "/a.apk"}, {"pkg": "com.z", "apk": "/z.apk"}]
        self.assertEqual(service.scan_apps(adb), (expected, None))
        now[0] = 159.9
        self.assertEqual(service.scan_apps(adb), (expected, None))
        self.assertEqual(adb.shell.call_count, 1)
        now[0] = 160.0
        service.scan_apps(adb)
        service.scan_apps(adb, force=True)
        self.assertEqual(adb.shell.call_count, 3)
        other = Mock(serial="WIFI")
        other.shell.return_value = "package:/b.apk=com.b"
        self.assertEqual(service.scan_apps(other)[0], [{"pkg": "com.b", "apk": "/b.apk"}])
        self.assertEqual(service.scan_apps(adb)[0], expected)

    def test_scan_failure_is_not_cached(self):
        service = AndroidQueries(clock=lambda: 1)
        adb = Mock(serial="USB")
        adb.shell.side_effect = [RuntimeError("offline"), "package:/game.apk=game"]
        self.assertEqual(service.scan_apps(adb), ([], "列出应用失败: offline"))
        self.assertEqual(service.scan_apps(adb), ([{"pkg": "game", "apk": "/game.apk"}], None))

    def test_concurrent_app_queries_share_one_scan(self):
        entered, release = threading.Event(), threading.Event()
        service = AndroidQueries(clock=lambda: 1)
        def read(_args):
            entered.set()
            self.assertTrue(release.wait(2))
            return "package:/game.apk=game"
        adb = Mock(serial="USB", shell=Mock(side_effect=read))
        results = []
        workers = [threading.Thread(target=lambda: results.append(service.scan_apps(adb))) for _ in range(2)]
        workers[0].start()
        self.assertTrue(entered.wait(1))
        workers[1].start()
        release.set()
        for worker in workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(adb.shell.call_count, 1)
        self.assertEqual(results, [([{"pkg": "game", "apk": "/game.apk"}], None)] * 2)

    def test_label_sources_preserve_failure_and_format_fallback(self):
        service = AndroidQueries()
        adb = Mock()
        adb.shell.side_effect = [OSError("denied"), "/system/bin/aapt2\n", "application: label='游戏' icon='x'", OSError("bad apk")]
        self.assertIsNone(service.find_aapt(adb))
        self.assertEqual(service.find_aapt(adb), "/system/bin/aapt2")
        self.assertEqual(service.aapt_label(adb, "aapt2", "/game.apk"), "游戏")
        self.assertIsNone(service.aapt_label(adb, "aapt2", "/game.apk"))
        with patch("android_queries.get_apk_label", return_value="游戏") as reader:
            self.assertEqual(service.apk_label(adb, "game", "/game.apk"), "游戏")
            reader.assert_called_once_with(adb, "game", "/game.apk")

    def test_candidate_adapter_calls_existing_probe(self):
        service, adb = AndroidQueries(), object()
        with patch("probe.probe_once", return_value={"ok": True}) as probe:
            self.assertEqual(service.probe_candidates(adb, "appbrand"), {"ok": True})
            probe.assert_called_once_with(adb, "appbrand")


class TestInjectedWebQueries(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.adb = Mock(serial="USB")
        self.queries = Mock()
        self.web = WebServer(output_dir=folder.name, adb=self.adb, device_queries=self.queries)

    def test_candidates_keep_ttl_force_validation_and_target_reset(self):
        result = {"ok": True, "candidates": [{"pid": 42, "name": "game:appbrand0"}],
                  "layer": {"name": "SurfaceView[game]#1", "appbrand_index": 0}}
        self.queries.probe_candidates.return_value = result
        with patch("web.time.time", return_value=100):
            self.assertEqual(self.web.probe_candidates(), result)
            self.assertTrue(self.web.request_start(42)[0])
            self.assertEqual(self.web.take_start_request(), {"pid": 42, "name": "game:appbrand0", "ts": 100})
            self.assertFalse(self.web.request_start(43)[0])
            self.assertEqual(self.queries.probe_candidates.call_count, 1)
            self.web.probe_candidates(force=True)
            self.assertEqual(self.queries.probe_candidates.call_count, 2)
        with patch("web.time.time", return_value=108):
            self.web.probe_candidates()
            self.assertEqual(self.queries.probe_candidates.call_count, 3)
            self.web.set_pending_target("game", "appbrand1")
            self.web.probe_candidates()
            self.queries.probe_candidates.assert_called_with(self.adb, "appbrand1")
            self.web.set_pending_target("game", "")
            self.assertTrue(self.web.probe_candidates()["direct"])
            self.assertTrue(self.web.request_start()[0])
            self.assertIsNone(self.web.take_start_request()["pid"])
            self.assertEqual(self.queries.probe_candidates.call_count, 4)

    def test_offline_queries_do_not_call_device_service(self):
        self.web.adb = None
        self.assertFalse(self.web.list_device_apps()["ok"])
        self.assertFalse(self.web.probe_candidates()["ok"])
        self.assertEqual(self.queries.mock_calls, [])

    def test_app_payload_copies_cache_and_keeps_label_state(self):
        scanned = [{"pkg": "game", "apk": "/game.apk"}]
        self.queries.scan_apps.return_value = scanned, None
        with patch.object(self.web, "_load_app_labels", return_value={"game": "游戏"}):
            self.assertEqual(self.web.list_device_apps(force=True), {
                "ok": True, "apps": [{"pkg": "game", "label": "游戏"}], "resolving": False})
        self.queries.scan_apps.assert_called_once_with(self.adb, force=True)
        self.assertEqual(scanned, [{"pkg": "game", "apk": "/game.apk"}])

    def test_query_errors_keep_existing_api_shape(self):
        self.queries.probe_candidates.side_effect = RuntimeError("offline")
        self.queries.scan_apps.return_value = [], "列出应用失败: offline"
        self.assertEqual(self.web.probe_candidates(), {
            "ok": False, "error": "探测失败: offline", "candidates": [],
            "layer": {"name": None, "appbrand_index": None}})
        self.assertEqual(self.web.list_device_apps(), {
            "ok": False, "apps": [], "error": "列出应用失败: offline"})
