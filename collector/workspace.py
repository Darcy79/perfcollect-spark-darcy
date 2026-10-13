"""Persistent local dashboard and pre-capture device preparation."""
import ipaddress
import errno
import re
import signal
import sys
import threading
import time

from adb import AdbError
from wireless import WirelessAdb


class ConsoleStartInput:
    """Windows终端备用输入：随等待循环轮询，不遗留跨采集会话的input线程。"""

    def __init__(self, console=None):
        if console is None and sys.platform == "win32" and sys.stdin and sys.stdin.isatty():
            import msvcrt
            console = msvcrt
        self.console = console
        self.buffer = []
        self.special = False
        # 丢弃在进入本次目标准备前留下的按键。
        if console:
            for _ in range(256):
                if not console.kbhit():
                    break
                console.getwch()

    def poll(self):
        if not self.console:
            return None
        for _ in range(256):
            if not self.console.kbhit():
                break
            char = self.console.getwch()
            if self.special:
                self.special = False
                continue
            if char in ("\x00", "\xe0"):
                self.special = True
            elif char in ("\r", "\n"):
                result = "".join(self.buffer)
                self.buffer.clear()
                print(flush=True)
                return result
            elif char == "\b":
                if self.buffer:
                    self.buffer.pop()
                    print("\b \b", end="", flush=True)
            elif char == "\x03":
                signal.raise_signal(signal.SIGINT)
                return None
            elif char.isprintable():
                self.buffer.append(char)
                print(char, end="", flush=True)
        return None


class WorkspacePortError(RuntimeError):
    """工作台未能绑定端口。"""

    def __init__(self, port):
        super().__init__(f"端口 {port} 已被占用")
        self.port = port


def port_candidates(preferred, explicit):
    return (preferred,) if explicit or preferred == 0 else range(preferred, 65536)


def start_workspace_server(preferred, explicit, output_dir, server_factory=None):
    """实际绑定后再选端口；只对地址占用回退，不掩盖权限等其他错误。"""
    if server_factory is None:
        from web import WebServer
        server_factory = WebServer
    for candidate in port_candidates(preferred, explicit):
        server = server_factory(port=candidate, output_dir=output_dir)
        try:
            actual = server.start()
            server.port = actual
            return server, actual
        except OSError as exc:
            server.stop()
            if exc.errno != errno.EADDRINUSE and getattr(exc, "winerror", None) != 10048:
                raise
            if explicit:
                raise WorkspacePortError(candidate) from exc
    raise WorkspacePortError(preferred)


def wifi_address(output):
    """Only select Wi-Fi interfaces; never use a cellular/VPN default route."""
    addresses = []
    for line in output.splitlines():
        match = re.search(r"\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/", line)
        if match and re.fullmatch(r"(?:wlan|wifi)\d+", match[1]):
            address = ipaddress.IPv4Address(match[2])
            if not address.is_loopback and not address.is_link_local and not address.is_unspecified:
                addresses.append(str(address))
    addresses = list(dict.fromkeys(addresses))
    if len(addresses) != 1:
        raise AdbError("无法确定手机的Wi-Fi地址，请确认手机已连接Wi-Fi；仍可使用USB采集。")
    return addresses[0]


def prepare_device(manager, serial, wireless=False, expected_identity=None):
    devices = manager.devices()
    device = next((d for d in devices if d["serial"] == serial and d["state"] == "device"), None)
    if not device:
        raise AdbError("所选手机已离线，请重新连接；若手机提示USB调试授权，请点允许。")
    if expected_identity:
        actual = manager._run(["-s", serial, "shell", "getprop", "ro.serialno"]).strip()
        if actual != expected_identity:
            raise AdbError("设备身份已变化，请刷新设备列表后重新选择。")
    if not wireless:
        manager._run(["-s", serial, "shell", "echo", "perfcollect-ready"])
        return serial
    kind = manager.identify_transport(device) if device["transport"] == "unknown" else device["transport"]
    if kind != "usb":
        raise AdbError("一键转无线需要先通过USB连接并授权手机。已在线的无线设备可直接选择采集。")
    identity = manager._run(["-s", serial, "shell", "getprop", "ro.serialno"]).strip()
    if not identity:
        raise AdbError("无法核对手机身份，本次不切换无线；请使用USB采集。")
    address = wifi_address(manager._run(["-s", serial, "shell", "ip", "-o", "-4", "addr", "show", "scope", "global"]))
    endpoint = address + ":5555"
    manager._run(["-s", serial, "tcpip", "5555"])
    # adbd restarts after tcpip. Retry only the selected endpoint, with a finite budget.
    error = None
    for attempt in range(3):
        try:
            manager.connect(endpoint)
            actual = manager._run(["-s", endpoint, "shell", "getprop", "ro.serialno"]).strip()
            if actual != identity:
                raise AdbError("无线端设备身份不一致，已拒绝使用；请保留USB线并检查网络。")
            manager._run(["-s", endpoint, "shell", "echo", "perfcollect-ready"])
            return endpoint
        except AdbError as exc:
            error = exc
            if "身份不一致" in str(exc):
                break
            if attempt < 2:
                time.sleep(1)
    raise AdbError("无线连接未通过验证，请保留USB线，确认电脑与手机在可互通的同一局域网。详情：" + str(error))


class Preparation:
    def __init__(self, web, manager_factory=WirelessAdb):
        self.web = web
        self.manager_factory = manager_factory
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.serial = None
        self.busy = False
        self.refreshing = False
        self.identities = {}
        self.accepting = True
        self.state = {"devices": [], "message": "请用USB连接手机，并在手机上允许USB调试。", "busy": False}

    def snapshot(self):
        with self.lock:
            return {**self.state, "busy": self.busy, "refreshing": self.refreshing, "available": self.accepting}

    def refresh(self):
        with self.lock:
            if self.busy or self.refreshing or not self.accepting or self.closed.is_set():
                return
            self.refreshing = True
        try:
            manager = self.manager_factory()
            devices = manager.devices()
            for device in devices:
                if device["state"] == "device" and device["transport"] == "unknown":
                    device["transport"] = manager.identify_transport(device)
                if device["state"] == "device" and device["transport"] in ("usb", "wifi"):
                    cached = self.identities.get(device["serial"])
                    if not cached or time.monotonic() - cached[0] > 15:
                        try:
                            identity = manager._run(["-s", device["serial"], "shell", "getprop", "ro.serialno"]).strip()
                            if not identity or identity.lower() in ("unknown", "null", "none", "0"):
                                identity = ""
                        except (AdbError, OSError):
                            identity = ""
                        cached = (time.monotonic(), identity)
                        self.identities[device["serial"]] = cached
                    device["identity"] = cached[1]
            online_serials = {d["serial"] for d in devices if d["state"] == "device"}
            self.identities = {k: v for k, v in self.identities.items() if k in online_serials}
            with self.lock:
                if self.accepting and not self.busy:
                    self.state["devices"] = devices
        except (AdbError, OSError) as exc:
            with self.lock:
                if self.accepting and not self.busy:
                    self.state["message"] = str(exc)
        finally:
            with self.lock:
                self.refreshing = False

    def choose(self, serial, wireless=False, expected_identity=None):
        with self.lock:
            if self.busy or not self.accepting or self.closed.is_set():
                return False, "正在准备或采集中，请稍候；停止并保存后才能更换连接。"
            if not serial:
                return False, "请先选择手机。"
            self.busy = True
            self.state["message"] = "正在准备并验证无线连接，请保留USB线…" if wireless else "正在验证手机连接…"
        def work():
            try:
                selected = prepare_device(self.manager_factory(), serial, wireless, expected_identity)
                with self.lock:
                    if self.closed.is_set():
                        return
                    self.serial = selected
                    self.accepting = False
                    self.state["message"] = "无线已连接，可以拔掉USB线。请选择采集目标。" if wireless else "手机已连接，请选择采集目标。"
                    self.ready.set()
            except (AdbError, OSError, ValueError) as exc:
                with self.lock:
                    self.state["message"] = str(exc)
            finally:
                with self.lock:
                    self.busy = False
        threading.Thread(target=work, daemon=True, name="device-prepare").start()
        return True, None

    def reset(self, message=None):
        self.web.abort_label_resolve()
        self.web.adb = None
        self.web.set_switch_callback(None)
        self.web.set_stop_callback(None)
        self.web.take_start_request()
        with self.lock:
            self.serial = None
            self.ready.clear()
            self.accepting = True
            self.state["message"] = message or "请选择手机及本次采集方式。"
        status = getattr(self.web, "status", {})
        completed = isinstance(status, dict) and bool(status.get("report_name"))
        cleared = {} if completed else dict(device="", device_info=None, connection=None, pid=None)
        self.web.set_status(phase="prepare", workspace=True, running=False, completed=completed,
                            completion_error=message or "", mismatch=None, **cleared)


def run_workspace(args, capture):
    from web import open_browser_when_ready
    preferred = 8080 if args.port is None else args.port
    try:
        web, port = start_workspace_server(preferred, args.port is not None, args.output)
    except (OSError, WorkspacePortError) as exc:
        print(f"[ERROR] 工作台启动失败：{exc}。请关闭已运行的窗口或使用其他--port。", flush=True)
        return 2
    prep = Preparation(web)
    web.preparation = prep
    prep.reset("连接手机后，选择本次采集方式。")
    web.set_shutdown_callback(prep.closed.set)
    if preferred and port != preferred:
        print(f"[!] 端口 {preferred} 已被占用，改用 {port}", flush=True)
    print(f"[+] 看板已启动：http://localhost:{port}；连接手机和选择应用均在页面完成。", flush=True)
    if not args.no_browser:
        open_browser_when_ready(port)

    def discover():
        while not prep.closed.is_set():
            prep.refresh()
            prep.closed.wait(3)
    threading.Thread(target=discover, daemon=True, name="device-discovery").start()
    signal.signal(signal.SIGINT, lambda *_: prep.closed.set())
    try:
        while not prep.closed.wait(0.2):
            if not prep.ready.is_set():
                continue
            serial = prep.serial
            web.clear_latest()
            web.set_status(phase="initializing", completed=False, run_id="", report_name="", started_at=None,
                           message="正在读取设备信息，请稍候…")
            error = None
            try:
                capture(web, serial)
            except (Exception, SystemExit) as exc:
                error = f"采集准备或运行失败：{exc}。请检查连接后重试；已有记录不会被覆盖。"
                print(error, flush=True)
            finally:
                if not prep.closed.is_set():
                    prep.reset(error)
                    web.set_shutdown_callback(prep.closed.set)
                    signal.signal(signal.SIGINT, lambda *_: prep.closed.set())
    finally:
        prep.closed.set()
        web.stop()
