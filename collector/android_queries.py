# -*- coding: utf-8 -*-
"""HTTP 无关的 Android 只读查询；不持有采集 worker 或更改连接模式。"""

import re
import threading
import time

from apk_label import get_apk_label


APPS_CACHE_TTL = 60


class AndroidQueries:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._apps_cache = {}
        self._apps_scan_lock = threading.Lock()

    def probe_candidates(self, adb, pattern):
        from probe import probe_once
        return probe_once(adb, pattern)

    def scan_apps(self, adb, force=False):
        """包列表 60s 缓存；并发只扫描一次，错误不缓存。"""
        serial = adb.serial
        if force:
            self._apps_cache.pop(serial, None)
        now = self.clock()
        cached = self._apps_cache.get(serial)
        if cached and cached[0] > now:
            return cached[1], None
        with self._apps_scan_lock:
            cached = self._apps_cache.get(serial)
            if cached and cached[0] > now:
                return cached[1], None
            try:
                out = adb.shell(["pm", "list", "packages", "-3", "-f"])
            except Exception as exc:
                return [], f"列出应用失败: {exc}"
            apps, seen = [], set()
            for line in out.splitlines():
                s = line.strip()
                if s.startswith("package:"):
                    rest = s[len("package:"):].strip()
                    path, _, pkg = rest.rpartition("=")
                    if not pkg:
                        pkg, path = path, ""
                    if pkg and pkg not in seen:
                        seen.add(pkg)
                        apps.append({"pkg": pkg, "apk": path})
            apps.sort(key=lambda a: a["pkg"])
            self._apps_cache[serial] = (self.clock() + APPS_CACHE_TTL, apps)
            return apps, None

    def find_aapt(self, adb):
        try:
            out = adb.shell(["sh", "-c",
                "ls /system/bin/aapt /system/bin/aapt2 /system/xbin/aapt "
                "/system/xbin/aapt2 2>/dev/null"])
        except Exception:
            return None
        for line in out.splitlines():
            if line.strip():
                return line.strip()
        return None

    def aapt_label(self, adb, aapt, apk_path):
        try:
            out = adb.shell([aapt, "dump", "badging", apk_path])
        except Exception:
            return None
        for line in out.splitlines():
            s = line.strip()
            match = re.search(r"application-label:['\"](.*?)['\"]", s)
            if match:
                return match.group(1)
            match = re.search(r"application:\s+label='(.*?)'", s)
            if match:
                return match.group(1)
        return None

    def apk_label(self, adb, package, apk_path):
        return get_apk_label(adb, package, apk_path)
