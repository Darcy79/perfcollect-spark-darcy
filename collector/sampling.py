# -*- coding: utf-8 -*-
"""采样线程与落盘主循环之间的线程安全 mailbox。"""

from collections import deque
import threading
import time


def sampler_error(key, error):
    """把采样器未捕获异常转换成所有诊断链路都能识别的统一结构。"""
    return {"error": f"{key}_exception", "detail": str(error)}


def sample_metric_once(target_context, mailbox, key, clock=time.time):
    """执行一次指标采样，并以“完成时刻”发布到当前目标代次。

    传给 collector 的时间仍是开始时刻，保持 CPU/网络差值算法语义；mailbox 的
    sampled_at 使用完成时刻，表示结果真正可用的时间。
    """
    started_at = clock()
    sampler, generation = target_context.capture_sampler(key)
    try:
        value = sampler.sample(started_at)
    except Exception as error:
        value = sampler_error(key, error)
    completed_at = clock()
    published = target_context.publish_if_current(
        mailbox, key, value, completed_at, generation)
    return {
        "started_at": started_at,
        "completed_at": completed_at,
        "published": published,
        "value": value,
    }


class SamplerScheduler:
    """统一编排各指标的独立周期 worker。

    CaptureSession 仍只负责停止信号和线程归属；本类只负责
    “采一次→扣除本次耗时→可中断等待”的采样节奏。
    """

    def __init__(self, session, target_context, mailbox, intervals,
                 sample_once=sample_metric_once, clock=time.time,
                 minimum_delay=0.05):
        self._session = session
        self._target_context = target_context
        self._mailbox = mailbox
        self._intervals = dict(intervals)
        self._sample_once = sample_once
        self._clock = clock
        self._minimum_delay = float(minimum_delay)

    def _run_metric(self, key, interval):
        while not self._session.is_stopping:
            result = self._sample_once(
                self._target_context, self._mailbox, key)
            delay = max(
                self._minimum_delay,
                interval - (self._clock() - result["started_at"]),
            )
            self._session.wait(delay)

    def start(self):
        """启动尚未停止的指标 worker，返回已启动线程列表。"""
        threads = []
        for key, interval in self._intervals.items():
            if self._session.is_stopping:
                break
            try:
                thread = self._session.start_worker(
                    self._run_metric,
                    args=(key, interval),
                    name=f"sampler-{key}",
                )
            except RuntimeError:
                # stop 可能在上面判断后、start_worker 加锁前到达。
                if not self._session.is_stopping:
                    raise
                break
            threads.append(thread)
        return threads


class MetricMailbox:
    """保存各指标最新值，并为指定指标保留尚未落盘的完整窗口。

    latest 用于兼容现有一行一个聚合点的 JSONL；pending 用于避免高频指标在
    两次主循环快照之间被覆盖。队列达到上限时丢最旧值并累计 dropped，调用方
    可以把丢弃数写入数据，不能静默丢样本。
    """

    def __init__(self, pending_limits=None):
        self._lock = threading.Lock()
        self._latest = {}
        self._seq = {}
        self._pending = {}
        self._dropped = {}
        for key, limit in (pending_limits or {}).items():
            limit = int(limit)
            if limit <= 0:
                raise ValueError("pending limit 必须大于 0")
            self._pending[key] = deque(maxlen=limit)
            self._dropped[key] = 0

    def publish(self, key, value, sampled_at):
        """发布一次采样并返回该指标在当前会话中的递增序号。"""
        with self._lock:
            seq = self._seq.get(key, 0) + 1
            self._seq[key] = seq
            item = {"seq": seq, "sampled_at": float(sampled_at), "value": value}
            self._latest[key] = item
            pending = self._pending.get(key)
            if pending is not None:
                if len(pending) == pending.maxlen:
                    self._dropped[key] += 1
                pending.append(item)
            return seq

    def snapshot(self, keys, drain_keys=()):
        """原子读取最新值，并取走 drain_keys 尚未落盘的窗口。"""
        with self._lock:
            latest = {key: self._latest[key] for key in keys if key in self._latest}
            drained = {}
            dropped = {}
            for key in drain_keys:
                pending = self._pending.get(key)
                if pending is None:
                    continue
                drained[key] = list(pending)
                pending.clear()
                dropped[key] = self._dropped.get(key, 0)
                self._dropped[key] = 0
            return latest, drained, dropped

    def clear(self):
        """清除最新值和待落盘窗口；序号保持递增，便于识别会话内切换边界。"""
        with self._lock:
            self._latest.clear()
            for key, pending in self._pending.items():
                pending.clear()
                self._dropped[key] = 0


def serialize_windows(items, capture_start):
    """把 mailbox 内部窗口转换成可落 JSONL 的稳定结构。"""
    out = []
    for item in items or []:
        sampled_t_ms = max(0.0, (item["sampled_at"] - capture_start) * 1000.0)
        out.append({
            "seq": item["seq"],
            "sampled_t_ms": round(sampled_t_ms, 1),
            "data": item["value"],
        })
    return out


def describe_metric_item(item, captured_at, capture_start, previous_seq=None):
    """生成 latest 指标的新鲜度元数据，不修改 mailbox 内部对象。"""
    sampled_at = float(item["sampled_at"])
    seq = item["seq"]
    return {
        "seq": seq,
        "sampled_at_ms": round(max(0.0, sampled_at - capture_start) * 1000.0, 1),
        "age_ms": round(max(0.0, captured_at - sampled_at) * 1000.0, 1),
        "is_reused": previous_seq == seq,
    }


def summarize_fps_windows(items):
    """聚合一次主循环内的 FPS 短窗，不对百分位做无效的简单平均。

    Jank 用采集器提供的 count/total 精确合并；P95/Max 只在 jank_total>0
    （本窗口确有新增帧间隔）时取窗口峰值，避免把沿用的旧帧时间重复计入。
    """
    items = list(items or [])
    if not items:
        return None
    jank_count = 0
    jank_total = 0
    p95_values = []
    max_values = []
    fresh_windows = 0
    for item in items:
        value = item.get("value") or {}
        count = value.get("jank_count")
        total = value.get("jank_total")
        if not isinstance(count, (int, float)) or not isinstance(total, (int, float)) or total <= 0:
            continue
        fresh_windows += 1
        jank_count += max(0, count)
        jank_total += total
        p95 = value.get("frame_p95_ms")
        frame_max = value.get("frame_max_ms")
        if isinstance(p95, (int, float)):
            p95_values.append(p95)
        if isinstance(frame_max, (int, float)):
            max_values.append(frame_max)
    out = {
        "window_count": len(items),
        "fresh_window_count": fresh_windows,
        "jank_count": jank_count,
        "jank_total": jank_total,
    }
    if jank_total > 0:
        out["jank_rate"] = round(jank_count / jank_total, 4)
    if p95_values:
        out["frame_p95_peak_ms"] = max(p95_values)
    if max_values:
        out["frame_max_peak_ms"] = max(max_values)
    return out


class SampleAggregator:
    """将同一目标代次的 mailbox 快照转换为稳定 JSONL 采样行。"""

    def __init__(self, capture_start):
        self._capture_start = float(capture_start)
        self._last_row_seq = {}

    def build(self, captured_at, target, latest, pending=None, dropped=None):
        captured_at = float(captured_at)
        pending = pending or {}
        dropped = dropped or {}
        row = {
            "ts": round(captured_at, 3),
            "t_ms": round((captured_at - self._capture_start) * 1000, 1),
            "target": target,
        }
        metric_meta = {}
        for key, item in latest.items():
            row[key] = item["value"]
            metric_meta[key] = describe_metric_item(
                item, captured_at, self._capture_start,
                self._last_row_seq.get(key),
            )
            self._last_row_seq[key] = item["seq"]
        if metric_meta:
            row["metric_meta"] = metric_meta

        fps_pending = pending.get("fps")
        fps_windows = serialize_windows(fps_pending, self._capture_start)
        if fps_windows:
            row["fps_windows"] = fps_windows
            row["fps_window_summary"] = summarize_fps_windows(fps_pending)
        if dropped.get("fps"):
            row["fps_windows_dropped"] = dropped["fps"]
        return row
