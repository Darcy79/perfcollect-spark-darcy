# -*- coding: utf-8 -*-
"""性能时间轴区间标注的安全持久化。"""

import json
import math
import os
import re
import threading
import time
import uuid


MAX_ANNOTATIONS = 200
MAX_TEXT_LENGTH = 120
MAX_T_MS = 7 * 24 * 60 * 60 * 1000
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
LABEL_COLORS = ("#ef5664", "#4fc3f7", "#66bb6a", "#ffd54f", "#ab47bc", "#ff8a65")


class AnnotationStore:
    """按报告保存 ``<basename>.annotations.json``，线程安全且原子替换。"""

    def __init__(self, output_dir):
        self.output_dir = os.path.abspath(output_dir)
        self._lock = threading.Lock()

    def _report_path(self, name):
        if (not isinstance(name, str) or not name.endswith(".jsonl")
                or name.endswith(".events.jsonl") or len(name) > 1024):
            raise ValueError("bad name")
        base = os.path.realpath(self.output_dir)
        report = os.path.realpath(os.path.join(base, name))
        if not report.startswith(base + os.sep):
            raise ValueError("bad path")
        if not os.path.isfile(report):
            raise FileNotFoundError("no such file")
        return report

    @staticmethod
    def sidecar_path(report_path):
        return os.path.splitext(report_path)[0] + ".annotations.json"

    @staticmethod
    def load_path(path):
        try:
            with open(path, encoding="utf-8") as stream:
                payload = json.load(stream)
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            return []
        items = payload.get("annotations", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)][:MAX_ANNOTATIONS]

    def load(self, name):
        report = self._report_path(name)
        with self._lock:
            return self.load_path(self.sidecar_path(report))

    def create(self, name, start_ms, end_ms, text, color):
        report = self._report_path(name)
        try:
            start = int(float(start_ms))
            end = int(float(end_ms))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("时间范围无效")
        if not (math.isfinite(start) and math.isfinite(end)):
            raise ValueError("时间范围无效")
        if start < 0 or end <= start or end > MAX_T_MS:
            raise ValueError("结束时间必须晚于开始时间")
        note = str(text or "").replace("\r", " ").replace("\n", " ").strip()
        note = re.sub(r"\s+", " ", note)[:MAX_TEXT_LENGTH]
        if not note:
            raise ValueError("备注不能为空")
        shade = str(color or "").lower()
        if not _COLOR_RE.fullmatch(shade):
            raise ValueError("颜色无效")

        item = {
            "id": uuid.uuid4().hex[:12],
            "start_ms": start,
            "end_ms": end,
            "text": note,
            "color": shade,
            "created_at": round(time.time(), 3),
        }
        path = self.sidecar_path(report)
        with self._lock:
            items = self.load_path(path)
            if len(items) >= MAX_ANNOTATIONS:
                raise ValueError(f"单份报告最多 {MAX_ANNOTATIONS} 条区间标注")
            items.append(item)
            self._write(path, items)
        return item

    @staticmethod
    def _first_sample_ms(report):
        try:
            with open(report, encoding="utf-8") as stream:
                for line in stream:
                    try:
                        value = json.loads(line).get("t_ms")
                        if value is not None:
                            return max(0, int(float(value)))
                    except (AttributeError, json.JSONDecodeError, TypeError,
                            ValueError, OverflowError):
                        continue
        except OSError:
            pass
        return 0

    def pin(self, name, at_ms):
        """在当前采样时刻一键开始/切换 Label，并返回完整时间段列表。"""
        report = self._report_path(name)
        try:
            at = int(float(at_ms))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("打点时间无效")
        if at < 0 or at > MAX_T_MS:
            raise ValueError("打点时间无效")

        path = self.sidecar_path(report)
        with self._lock:
            items = self.load_path(path)
            needed = 2 if not items else 1
            if len(items) + needed > MAX_ANNOTATIONS:
                raise ValueError(f"单份报告最多 {MAX_ANNOTATIONS} 个 Label")
            # Label1 从采集首点天然开始；第一次点击表示切换到 Label2。
            if not items:
                first = self._first_sample_ms(report)
                if at <= first:
                    raise ValueError("请等待下一个采样点后再打点")
                items.append({
                    "id": uuid.uuid4().hex[:12],
                    "start_ms": first,
                    "end_ms": at,
                    "text": "Label1",
                    "color": LABEL_COLORS[0],
                    "created_at": round(time.time(), 3),
                })
            if items and items[-1].get("end_ms") is None:
                start = items[-1].get("start_ms")
                if not isinstance(start, (int, float)) or at <= start:
                    raise ValueError("请等待下一个采样点后再打点")
                items[-1]["end_ms"] = at
            index = len(items) + 1
            item = {
                "id": uuid.uuid4().hex[:12],
                "start_ms": at,
                "end_ms": None,
                "text": f"Label{index}",
                "color": LABEL_COLORS[(index - 1) % len(LABEL_COLORS)],
                "created_at": round(time.time(), 3),
            }
            items.append(item)
            self._write(path, items)
        return item, items

    def rename(self, name, annotation_id, text):
        """修改 Label 名称；不改变边界与自动分配的颜色。"""
        report = self._report_path(name)
        target = str(annotation_id or "")
        note = str(text or "").replace("\r", " ").replace("\n", " ").strip()
        note = re.sub(r"\s+", " ", note)[:MAX_TEXT_LENGTH]
        if not note:
            raise ValueError("Label 名称不能为空")
        path = self.sidecar_path(report)
        with self._lock:
            items = self.load_path(path)
            for item in items:
                if item.get("id") == target:
                    item["text"] = note
                    self._write(path, items)
                    return item, items
        raise ValueError("Label 不存在")

    def delete(self, name, annotation_id):
        report = self._report_path(name)
        target = str(annotation_id or "")
        path = self.sidecar_path(report)
        with self._lock:
            items = self.load_path(path)
            kept = [item for item in items if item.get("id") != target]
            if len(kept) == len(items):
                raise ValueError("标注不存在")
            self._write(path, kept)
        return True

    @staticmethod
    def _write(path, items):
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "annotations": items}, stream,
                          ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
