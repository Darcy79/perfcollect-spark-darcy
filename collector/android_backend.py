# -*- coding: utf-8 -*-
"""Android 目标装配：首次采集与热切换共用，不启动 worker 或选择探针。"""

from metrics.cpu import CpuCollector
from metrics.fps import FpsCollector
from metrics.mem import MemCollector
from metrics.network import NetworkCollector
from pidresolver import PidResolver


def create_resolver(adb, package, pattern, fixed_pid=None, fixed_name=None):
    return PidResolver(adb, package, pattern,
                       fixed_pid=fixed_pid, fixed_name=fixed_name)


def create_target_collectors(adb, package, pattern, resolver):
    # resolve() 由调用方先执行；温度属于设备，仍由 main 创建并跨目标保留。
    return {
        "fps": FpsCollector(adb, package, pattern),
        "cpu": CpuCollector(adb, resolver),
        "mem": MemCollector(adb, resolver, package, min_interval=0),
        "net": NetworkCollector(adb, resolver),
    }
