# -*- coding: utf-8 -*-
"""logcat 事件的懒打开、批量 JSONL 落盘。"""

import json


class JsonlEventSink:
    """单生产者事件 sink；零事件时不创建文件。"""

    DIAGNOSTIC_KINDS = {
        "confirmed_crash", "crash_log", "anr", "system_kill",
        "process_log", "process_exit", "process_restart",
    }

    def __init__(self, path, diagnostic_path=None):
        self.path = path
        self.diagnostic_path = diagnostic_path
        self.count = 0
        self.diagnostic_count = 0
        self._stream = None
        self._diagnostic_stream = None
        self._closed = False

    def write_many(self, events):
        if self._closed:
            raise RuntimeError("事件 sink 已关闭")
        events = list(events or [])
        lines = [json.dumps(event, ensure_ascii=False) + "\n"
                 for event in events]
        if not lines:
            return 0
        if self._stream is None:
            self._stream = open(self.path, "a", encoding="utf-8")
        self._stream.writelines(lines)
        self._stream.flush()
        self.count += len(lines)
        diagnostic = [event for event in events
                      if event.get("kind") in self.DIAGNOSTIC_KINDS]
        if diagnostic and self.diagnostic_path:
            if self._diagnostic_stream is None:
                self._diagnostic_stream = open(
                    self.diagnostic_path, "a", encoding="utf-8")
            for event in diagnostic:
                t_ms = event.get("t_ms")
                t_text = f"{float(t_ms) / 1000:.3f}s" if t_ms is not None else "?s"
                self._diagnostic_stream.write(
                    f"[{t_text}] [{event.get('kind', 'event')}] "
                    f"[{event.get('level', '-')}/{event.get('tag', '-')}] "
                    f"pid={event.get('pid', '-')} {event.get('text', '')}\n"
                )
            self._diagnostic_stream.flush()
            self.diagnostic_count += len(diagnostic)
        return len(lines)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._diagnostic_stream is not None:
            self._diagnostic_stream.close()
            self._diagnostic_stream = None


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
