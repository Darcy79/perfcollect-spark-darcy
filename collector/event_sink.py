# -*- coding: utf-8 -*-
"""logcat 事件的懒打开、批量 JSONL 落盘。"""

import json


class JsonlEventSink:
    """单生产者事件 sink；零事件时不创建文件。"""

    def __init__(self, path):
        self.path = path
        self.count = 0
        self._stream = None
        self._closed = False

    def write_many(self, events):
        if self._closed:
            raise RuntimeError("事件 sink 已关闭")
        lines = [json.dumps(event, ensure_ascii=False) + "\n"
                 for event in (events or [])]
        if not lines:
            return 0
        if self._stream is None:
            self._stream = open(self.path, "a", encoding="utf-8")
        self._stream.writelines(lines)
        self._stream.flush()
        self.count += len(lines)
        return len(lines)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._stream is not None:
            self._stream.close()
            self._stream = None


def stop_and_drain_event_capture(monitor, sink):
    """先停止生产者，再写尾部事件，最后保证关闭 sink。"""
    try:
        if monitor is not None:
            monitor.stop()
            if sink is not None:
                sink.write_many(monitor.get_events())
    finally:
        if sink is not None:
            sink.close()
