# -*- coding: utf-8 -*-
"""Bounded per-sample evidence, separate from report/display aggregation."""

import json

from jsonl_writer import JsonlWriter


class SampleRecording:
    """One writer owns the sidecar; publication is ordered by TargetContext.

    Host wall/monotonic clocks and the SF clock are separate domains. No
    device-to-host anchor or precision claim is fabricated by this protocol.
    """

    def __init__(self, path, sampling_info=None, **writer_options):
        self.writer = JsonlWriter(path, **({"capacity": 64} | writer_options))
        self._sampling_info = sampling_info
        self._error = None
        self._sequence = 0

    def start(self):
        self.writer.start()
        try:
            self.writer.write({
                "event": "meta", "schema_version": 1,
                "sampling": self._sampling_info,
                "recording": "per_sample", "backend": "per_metric",
                "sampled_at_semantics": "host_query_completion",
                "device_to_host_anchor": None,
                "clock_alignment_error_ns": None,
                "frame_interval_semantics": "observed_present_interval",
                "budgets": self.writer.stats,
            }, flush=True)
        except Exception:
            self.writer.close()
            raise
        return self

    def record(self, metric, value, target, recording):
        if self._error is not None:
            return
        try:
            sequence = self._sequence + 1
            self.writer.write({
                "event": "metric_sample", "seq": sequence, "metric": metric,
                "backend": "adb_query", "target": target.package,
                "process_pattern": target.process_pattern,
                "pid_observed_at_publish": target.pid,
                "target_generation": target.generation,
                "data": value, **recording,
            })
            self._sequence = sequence
        except Exception as error:
            # The main loop and close both surface this failure. A worker must
            # not silently disappear while the capture still looks successful.
            self._error = error

    def check(self):
        if self._error is not None:
            raise OSError(f"Per-sample recording failed: {self._error}") from self._error
        self.writer._raise_if_failed()

    def close(self, unfinished_workers=(), publication=None, sampling_runtime=None):
        try:
            self.check()
            self.writer.flush()
            self.writer.write({
                "sampling_runtime": sampling_runtime,
                "event": "capture_end", "samples": self._sequence,
                "unfinished_workers": list(unfinished_workers),
                "publication": publication or {},
                "writer": self.writer.stats,
            })
        finally:
            self.writer.close()


def finish_sample_recording(session, target_context, recording, timeout=1.0, scheduler=None, cpu_backend=None):
    """Stop, bounded join, seal publication, then drain all accepted records."""
    session.request_stop()
    unfinished = session.join_workers(timeout=timeout)
    publication = target_context.seal_publication()
    runtime = scheduler.snapshot() if scheduler else None
    if cpu_backend:
        runtime = runtime or {}
        runtime["cpu_backend"] = cpu_backend.snapshot()
    recording.close(unfinished, publication, runtime)


def iter_samples(path):
    """Stream evidence without materializing a full capture in memory."""
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                if row.get("event") == "metric_sample":
                    yield row
