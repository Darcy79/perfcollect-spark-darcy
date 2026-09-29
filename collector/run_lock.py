# -*- coding: utf-8 -*-
"""跨进程标记正在写入的报告，供独立历史看板安全判断改名时机。"""

import os
import re


_RUN_ID = re.compile(r"^\d{8}_\d{6}$")


def _lock(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _path(output_dir, run_id):
    if not _RUN_ID.fullmatch(run_id or ""):
        raise ValueError("run_id 无效")
    return os.path.join(os.path.realpath(output_dir), ".capture-locks", run_id + ".lock")


class CaptureRunLock:
    """采集开始即持有独占锁，直到报告产物全部收尾。"""

    def __init__(self, output_dir, run_id):
        self.path = _path(output_dir, run_id)
        self._stream = None

    def acquire(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        stream = open(self.path, "a+b")
        try:
            if os.path.getsize(self.path) == 0:
                stream.write(b"0")
                stream.flush()
            _lock(stream)
        except Exception:
            stream.close()
            raise
        self._stream = stream
        return self

    def release(self):
        if self._stream is not None:
            try:
                _unlock(self._stream)
            finally:
                self._stream.close()
                self._stream = None


def is_run_active(output_dir, run_id):
    """无锁文件或锁已释放即为非采集中；无法检查时保守拒绝改名。"""
    path = _path(output_dir, run_id)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r+b") as stream:
            _lock(stream)
            _unlock(stream)
        return False
    except OSError:
        return True
