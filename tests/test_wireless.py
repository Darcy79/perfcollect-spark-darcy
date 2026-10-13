"""Dashboard wireless contracts; no real device or network operations."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector"))
from adb import Adb, AdbError
from adb_connection import (WIRELESS_NETWORK_NOTE, connection_info, describe_connection,
                            parse_devices, validate_endpoint)
from export_report import export_html
from wireless import WirelessAdb


DEVICES = """List of devices attached
USB123 device usb:1-2 product:game model:My_Phone transport_id:1
192.168.1.20:37123 device product:game model:My_Phone transport_id:2
adb-TLS123-random._adb-tls-connect._tcp device model:Other_Phone transport_id:3
192.168.1.21:37123 offline transport_id:4
USB456 unauthorized usb:1-3
"""


class TestWirelessConnection(unittest.TestCase):
    def test_device_list_distinguishes_usb_wifi_and_unavailable(self):
        rows = parse_devices(DEVICES)
        self.assertEqual([row["transport"] for row in rows], ["usb", "wifi", "wifi", "wifi", "usb"])
        self.assertEqual(rows[0]["model"], "My Phone")
        self.assertEqual(rows[-1]["state"], "unauthorized")

    def test_endpoint_accepts_ipv4_ipv6_and_local_hostname(self):
        for value in ("192.168.1.2:37123", "[fe80::1234]:12345", "phone.local:65535"):
            self.assertEqual(validate_endpoint(value), value)

    def test_endpoint_rejects_options_commands_and_invalid_address(self):
        for value in ("--help", "192.168.1.2", "host:0", "host:65536", "999.1.1.1:5555",
                      "host:5555;adb kill-server", "host:5555\n--help", "[bad]:123", "bad..host:123"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_endpoint(value)

    def test_wireless_note_is_recorded_and_legacy_unknown_not_called_usb(self):
        wifi = connection_info("192.168.1.20:37123", parse_devices(DEVICES))
        self.assertEqual(wifi["network_note"], WIRELESS_NETWORK_NOTE)
        self.assertEqual(connection_info("USB123")["transport"], "unknown")
        self.assertIsNone(connection_info("USB123", parse_devices(DEVICES))["network_note"])

    def test_metadata_failure_keeps_known_wireless_serial(self):
        adb = Mock(serial="192.168.1.2:5555")
        adb._run.side_effect = AdbError("offline")
        self.assertEqual(describe_connection(adb)["transport"], "wifi")

    def test_windows_usb_without_list_attribute_uses_selected_devpath(self):
        adb = Mock(serial="USB123")
        adb._run.side_effect = ["List of devices attached\nUSB123 device model:Phone\n", "usb:1-2"]
        self.assertEqual(describe_connection(adb)["transport"], "usb")
        self.assertEqual(adb._run.call_args.args[0], ["get-devpath"])

    def test_multi_connection_requires_explicit_serial(self):
        adb = Adb.__new__(Adb)
        adb.adb, adb.serial = "adb", ""
        adb._run = Mock(return_value=DEVICES)
        with self.assertRaisesRegex(AdbError, "多个ADB连接"):
            adb._probe()
        adb.serial = "192.168.1.20:37123"
        adb._probe()

    def test_single_online_connection_preserves_automatic_selection(self):
        adb = Adb.__new__(Adb)
        adb.adb, adb.serial = "adb", ""
        adb._run = Mock(return_value="List of devices attached\n192.168.1.2:5555 device\nUSB123 offline\n")
        adb._probe()
        self.assertEqual(adb._base, ["adb", "-s", "192.168.1.2:5555"])


class TestWirelessOperations(unittest.TestCase):
    def setUp(self):
        self.manager = WirelessAdb("adb")

    def test_windows_unknown_devpath_is_proved_by_usb_only_selector(self):
        self.manager._run = Mock(side_effect=["unknown", "USB123\n"])
        device = {"serial": "USB123", "transport": "unknown"}
        self.assertEqual(self.manager.identify_transport(device), "usb")
        self.assertEqual(self.manager._run.call_args.args[0], ["-d", "get-serialno"])

    def test_other_usb_device_does_not_prove_selected_connection(self):
        self.manager._run = Mock(side_effect=["unknown", "OTHER"])
        self.assertEqual(self.manager.identify_transport({"serial": "USB123", "transport": "unknown"}), "unknown")

    def test_multiple_usb_or_unsupported_selector_stays_unknown(self):
        self.manager._run = Mock(side_effect=["unknown", AdbError("more than one device")])
        self.assertEqual(self.manager.identify_transport({"serial": "USB123", "transport": "unknown"}), "unknown")

    def test_known_wireless_never_uses_usb_selector(self):
        self.manager._run = Mock()
        self.assertEqual(self.manager.identify_transport({"serial": "host:5555", "transport": "wifi"}), "wifi")
        self.manager._run.assert_not_called()

    def test_invalid_endpoint_does_not_execute_adb(self):
        with patch("wireless.subprocess.run") as run, self.assertRaises(ValueError):
            self.manager.connect("--help")
        run.assert_not_called()

    def test_windows_unknown_devpath_metadata_uses_global_usb_selector(self):
        adb = Mock(serial="USB123", adb="adb")
        adb._run.side_effect = ["List of devices attached\nUSB123 device model:Phone\n", "unknown"]
        with patch("wireless.WirelessAdb._run", return_value="USB123\n") as run:
            self.assertEqual(describe_connection(adb)["transport"], "usb")
        run.assert_called_once_with(["-d", "get-serialno"])


    def test_failed_connect_with_zero_exit_is_not_success(self):
        with patch("wireless.subprocess.run", return_value=Mock(returncode=0, stdout="failed to connect to host:123", stderr="")):
            with self.assertRaisesRegex(AdbError, "failed"):
                self.manager.connect("host:123")

    def test_successful_connect_and_already_connected(self):
        for output in ("connected to host:123", "already connected to host:123"):
            with patch("wireless.subprocess.run", return_value=Mock(returncode=0, stdout=output, stderr="")) as run:
                self.assertEqual(self.manager.connect("host:123"), output)
                self.assertEqual(run.call_args.args[0], ["adb", "connect", "host:123"])


    def test_timeout_has_actionable_error_and_no_reset(self):
        with patch("wireless.subprocess.run", side_effect=subprocess.TimeoutExpired(["adb", "connect"], 15)) as run:
            with self.assertRaisesRegex(AdbError, "超时"):
                self.manager.connect("host:123")
            self.assertEqual(run.call_count, 1)


    def test_export_preserves_connection_and_network_scope(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "wireless.html"
            export_html([{"event": "meta", "device": {"model": "Phone", "connection":
                         connection_info("192.168.1.20:37123")}}, {"t_ms": 0}], str(path))
            page = path.read_text(encoding="utf-8")
            self.assertIn("无线ADB", page)
            self.assertIn(WIRELESS_NETWORK_NOTE, page)

    def test_distribution_includes_dashboard_transport_without_obsolete_launcher(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("wireless_distribution", Path(__file__).resolve().parents[1] / "tools/make_zip.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        paths = [relative for _, relative in module.collect()]
        self.assertNotIn("start_wireless.bat", paths)
        self.assertFalse((Path(__file__).resolve().parents[1] / "start_wireless.bat").exists())
        self.assertNotIn("start_dashboard.bat", paths)
        self.assertNotIn("collector/dashboard.py", paths)
        self.assertIn("start_perfcollect.bat", paths)
        for relative in ("collector/wireless.py", "collector/adb_connection.py"):
            self.assertIn(relative, paths)
