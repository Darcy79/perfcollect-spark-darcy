# -*- coding: utf-8 -*-
"""Independent query cadence configuration; never writes runtime target files."""
import json
import math

DEFAULT_INTERVALS = {"fps": 0.5, "cpu": 1.0, "mem": 2.0, "net": 1.0, "therm": 2.0}
# Query backend limits. Faster device-probe modes need their own validation.
MIN_INTERVALS = {**DEFAULT_INTERVALS, "cpu": 0.3}


def resolve_sampling_intervals(overrides=None):
    if overrides is None:
        return dict(DEFAULT_INTERVALS)
    if not isinstance(overrides, dict):
        raise ValueError("采样节奏配置必须是指标名到秒数的 JSON 对象")
    unknown = set(overrides) - set(DEFAULT_INTERVALS)
    if unknown:
        raise ValueError("未知采样指标: " + ", ".join(sorted(unknown)))
    intervals = dict(DEFAULT_INTERVALS)
    for key, value in overrides.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} 周期必须是数字（秒）")
        if not MIN_INTERVALS[key] <= value <= 60 or not math.isfinite(value):
            raise ValueError(f"{key} 周期必须在 {MIN_INTERVALS[key]} 到 60 秒之间")
        intervals[key] = float(value)
    return intervals


def load_sampling_intervals(path=None):
    if path is None:
        return resolve_sampling_intervals()
    with open(path, encoding="utf-8-sig") as stream:
        payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("采样节奏配置必须是 JSON 对象")
        return resolve_sampling_intervals(payload)


def sampling_descriptor(intervals, aggregate_interval):
    return {"backend": "adb_query", "streaming": False,
            "device_clock_aligned": False, "scheduler_clock": "host_monotonic",
            "intervals_s": dict(intervals), "aggregate_interval_s": aggregate_interval,
            "profile": "baseline" if intervals == DEFAULT_INTERVALS else "custom",
            "experimental_cpu_cadence": intervals["cpu"] < DEFAULT_INTERVALS["cpu"]}
