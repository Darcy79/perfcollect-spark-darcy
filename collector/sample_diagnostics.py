"""Bounded, read-only projection of per-sample evidence for history reports."""
import json
import math
from collections import deque

LINE_LIMIT = 256 * 1024
SCAN_LIMIT = 64 * 1024 * 1024
CPU_LIMIT = 3000
FRAME_LIMIT = 100


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def records(path, state, budget=SCAN_LIMIT):
    with open(path, "rb") as stream:
        while state["bytes"] < budget:
            line = stream.readline(min(LINE_LIMIT + 1, budget - state["bytes"]))
            if not line:
                return
            state["bytes"] += len(line)
            if not line.endswith(b"\n"):
                state["complete"] = False
                state["reason"] = "line_or_scan_budget_or_partial_tail"
                return
            if len(line) > LINE_LIMIT:
                state["complete"] = False
                state["reason"] = "line_budget"
                return
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("object required")
            except (ValueError, UnicodeError):
                state["invalid_rows"] += 1
                continue
            yield row
        if stream.read(1):
            state["complete"] = False
            state["reason"] = "scan_budget"


def load_diagnostics(report_path, sample_path):
    anchor = None
    anchor_state = {"bytes": 0, "complete": True, "invalid_rows": 0}
    for row in records(report_path, anchor_state, budget=1024 * 1024):
        if number(row.get("ts")) and number(row.get("t_ms")):
            anchor = row["ts"] - row["t_ms"] / 1000
            break
    state = {"bytes": 0, "complete": True, "invalid_rows": 0}
    cpu, frames = deque(maxlen=CPU_LIMIT), deque(maxlen=FRAME_LIMIT)
    counts = {"cpu": 0, "long_frames": 0, "coverage_windows": 0, "untimed_samples": 0}
    for row in records(sample_path, state):
        if row.get("event") != "metric_sample":
            continue
        timing = row.get("timing") or {}
        if not isinstance(timing, dict):
            state["invalid_rows"] += 1
            continue
        host = timing.get("host_received_at", timing.get("host_completed_at"))
        t_ms = round((host - anchor) * 1000, 3) if number(host) and anchor is not None else None
        if not number(t_ms):
            t_ms = None
            counts["untimed_samples"] += 1
        common = {"t_ms": t_ms,
                  "target_generation": row.get("target_generation") if type(row.get("target_generation")) is int else None,
                  "backend": str(row.get("backend") or "")[:64],
                  "seq": row.get("seq") if type(row.get("seq")) is int else None}
        if row.get("metric") == "cpu":
            data = row.get("data") or {}
            if not isinstance(data, dict):
                state["invalid_rows"] += 1
                continue
            cpu.append({**common, "pid": data.get("pid") if type(data.get("pid")) is int else None, "cpu_proc_pct": data.get("cpu_proc_pct") if number(data.get("cpu_proc_pct")) else None,
                        "cpu_total_pct": data.get("cpu_total_pct") if number(data.get("cpu_total_pct")) else None,
                        "error": str(data.get("error") or "")[:160]})
            counts["cpu"] += 1
        if row.get("metric") != "fps":
            continue
        raw = row.get("frames") or {}
        if not isinstance(raw, dict) or not raw.get("available"):
            continue
        flags = raw.get("coverage_flags") or []
        counts["coverage_windows"] += bool(flags)
        stamps, intervals = raw.get("timestamps_ns"), raw.get("intervals_ns")
        if not isinstance(stamps, list) or not isinstance(intervals, list) or len(stamps) != len(intervals):
            state["invalid_rows"] += 1
            continue
        for stamp, interval in zip(stamps, intervals):
            if interval is None:
                continue
            try:
                if not isinstance(interval, str) or len(interval) > 32 or not interval.isdecimal() or not isinstance(stamp, str) or len(stamp) > 32 or not stamp.isdecimal():
                    raise ValueError("nanoseconds must be decimal strings")
                duration = int(interval)
                if duration < 50_000_000:
                    continue
            except ValueError:
                state["invalid_rows"] += 1
                continue
            frames.append({**common, "interval_ms": duration / 1_000_000,
                           "device_present_ns": stamp, "layer": str(raw.get("layer") or "")[:256],
                           "layer_generation": raw.get("layer_generation") if type(raw.get("layer_generation")) is int else None,
                           "coverage_flags": str(flags)[:256]})
            counts["long_frames"] += 1
    return {"available": True, "cpu": list(cpu), "long_frames": list(frames),
            "counts": counts, "scan": state, "anchor_available": anchor is not None,
            "limits": {"cpu": CPU_LIMIT, "long_frames": FRAME_LIMIT, "scan_bytes": SCAN_LIMIT},
            "time_semantics": "host_receive_or_query_completion; SF present clock is not aligned",
            "long_frame_threshold_ms": 50}
