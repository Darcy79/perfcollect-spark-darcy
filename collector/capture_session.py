# -*- coding: utf-8 -*-
"""采集会话生命周期：停止、退出和工作线程归属。"""

import threading
import time


class CaptureSession:
    """用 Event 统一 Ctrl+C、Web 停止与后台线程的生命周期。"""

    def __init__(self):
        self._stop_event = threading.Event()
        self._shutdown_event = threading.Event()
        self._threads = []
        self._threads_lock = threading.Lock()

    @property
    def is_stopping(self):
        return self._stop_event.is_set()

    @property
    def shutdown_requested(self):
        return self._shutdown_event.is_set()

    def request_stop(self):
        """请求结束采集；重复调用安全。"""
        self._stop_event.set()

    def request_shutdown(self):
        """请求采集结束后退出整个程序。"""
        self._shutdown_event.set()
        self._stop_event.set()

    def wait(self, timeout):
        """可被停止请求立即唤醒的等待；返回是否已请求停止。"""
        return self._stop_event.wait(max(0.0, float(timeout)))

    def start_worker(self, target, args=(), name=None):
        """启动并登记一个 daemon 工作线程。"""
        thread = threading.Thread(
            target=target, args=args, daemon=True, name=name)
        with self._threads_lock:
            if self._stop_event.is_set():
                raise RuntimeError("采集会话已停止，不能再启动工作线程")
            self._threads.append(thread)
            thread.start()
        return thread

    def join_workers(self, timeout=1.0):
        """在总时间预算内等待工作线程退出，返回仍存活的线程名。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._threads_lock:
            threads = list(self._threads)
        current = threading.current_thread()
        for thread in threads:
            if thread is current or not thread.is_alive():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)
        return [thread.name for thread in threads if thread.is_alive()]
