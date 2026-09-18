# -*- coding: utf-8 -*-
"""采集目标的线程安全状态边界。"""

from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class TargetSnapshot:
    """某一目标代次的不可变快照。"""

    package: str
    process_pattern: str
    pid: object
    proc_name: object
    resolver: object
    generation: int


class TargetContext:
    """原子管理目标身份、resolver、采集器和热切换代次。

    worker 在采样前领取 ``(sampler, generation)``，完成后仅在代次仍一致时
    发布结果；主循环则在同一把锁下取得 mailbox 快照和目标标签。因此慢采样
    跨越热切换时，不会被误标成新目标的数据。
    """

    def __init__(self, package, process_pattern, resolver, pid, collectors):
        self._lock = threading.Lock()
        self._package = package
        self._process_pattern = process_pattern or ""
        self._resolver = resolver
        self._pid = pid
        self._collectors = dict(collectors)
        self._generation = 0

    def _snapshot_unlocked(self):
        # resolver 会在目标进程重启后更新 pid；优先读取其实时值，构造参数仅作兜底。
        current_pid = getattr(self._resolver, "pid", self._pid)
        return TargetSnapshot(
            package=self._package,
            process_pattern=self._process_pattern,
            pid=current_pid,
            proc_name=getattr(self._resolver, "proc_name", None),
            resolver=self._resolver,
            generation=self._generation,
        )

    def state(self):
        """返回当前目标的不可变快照。"""
        with self._lock:
            return self._snapshot_unlocked()

    def capture_sampler(self, key):
        """为一次采样领取采集器与对应目标代次。"""
        with self._lock:
            return self._collectors[key], self._generation

    def publish_if_current(self, mailbox, key, value, sampled_at, generation):
        """仅发布仍属于当前目标代次的采样结果。"""
        with self._lock:
            if generation != self._generation:
                return False
            mailbox.publish(key, value, sampled_at)
            return True

    def snapshot_mailbox(self, mailbox, keys, drain_keys=()):
        """原子取得指标快照及其目标身份。"""
        with self._lock:
            latest, pending, dropped = mailbox.snapshot(keys, drain_keys)
            return latest, pending, dropped, self._snapshot_unlocked()

    def switch_target(self, package, process_pattern, resolver, pid,
                      collectors, mailbox, before_switch=None):
        """原子切换目标，并清除上一目标尚未消费的指标。

        ``before_switch(old_snapshot)`` 在持锁且新目标尚不可见时执行，可先把
        target_switch 事件提交给唯一 writer，保证任何新目标采样行都排在事件之后。
        """
        package = (package or "").strip()
        if not package:
            raise ValueError("包名为空")
        with self._lock:
            if before_switch is not None:
                before_switch(self._snapshot_unlocked())
            self._generation += 1
            self._package = package
            self._process_pattern = process_pattern or ""
            self._resolver = resolver
            self._pid = pid
            self._collectors.update(collectors)
            mailbox.clear()
            return self._snapshot_unlocked()
