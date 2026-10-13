"""ADB device enumeration and connection metadata, shared by both launchers."""
import ipaddress
import re


WIRELESS_NETWORK_NOTE = "整机网络流量包含无线ADB采集通信，不等同于游戏流量"


def validate_endpoint(value):
    """Require one host:port endpoint; never pass arbitrary ADB options."""
    value = str(value).strip()
    if value.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\]:(\d{1,5})", value)
        if not match:
            raise ValueError("请输入 [IPv6地址]:端口")
        ipaddress.IPv6Address(match[1])
    else:
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9.-]*):(\d{1,5})", value)
        if not match:
            raise ValueError("请输入手机IP:端口，例如192.168.1.20:37123")
        host = match[1]
        if re.fullmatch(r"[0-9.]+", host):
            ipaddress.IPv4Address(host)
        elif len(host) > 253 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part)
                                    for part in host.split(".")):
            raise ValueError("设备地址格式不正确")
    if not 1 <= int(match[2]) <= 65535:
        raise ValueError("端口必须在1～65535之间")
    return value


def transport_kind(serial, fields=None):
    fields = fields or {}
    if serial.startswith("emulator-"):
        return "emulator"
    if "usb" in fields:
        return "usb"
    if "_adb-tls-connect._tcp" in serial:
        return "wifi"
    try:
        validate_endpoint(serial)
        return "wifi"
    except ValueError:
        return "unknown"


def parse_devices(output):
    devices = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] not in ("device", "offline", "unauthorized", "no"):
            continue
        fields = dict(part.split(":", 1) for part in parts[2:] if ":" in part)
        devices.append({"serial": parts[0], "state": parts[1],
                        "model": fields.get("model", "").replace("_", " "),
                        "transport": transport_kind(parts[0], fields)})
    return devices


def connection_info(serial, devices=()):
    device = next((item for item in devices if item["serial"] == serial), None)
    kind = device["transport"] if device else transport_kind(serial)
    return {"transport": kind, "serial": serial,
            "label": {"wifi": "无线ADB", "usb": "USB", "emulator": "模拟器ADB"}.get(kind, "ADB（连接类型未识别）"),
            "network_note": WIRELESS_NETWORK_NOTE if kind == "wifi" else None}


def describe_connection(adb):
    try:
        devices = parse_devices(adb._run(["devices", "-l"]))
    except Exception:
        devices = []
    info = connection_info(adb.serial, devices)
    if info["transport"] == "unknown":
        try:
            # Some Windows ADB builds omit the usb: attribute in devices -l.
            if adb._run(["get-devpath"]).strip().startswith("usb:"):
                info.update(transport="usb", label="USB")
            else:
                from wireless import WirelessAdb
                # Use the global USB selector, not this instance's -s base.
                manager = WirelessAdb(adb.adb)
                if manager._run(["-d", "get-serialno"]).strip() == adb.serial:
                    info.update(transport="usb", label="USB")
        except Exception:
            pass
    return info
