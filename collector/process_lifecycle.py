# -*- coding: utf-8 -*-
"""目标进程生命周期状态沿。

PID 消失本身不能证明闪退：也可能是用户退出、系统回收或 adb 身份读取失败。
本模块只陈述可确认事实（退出 / 重新出现），崩溃归因由 logcat 明确信号完成。
"""


class ProcessLifecycleTracker:
    """把目标包名与 PID 快照转换为去重的生命周期事件。"""

    def __init__(self, package=None, pid=None):
        self._package = package
        self._last_pid = pid
        self._exited_pid = None
        self._missing = False

    def update(self, package, pid, t_ms):
        package = (package or "").strip()
        if package != self._package:
            self._package = package
            self._last_pid = pid
            self._exited_pid = None
            self._missing = False
            return None

        if pid == self._last_pid and not (pid is None and self._missing):
            return None

        if pid is None:
            if self._last_pid is None or self._missing:
                return None
            old_pid = self._last_pid
            self._last_pid = None
            self._exited_pid = old_pid
            self._missing = True
            return {
                "t_ms": round(float(t_ms), 1),
                "kind": "process_exit",
                "level": "W",
                "target": package,
                "pid": old_pid,
                "reason": "unknown",
                "text": f"目标进程已退出或不可见：{package} pid={old_pid}（原因待日志确认）",
            }

        if self._missing:
            old_pid = self._exited_pid
            self._last_pid = pid
            self._exited_pid = None
            self._missing = False
            return self._restart_event(package, old_pid, pid, t_ms)

        if self._last_pid is not None and pid != self._last_pid:
            old_pid = self._last_pid
            self._last_pid = pid
            return self._restart_event(package, old_pid, pid, t_ms)

        # 采集启动时尚未解析到进程，首次出现只建立基线，不误报“重启”。
        self._last_pid = pid
        self._missing = False
        return None

    @staticmethod
    def _restart_event(package, old_pid, new_pid, t_ms):
        return {
            "t_ms": round(float(t_ms), 1),
            "kind": "process_restart",
            "level": "W",
            "target": package,
            "old_pid": old_pid,
            "pid": new_pid,
            "text": f"目标进程重新出现：{package} pid={old_pid or '-'} → {new_pid}",
        }
