"""ADB operations shared by the dashboard's connection preparation."""
import subprocess

from adb import AdbError, _find_adb
from adb_connection import parse_devices, validate_endpoint


class WirelessAdb:
    def __init__(self, adb_path=None):
        self.adb = adb_path or _find_adb()
        if not self.adb:
            raise AdbError("未找到可用ADB，请安装Android SDK Platform-Tools并检查PATH。")

    def _run(self, args, timeout=15):
        try:
            result = subprocess.run([self.adb, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as error:
            raise AdbError("ADB操作超时，请检查地址、端口和网络可达性。") from error
        except OSError as error:
            raise AdbError("无法运行ADB，请检查Platform-Tools。") from error
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode:
            raise AdbError(output[:500] or "ADB操作失败")
        return output

    def devices(self):
        return parse_devices(self._run(["devices", "-l"]))

    def identify_transport(self, device):
        """Prove USB without guessing from the shape of a device serial."""
        kind = device["transport"]
        if kind != "unknown":
            return kind
        serial = device["serial"]
        try:
            if self._run(["-s", serial, "get-devpath"]).strip().startswith("usb:"):
                return "usb"
        except AdbError:
            pass
        try:
            # Windows ADB may omit usb: AND return unknown for get-devpath.
            # -d selects physical USB only; ambiguous/mismatched results fail closed.
            if self._run(["-d", "get-serialno"]).strip() == serial:
                return "usb"
        except AdbError:
            pass
        return "unknown"


    def connect(self, endpoint):
        endpoint = validate_endpoint(endpoint)
        output = self._run(["connect", endpoint])
        if not (output.lower().startswith("connected to ") or
                output.lower().startswith("already connected to ")):
            raise AdbError(output[:500] or "未能连接设备")
        return output
