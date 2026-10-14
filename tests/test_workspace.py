import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))
from adb import AdbError
from wireless import WirelessAdb
from workspace import Preparation, prepare_device, wifi_address, run_workspace, ConsoleStartInput
from web import WebServer


def manager():
    value = Mock()
    value.devices.return_value = [{"serial": "USB1", "state": "device", "model": "Phone", "transport": "usb"}]
    def command(args):
        if args[-1] == "ro.serialno":
            return "physical-one"
        if "addr" in args:
            return "12: wlan0    inet 192.168.1.8/24 brd 192.168.1.255 scope global wlan0"
        return "perfcollect-ready"
    value.identify_transport.side_effect = lambda device: WirelessAdb.identify_transport(value, device)
    value._run.side_effect = command
    return value


class WorkspaceTests(unittest.TestCase):
    def test_discovery_classifies_windows_usb_when_devpath_unknown(self):
        m = manager()
        m.devices.return_value[0]["transport"] = "unknown"
        m._run.side_effect = ["unknown", "USB1", "physical-one"]
        prep = Preparation(Mock(), manager_factory=lambda: m)
        prep.refresh()
        self.assertEqual(prep.snapshot()["devices"][0]["transport"], "usb")

    def test_identity_discovery_is_cached_and_not_a_blocking_operation(self):
        m = manager()
        prep = Preparation(Mock(), manager_factory=lambda: m)
        prep.refresh()
        self.assertEqual(prep.snapshot()["devices"][0]["identity"], "physical-one")
        self.assertFalse(prep.snapshot()["busy"])
        calls = m._run.call_count
        prep.refresh()
        self.assertEqual(m._run.call_count, calls)

    def test_identity_unavailable_keeps_connection_usable(self):
        m = manager()
        m._run.side_effect = AdbError("permission denied")
        prep = Preparation(Mock(), manager_factory=lambda: m)
        prep.refresh()
        self.assertEqual(prep.snapshot()["devices"][0]["identity"], "")
        self.assertEqual(prep.snapshot()["devices"][0]["state"], "device")

    def test_selected_identity_is_rechecked_before_using_connection(self):
        m = manager()
        with self.assertRaisesRegex(AdbError, "身份已变化"):
            prepare_device(m, "USB1", expected_identity="different")
        m.connect.assert_not_called()
        self.assertFalse(any("tcpip" in c.args[0] for c in m._run.call_args_list))

    def test_discovery_does_not_lock_out_user_selection(self):
        m = manager()
        entered, finish = threading.Event(), threading.Event()
        def devices():
            entered.set()
            finish.wait(2)
            return [{"serial":"USB1","state":"device","model":"Phone","transport":"usb"}]
        m.devices.side_effect = devices
        prep = Preparation(Mock(), manager_factory=lambda: m)
        worker = threading.Thread(target=prep.refresh)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(prep.snapshot()["refreshing"])
            self.assertFalse(prep.snapshot()["busy"])
            with patch("workspace.prepare_device", return_value="USB1"):
                self.assertTrue(prep.choose("USB1")[0])
                self.assertTrue(prep.ready.wait(1))
        finally:
            finish.set()
            worker.join(2)
        self.assertEqual(prep.snapshot()["devices"], [])

    def test_completed_capture_retains_status_until_user_prepares_again(self):
        with tempfile.TemporaryDirectory() as directory:
            web = WebServer(output_dir=directory)
            web.set_status(report_name="run/data.jsonl", device="USB1", pid=123,
                           device_info={"model":"phone"}, connection={"label":"USB"})
            prep = Preparation(web)
            prep.reset()
            self.assertTrue(web.status["completed"])
            self.assertEqual(web.status["device"], "USB1")
            self.assertEqual(web.status["pid"], 123)
            self.assertEqual(web.status["report_name"], "run/data.jsonl")
            self.assertTrue(prep.snapshot()["available"])

    def test_prepare_revalidates_windows_usb_before_switching(self):
        m = manager()
        m.devices.return_value[0]["transport"] = "unknown"
        original = m._run.side_effect
        def command(args):
            if args[-1] == "get-devpath":
                return "unknown"
            if args == ["-d", "get-serialno"]:
                return "USB1"
            return original(args)
        m._run.side_effect = command
        self.assertEqual(prepare_device(m, "USB1", True), "192.168.1.8:5555")

    def test_wifi_address_ignores_cellular_and_vpn(self):
        text = "1: rmnet0 inet 10.1.1.1/24\n2: tun0 inet 10.2.2.2/24\n3: wlan0 inet 192.168.1.8/24"
        self.assertEqual(wifi_address(text), "192.168.1.8")

    def test_ambiguous_or_absent_wifi_is_not_guessed(self):
        for text in ("", "1: wlan0 inet 192.168.1.8/24\n2: wlan1 inet 192.168.2.8/24"):
            with self.subTest(text=text), self.assertRaises(AdbError):
                wifi_address(text)

    def test_usb_does_not_change_device_mode(self):
        m = manager()
        self.assertEqual(prepare_device(m, "USB1"), "USB1")
        m.connect.assert_not_called()
        self.assertFalse(any("tcpip" in c.args[0] for c in m._run.call_args_list))

    def test_wireless_verifies_same_identity_before_ready(self):
        m = manager()
        self.assertEqual(prepare_device(m, "USB1", True), "192.168.1.8:5555")
        m.connect.assert_called_once_with("192.168.1.8:5555")
        self.assertIn(["-s", "USB1", "tcpip", "5555"], [c.args[0] for c in m._run.call_args_list])

    def test_identity_mismatch_rejected_without_switching_other_devices(self):
        m = manager()
        original = m._run.side_effect
        m._run.side_effect = lambda args: "other-phone" if args[:2] == ["-s", "192.168.1.8:5555"] else original(args)
        with self.assertRaisesRegex(AdbError, "身份不一致"):
            prepare_device(m, "USB1", True)
        m.connect.assert_called_once()

    def test_offline_cannot_start_or_change_mode(self):
        m = manager()
        with self.assertRaisesRegex(AdbError, "离线"):
            prepare_device(m, "different", True)
        m._run.assert_not_called()

    def test_unknown_transport_requires_usb_devpath(self):
        m = manager()
        m.devices.return_value[0]["transport"] = "unknown"
        m._run.return_value = "local:whatever"
        m._run.side_effect = None
        with self.assertRaisesRegex(AdbError, "USB"):
            prepare_device(m, "USB1", True)
        self.assertEqual(m._run.call_count, 2)

    def test_failed_connect_has_finite_retry_and_no_reset(self):
        m = manager()
        m.connect.side_effect = AdbError("refused")
        with patch("workspace.time.sleep"), self.assertRaisesRegex(AdbError, "保留USB线"):
            prepare_device(m, "USB1", True)
        self.assertEqual(m.connect.call_count, 3)
        self.assertFalse(any("kill-server" in c.args[0] for c in m._run.call_args_list))

    def test_pending_preparation_rejects_double_click(self):
        web = Mock()
        prep = Preparation(web)
        entered, finish = threading.Event(), threading.Event()
        def work(*args):
            entered.set()
            finish.wait(2)
            return "USB1"
        with patch("workspace.prepare_device", side_effect=work), patch.object(prep, "manager_factory", return_value=manager()):
            self.assertTrue(prep.choose("USB1")[0])
            self.assertTrue(entered.wait(1))
            self.assertFalse(prep.choose("USB1", True)[0])
            finish.set()
            self.assertTrue(prep.ready.wait(1))
        self.assertFalse(prep.snapshot()["available"])

    def test_failure_preserves_preparation_and_does_not_signal_ready(self):
        prep = Preparation(Mock(), manager_factory=manager)
        with patch("workspace.prepare_device", side_effect=AdbError("failure")):
            prep.choose("USB1")
            for _ in range(100):
                if not prep.snapshot()["busy"]:
                    break
                threading.Event().wait(.01)
        self.assertFalse(prep.ready.is_set())
        self.assertTrue(prep.snapshot()["available"])
        self.assertEqual(prep.snapshot()["message"], "failure")

    def test_discovery_stops_during_capture(self):
        m = manager()
        prep = Preparation(Mock(), manager_factory=lambda: m)
        prep.accepting = False
        prep.refresh()
        m.devices.assert_not_called()

    def test_reset_removes_old_device_callbacks_and_start_request(self):
        web = Mock()
        prep = Preparation(web)
        prep.ready.set()
        prep.accepting = False
        prep.reset()
        self.assertIsNone(web.adb)
        self.assertFalse(prep.ready.is_set())
        self.assertTrue(prep.accepting)
        web.set_switch_callback.assert_called_with(None)
        web.take_start_request.assert_called_once()

    def test_bootstrap_happens_before_adb_validation(self):
        import main
        with patch.object(sys, "argv", ["main.py", "--web", "--no-browser"]), \
             patch("main.load_config", return_value={}), \
             patch("workspace.run_workspace", return_value="opened") as start, \
             patch("main.Adb", side_effect=AssertionError("must not require device")):
            self.assertEqual(main.main(), "opened")
        start.assert_called_once()

    def test_bootstrap_does_not_read_or_validate_capture_config(self):
        import main
        with patch.object(sys, "argv", ["main.py", "--web", "--interval", "-1", "--config", "missing.json"]), \
             patch("main.load_config", side_effect=AssertionError("history must not read config")), \
             patch("main.validate_probe_options", side_effect=AssertionError("not capturing")), \
             patch("workspace.run_workspace", return_value="opened"):
            self.assertEqual(main.main(), "opened")

    def test_prepared_capture_config_error_is_reportable_without_adb(self):
        import main
        with patch.object(sys, "argv", ["main.py", "--web"]), \
             patch("main.load_config", side_effect=ValueError("bad config")), \
             patch("main.Adb") as adb:
            with self.assertRaisesRegex(ValueError, "采集配置错误.*bad config"):
                main.main(Mock(), "USB1")
        adb.assert_not_called()

    def test_missing_adb_discovery_preserves_history_service(self):
        from urllib.parse import urlencode
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "perfcollect_20261013_010101.jsonl"
            report.write_text('{"t_ms":0,"fps":{"fps":60}}\n', encoding="utf-8")
            web = WebServer(port=0, output_dir=directory)
            prep = Preparation(web, manager_factory=Mock(side_effect=AdbError("missing ADB")))
            web.preparation = prep
            prep.reset()
            prep.refresh()
            port = web.start()
            base = f"http://127.0.0.1:{port}"
            try:
                with urlopen(base + "/report.html") as response:
                    self.assertEqual(response.status, 200)
                with urlopen(base + "/api/runs") as response:
                    self.assertTrue(json.load(response))
                query = urlencode({"name": report.name, "newname": "offline"})
                with urlopen(Request(base + "/api/rename?" + query, method="POST")) as response:
                    renamed = json.load(response)
                    self.assertTrue(renamed["ok"])
                with urlopen(base + "/api/report?" + urlencode({"name": renamed["name"]})) as response:
                    self.assertIn('60', response.read().decode())
                self.assertEqual(prep.snapshot()["message"], "missing ADB")
            finally:
                web.stop()

    def test_workspace_returns_after_capture_save_and_allows_second_run(self):
        web = Mock(port=8080)
        web.start.return_value = 8080
        captures = []
        original_reset = Preparation.reset
        def reset(prep, message=None):
            original_reset(prep, message)
            prep.serial = "USB1"
            prep.ready.set()
        def capture(server, serial):
            captures.append(serial)
            if len(captures) == 2:
                server.preparation.closed.set()
        args = SimpleNamespace(port=8080, output="unused", no_browser=True)
        with patch("web.WebServer", return_value=web), patch("workspace.Preparation.reset", reset), \
             patch("workspace.Preparation.refresh"), patch("workspace.signal.signal"):
            run_workspace(args, capture)
        self.assertEqual(captures, ["USB1", "USB1"])
        web.stop.assert_called_once()

    def test_http_preparation_available_without_adb_and_cross_origin_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            web = WebServer(port=0, output_dir=directory)
            web.preparation = Preparation(web)
            web.preparation.reset()
            port = web.start()
            try:
                base = f"http://127.0.0.1:{port}"
                with urlopen(base + "/api/preparation") as response:
                    self.assertTrue(json.load(response)["available"])
                req = Request(base + "/api/prepare-device?serial=USB1", method="POST", headers={"Origin": "http://evil.example"})
                with self.assertRaises(HTTPError) as error:
                    urlopen(req)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            finally:
                web.stop()

    def test_start_blocked_before_device_ready_and_while_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            web = WebServer(output_dir=directory, process_pattern="")
            web.preparation = Preparation(web)
            web.set_status(target="game", phase="prepare")
            self.assertFalse(web.request_start()[0])
            web.set_status(phase="saving")
            self.assertFalse(web.request_start()[0])

    def test_start_request_claims_state_until_collector_initializes(self):
        with tempfile.TemporaryDirectory() as directory:
            web = WebServer(output_dir=directory, process_pattern="")
            web.preparation = Preparation(web)
            web.set_status(target="game", phase="waiting")
            self.assertTrue(web.request_start()[0])
            self.assertEqual(web.status["phase"], "starting")
            web.take_start_request()
            self.assertFalse(web.request_start()[0])

    def test_old_label_worker_keeps_original_device_and_cannot_write_after_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            old_adb, new_adb = Mock(), Mock()
            web = WebServer(output_dir=directory, adb=new_adb)
            old_abort = threading.Event()
            web._resolving = True
            def resolve(adb, *args):
                self.assertIs(adb, old_adb)
                old_abort.set()
                return "Old label"
            with patch.object(web, "_load_app_labels", return_value={}), \
                 patch.object(web.device_queries, "find_aapt", return_value=None), \
                 patch.object(web.device_queries, "apk_label", side_effect=resolve), \
                 patch.object(web, "_save_app_label") as save:
                web._resolve_labels_async("OLD", [("game", "/old.apk")], old_adb, old_abort)
            save.assert_not_called()
            self.assertTrue(web._resolving)


class ConsoleStartInputTests(unittest.TestCase):
    def console(self, initial=""):
        chars = list(initial)
        return SimpleNamespace(kbhit=lambda:bool(chars), getwch=lambda:chars.pop(0), chars=chars)

    @patch("builtins.print")
    def test_empty_enter_selects_recommended_without_blocking(self, _):
        console = self.console(); reader = ConsoleStartInput(console)
        self.assertIsNone(reader.poll())
        console.chars.extend("\r")
        self.assertEqual(reader.poll(), "")

    @patch("builtins.print")
    def test_partial_pid_waits_for_enter(self, _):
        console = self.console(); reader = ConsoleStartInput(console)
        console.chars.extend("12")
        self.assertIsNone(reader.poll())
        console.chars.extend("3\r")
        self.assertEqual(reader.poll(), "123")

    @patch("builtins.print")
    def test_backspace_edits_pid(self, _):
        console = self.console(); reader = ConsoleStartInput(console)
        console.chars.extend("234\b5\r")
        self.assertEqual(reader.poll(), "235")

    @patch("builtins.print")
    def test_extended_keys_are_not_pid_characters(self, _):
        console = self.console(); reader = ConsoleStartInput(console)
        console.chars.extend("\xe0H1\r")
        self.assertEqual(reader.poll(), "1")

    @patch("builtins.print")
    def test_new_session_discards_old_buffered_keys(self, _):
        console = self.console("999\r"); reader = ConsoleStartInput(console)
        self.assertIsNone(reader.poll())
        console.chars.extend("2\r")
        self.assertEqual(reader.poll(), "2")

    def test_without_windows_console_does_not_read_stdin(self):
        with patch("workspace.sys.platform", "linux"):
            reader = ConsoleStartInput()
        self.assertIsNone(reader.poll())

    def test_ctrl_c_uses_existing_stop_handler(self):
        console = self.console(); reader = ConsoleStartInput(console)
        console.chars.extend("\x03")
        with patch("workspace.signal.raise_signal") as raise_signal:
            self.assertIsNone(reader.poll())
        import signal
        raise_signal.assert_called_once_with(signal.SIGINT)


if __name__ == "__main__":
    unittest.main()
