# -*- coding: utf-8 -*-
"""被测目标热切换编排。

把配置持久化、目标相关采集器重建、TargetContext 原子切换和 Web 状态刷新
从 main.py 的回调闭包中收口；温度采集器由 TargetContext 保留，不随应用切换。
"""

import time

from metrics.cpu import CpuCollector
from metrics.fps import FpsCollector
from metrics.mem import MemCollector
from metrics.network import NetworkCollector
from pidresolver import PidResolver
from runtime_config import persist_runtime_target


def _target_collectors(adb, package, pattern, resolver):
    return {
        "fps": FpsCollector(adb, package, pattern),
        "cpu": CpuCollector(adb, resolver),
        "mem": MemCollector(adb, resolver, package, min_interval=0),
        "net": NetworkCollector(adb, resolver),
    }


class TargetSwitcher:
    """执行一次热切换；公开 ``apply`` 可直接注册为 Web 回调。"""

    def __init__(self, adb, config, config_path, target_context, mailbox,
                 writer, web, resolver_factory=PidResolver,
                 collectors_factory=_target_collectors, clock=time.time,
                 log=print):
        self.adb = adb
        self.config = config
        self.config_path = config_path
        self.target_context = target_context
        self.mailbox = mailbox
        self.writer = writer
        self.web = web
        self.resolver_factory = resolver_factory
        self.collectors_factory = collectors_factory
        self.clock = clock
        self.log = log

    def _persist(self, package, pattern):
        """原子更新本机目标选择，写入失败不阻塞本次内存态切换。"""
        self.config["package"] = package
        self.config["process_pattern"] = pattern
        try:
            persist_runtime_target(self.config_path, package, pattern)
        except Exception as exc:
            self.log(f"[!] 目标持久化失败（不影响本次切换）: {exc}")

    def apply(self, new_package, new_pattern=""):
        package = (new_package or "").strip()
        pattern = new_pattern or ""
        if not package:
            return False, "包名为空"

        self._persist(package, pattern)
        try:
            resolver = self.resolver_factory(self.adb, package, pattern)
            pid = resolver.resolve()
            switch_event = {
                "ts": round(self.clock(), 3),
                "event": "target_switch",
                "to": package,
                "process_pattern": pattern,
            }
            collectors = self.collectors_factory(
                self.adb, package, pattern, resolver)
            state = self.target_context.switch_target(
                package, pattern, resolver, pid, collectors, self.mailbox,
                before_switch=lambda _old: self.writer.write(switch_event),
            )
            self.web.clear_latest()
            self.web.set_status(
                pid=state.pid, target=state.package,
                process_pattern=state.process_pattern)
            try:
                self.writer.flush()
            except Exception as exc:
                self.log(f"[!] 目标切换标记写入失败: {exc}")
            message = f"目标已切换为 {package}"
            if not state.pid:
                message += "（未找到进程，请确认应用已在前台打开）"
            self.log(f"[>] {message}")
            return True, message
        except Exception as exc:
            return False, f"切换失败: {exc}"
