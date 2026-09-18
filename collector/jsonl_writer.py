# -*- coding: utf-8 -*-
"""单写线程 JSONL writer：统一串行化主采集文件的所有记录。"""

import json
import queue
import threading


_STOP = object()


class JsonlWriter:
    """把多线程提交的 dict 按接收顺序写入同一个 JSONL 文件。

    ``write`` 在调用线程完成 JSON 序列化，因此非法对象会立即报错；文件 I/O 由唯一
    后台线程执行。``close`` 会排空此前接受的记录、flush 并等待线程退出。
    """

    def __init__(self, path):
        self.path = path
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread = None
        self._state = "new"
        self._error = None
        self._sequence = 0
        self._written = 0

    @property
    def written_count(self):
        with self._lock:
            return self._written

    def start(self):
        with self._lock:
            if self._state != "new":
                raise RuntimeError("JsonlWriter 只能启动一次")
            self._state = "open"
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="jsonl-writer")
            self._thread.start()
        self._ready.wait()
        try:
            self._raise_if_failed()
        except OSError:
            # open 失败时后台线程正在等待 STOP；主动收口，不能泄漏守护线程。
            try:
                self.close()
            except OSError:
                pass
            self._raise_if_failed()
        return self

    def _raise_if_failed(self):
        with self._lock:
            error = self._error
        if error is not None:
            raise OSError(f"JSONL 写入失败: {error}") from error

    def write(self, record, flush=False):
        """提交一条记录，返回进程内单调递增的写入序号。"""
        line = json.dumps(record, ensure_ascii=False) + "\n"
        done = threading.Event() if flush else None
        with self._lock:
            if self._state != "open":
                raise RuntimeError("JsonlWriter 未启动或已关闭")
            if self._error is not None:
                error = self._error
                raise OSError(f"JSONL 写入失败: {error}") from error
            self._sequence += 1
            sequence = self._sequence
            self._queue.put(("line", sequence, line, done))
        if done is not None:
            done.wait()
            self._raise_if_failed()
        return sequence

    def flush(self):
        """等待此前提交的记录全部写入并刷盘。"""
        done = threading.Event()
        with self._lock:
            if self._state != "open":
                raise RuntimeError("JsonlWriter 未启动或已关闭")
            self._queue.put(("flush", done))
        done.wait()
        self._raise_if_failed()

    def close(self):
        """排空队列并关闭文件；重复关闭安全。"""
        with self._lock:
            if self._state == "closed":
                error = self._error
                if error is not None:
                    raise OSError(f"JSONL 写入失败: {error}") from error
                return
            if self._state == "new":
                self._state = "closed"
                return
            if self._state == "open":
                self._state = "closing"
                self._queue.put(_STOP)
            thread = self._thread
        if thread is not None:
            thread.join()
        with self._lock:
            self._state = "closed"
        self._raise_if_failed()

    def _run(self):
        item = None
        try:
            with open(self.path, "w", encoding="utf-8") as stream:
                self._ready.set()
                while True:
                    item = self._queue.get()
                    if item is _STOP:
                        stream.flush()
                        return
                    if item[0] == "flush":
                        stream.flush()
                        item[1].set()
                        continue
                    _, _, line, done = item
                    stream.write(line)
                    if done is not None:
                        stream.flush()
                    with self._lock:
                        self._written += 1
                    if done is not None:
                        done.set()
        except Exception as error:
            with self._lock:
                self._error = error
            self._ready.set()
            if item is _STOP:
                return
            if item is not None and item is not _STOP and item[0] == "line" and item[3] is not None:
                item[3].set()
            # 继续消费队列，释放 flush 等待者并等 close 的 STOP，避免调用方死锁。
            while True:
                item = self._queue.get()
                if item is _STOP:
                    return
                if item[0] == "flush":
                    item[1].set()
                elif item[0] == "line" and item[3] is not None:
                    item[3].set()

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False
