# -*- coding: utf-8 -*-
"""logcat 事件采集（零侵入崩溃证据 + 微信小游戏 console.log）。

背景：埋点 SDK 每次测试都要改代码/重打包，落地成本高。
用 adb logcat 流式读取所有被测应用的 Java/Native 崩溃、ANR、系统回收证据；
微信小游戏目标另行保留 JS 日志（console.log / chromium / 微信 tag）采集。

原理：
    adb logcat -v time  持续流式输出系统日志；
    后台线程逐行解析，以目标 PID/包名/完整进程名关联系统诊断事件；
    微信目标再按 tag 白名单 + 关键词过滤小游戏日志；
    用"设备 epoch 秒"锚点 + logcat 行自带设备时间戳做时间对齐 → 事件 t_ms 与采集数据同基准；
    供看板在曲线上叠加"场景/事件标注层"。

用法（main.py 集成）:
    from logcat import LogcatMonitor
    mon = LogcatMonitor(adb, serial)
    mon.start()
    ...
    for ev in mon.get_events():
        write_to(events_file, ev)
    mon.stop()
"""

import re
import subprocess
import threading
import time
from datetime import datetime

# logcat 行: 08-17 10:30:00.123  1234  5678 I chromium: [INFO:CONSOLE(12)] "hello"
_LINE_RE = re.compile(
    r"^(\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(\d+)\s+(\d+)\s+([VDIWEF])\s+([^:]+): (.*)$"
)

# tag 白名单：小游戏 JS 日志常出现在这些 tag
DEFAULT_TAGS = (
    "chromium", "WeChat", "MicroMsg", "console", "XWeb",
    "JsCore", "JSCore", "JSBridge", "WebView", "mm",
)

# 文本命中词：场景切换 / 打点 / 错误（命中即收）
DEFAULT_TEXT_HITS = (
    "console", "PERF", "perf", "scene", "Scene", "onShow", "onHide",
    "进入", "场景", "对局", "商城", "卡顿", "error", "exception",
    "crash", "Error", "Exception",
)

# Android 崩溃证据来自系统 tag，而不是应用自己的 console tag。只在行 PID、正文包名
# 或正文目标 PID 与当前被测目标明确关联时收集，避免把别的应用崩溃误标给被测应用。
CRASH_TAG_HITS = (
    "androidruntime", "libc", "debug", "crash_dump", "tombstoned",
    "activitymanager", "activitytaskmanager", "lmkd",
)
CRASH_CAPTURE_SECONDS = 8.0


class LogcatMonitor:
    """后台读取 logcat，缓存目标应用诊断事件及微信小游戏日志。"""

    def __init__(self, adb, serial="", tags=None, text_hits=None,
                 min_interval=1.0, target_package="", target_pid=None,
                 target_process=""):
        self._adb = adb                 # Adb 实例（复用其 adb 路径）
        self._serial = serial or getattr(adb, "serial", "")
        self._tags = tuple(tags) if tags else DEFAULT_TAGS
        self._hits = tuple(text_hits) if text_hits else DEFAULT_TEXT_HITS
        self._min_gap = min_interval    # 微信 console 同 tag+text 限流间隔（秒）
        self._proc = None
        self._thread = None
        self._stop = False
        self._events = []               # 待消费事件（加锁）
        self._lock = threading.Lock()
        self._anchor = None             # 采集启动时设备 epoch（秒）
        self._anchor_year = None        # 锚点对应年份（logcat 时间戳无年份，补锚点年）
        self._last_emit = {}            # 微信 console (tag, text) -> 最近发送时间
        self._target_package = (target_package or "").strip()
        self._target_pid = target_pid
        self._target_process = (target_process or "").strip()
        self._crash_capture_until = 0.0
        self._crash_type = None
        self.started = False

    # ---------------- 生命周期 ----------------
    def start(self):
        """启动 logcat 监听。取设备时间锚点 + 拉流。"""
        # 设备 epoch 秒锚点（与 logcat 行时间戳同基准，保证 t_ms 对齐）
        try:
            out = self._adb.shell(["date", "+%s"])
            self._anchor = float(out.strip())
        except Exception:
            self._anchor = time.time()
        # logcat 行时间戳无年份字段，取锚点年（跨 12/31 采集不会错一年，2026-08-21 修复）
        self._anchor_year = datetime.fromtimestamp(self._anchor).year
        cmd = [self._adb.adb]
        if self._serial:
            cmd += ["-s", self._serial]
        # -T 1：只读新日志，不 dump 历史 buffer；-v time 输出设备时间戳
        cmd += ["logcat", "-v", "time", "-T", "1"]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=1, text=True, encoding="utf-8", errors="replace")
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="logcat-monitor")
        self._stop = False
        self._thread.start()
        self.started = True

    def stop(self, timeout=1.0):
        """请求停止并有限等待读流线程，重复调用安全。"""
        self._stop = True
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))
        self.started = False

    # ---------------- 事件消费 ----------------
    def get_events(self):
        """取出自上次调用以来的新事件列表（清空缓存）。"""
        with self._lock:
            evs = self._events
            self._events = []
            return evs

    def update_target(self, package, pid=None, process_name=None):
        """更新崩溃日志关联目标；pid 暂失时保留最后确认值以接住死亡日志。"""
        package = (package or "").strip()
        with self._lock:
            if package != self._target_package:
                self._target_package = package
                self._target_pid = pid
                self._target_process = (process_name or "").strip()
                self._crash_capture_until = 0.0
                self._crash_type = None
            elif pid is not None:
                self._target_pid = pid
                if process_name:
                    self._target_process = process_name.strip()

    # ---------------- 内部 ----------------
    def _run(self):
        """读流主循环。USB 抖动导致进程退出时自动重连。"""
        while not self._stop:
            try:
                line = self._proc.stdout.readline()
            except Exception:
                line = ""
            if line:
                ev = self._parse(line)
                if ev:
                    with self._lock:
                        self._events.append(ev)
                continue
            # EOF：进程退出（USB 断/被杀）。未停止则 2s 后重启拉流
            if self._stop:
                break
            self._proc.terminate()
            time.sleep(2)
            if not self._stop:
                try:
                    cmd = [self._adb.adb]
                    if self._serial:
                        cmd += ["-s", self._serial]
                    cmd += ["logcat", "-v", "time", "-T", "1"]
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        bufsize=1, text=True, encoding="utf-8",
                        errors="replace")
                except Exception:
                    time.sleep(2)

    def _parse(self, line):
        """解析一行 logcat → 事件 dict（不符合过滤规则的返回 None）。"""
        m = _LINE_RE.match(line)
        if not m:
            return None
        date_s, hmss, pid_s, _tid_s, level, tag, text = m.groups()
        log_pid = int(pid_s)
        text = text.strip()
        tag_l = tag.lower()

        with self._lock:
            target_package = self._target_package
            target_pid = self._target_pid
            target_process = self._target_process

        text_l = text.lower()
        identity = target_process or target_package
        package_match = bool(identity and identity.lower() in text_l)
        pid_match = target_pid is not None and log_pid == target_pid
        text_pid_match = bool(
            target_pid is not None
            and re.search(r"\bpid\s*[:=]?\s*" + re.escape(str(target_pid)) + r"\b",
                          text, re.IGNORECASE)
        )
        related = package_match or pid_match or text_pid_match
        is_crash_tag = any(hit in tag_l for hit in CRASH_TAG_HITS)

        kind = crash_type = None
        if "fatal exception" in text_l or "am_crash" in text_l:
            kind, crash_type = "confirmed_crash", "java"
        elif "fatal signal" in text_l:
            kind, crash_type = "confirmed_crash", "native"
        elif "anr in " in text_l or "am_anr" in text_l:
            kind, crash_type = "anr", "anr"
        elif "lmkd" in tag_l and ("kill" in text_l or "killing" in text_l):
            kind, crash_type = "system_kill", "low_memory"
        elif "has died" in text_l and ("process" in text_l or "pid" in text_l):
            kind = "process_log"

        now = time.monotonic()
        if kind in ("confirmed_crash", "anr", "system_kill"):
            if not related:
                return None
            self._crash_capture_until = now + CRASH_CAPTURE_SECONDS
            self._crash_type = crash_type
        elif is_crash_tag and (related or now <= self._crash_capture_until):
            kind = kind or "crash_log"
            crash_type = crash_type or self._crash_type
        elif is_crash_tag:
            return None

        if kind is None:
            # 原有微信小游戏 console 事件：仅目标仍是微信时保留，普通 APK 不捞业务噪音。
            if target_package.lower() != "com.tencent.mm":
                return None
            if level in ("V", "D"):
                return None
            if not any(t in tag_l for t in self._tags):
                return None
            is_console = "console" in text_l or "info:console" in text_l
            if not (is_console or level in ("E", "F")
                    or any(h in text for h in self._hits)):
                return None
            kind = "app_log"

        # 时间对齐：logcat 时间戳 → 设备 epoch → 相对锚点的 t_ms
        # 年份取锚点年（设备时钟与电脑可能不同年，datetime.now().year 会错一年）
        t_ms = None
        try:
            year = self._anchor_year if self._anchor_year is not None \
                else datetime.now().year
            dt = datetime.strptime(
                f"{year}-{date_s} {hmss}",
                "%Y-%m-%d %H:%M:%S.%f")
            t_ms = (dt.timestamp() - self._anchor) * 1000
            if t_ms < 0:
                t_ms = 0
        except Exception:
            pass

        # 只限流高频业务 console。崩溃、ANR、系统回收及其堆栈必须逐行保留，
        # 否则相同异常在 1 秒内重复出现时会破坏诊断现场。
        if kind == "app_log":
            now = time.time()
            key = (tag, text[:80])
            last = self._last_emit.get(key, 0)
            if now - last < self._min_gap:
                return None
            self._last_emit[key] = now
            # 长采集大量唯一文本时定期裁剪，避免字典缓慢膨胀。
            if len(self._last_emit) > 500:
                self._last_emit.clear()

        event = {
            "t_ms": t_ms, "kind": kind, "target": target_package,
            "pid": log_pid, "tag": tag, "level": level, "text": text,
        }
        if target_process:
            event["process"] = target_process
        if crash_type:
            event["crash_type"] = crash_type
        return event


if __name__ == "__main__":
    # 独立调试：python logcat.py [serial]
    import sys
    from adb import Adb
    serial = sys.argv[1] if len(sys.argv) > 1 else ""
    adb = Adb(serial)
    mon = LogcatMonitor(adb, serial)
    mon.start()
    print("[+] logcat 监听中（Ctrl+C 停止），10 秒窗口…")
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            for ev in mon.get_events():
                t = ev["t_ms"] / 1000 if ev["t_ms"] is not None else -1
                print(f"[{t:8.1f}s] {ev['level']} {ev['tag']}: {ev['text']}")
            time.sleep(0.2)
    finally:
        mon.stop()
    print("[-] 监听结束")
