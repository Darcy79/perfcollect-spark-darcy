# -*- coding: utf-8 -*-
"""Optional CPU stream backend: owned deployment, bounded transport and fallback."""
import json
import math
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

from sampling import sample_metric_once


class ProbeError(RuntimeError):
    pass


def validate_probe_options(path, interval):
    if not math.isfinite(interval) or not .25 <= interval <= 60:
        raise ValueError("探针周期必须在 0.25 到 60 秒之间")
    if path:
        probe_artifact_kind(path)


def probe_artifact_kind(path):
    """Accept our DEX or ARM little-endian executable; deployment failures fall back."""
    with open(path, "rb") as stream:
        header = stream.read(64)
    if header.startswith(b"dex\n"):
        return "java_dex"
    if (len(header) >= 52 and header[:4] == b"\x7fELF" and header[5:7] == b"\x01\x01"
            and int.from_bytes(header[16:18], "little") in (2, 3)
            and ((header[4] == 2 and len(header) >= 64
                  and int.from_bytes(header[18:20], "little") == 183)
                 or (header[4] == 1 and int.from_bytes(header[18:20], "little") == 40))):
        return "native_c"
    raise ValueError("CPU probe must be an own-source DEX or ARM ELF executable")



def select_cpu_probe(adb, requested="auto", artifact_dir=None):
    """Select a bundled device ABI; preflight failures retain the query backend."""
    selection = {"mode": "auto" if requested == "auto" else "explicit" if requested else "query",
                 "path": None, "implementation": None, "reason": None}
    if not requested:
        selection["reason"] = "disabled_by_user"
        return selection
    if requested != "auto":
        selection.update(path=str(Path(requested).resolve()), implementation=probe_artifact_kind(requested))
        return selection
    try:
        properties = adb._run(["shell", "getprop ro.build.version.sdk; getprop ro.product.cpu.abilist; getprop ro.product.cpu.abi"], timeout=3).splitlines()
        if not properties or int(properties[0].strip()) < 26:
            selection["reason"] = "native_probe_requires_api26"
            return selection
        abis = ",".join(properties[1:]).replace(" ", "").split(",")
        abi = next((name for name in abis if name in ("arm64-v8a", "armeabi-v7a")), None)
        if abi is None:
            selection["reason"] = "native_probe_unsupported_abi"
            return selection
        folder = Path(artifact_dir) if artifact_dir is not None else Path(__file__).parent / "probes" / "bin"
        path = folder / ("perfcollect_cpu_" + abi + ".elf")
        if probe_artifact_kind(path) != "native_c":
            raise ValueError("bundled native probe is not an ARM ELF")
        header = path.read_bytes()[:64]
        if header[4] != (2 if abi == "arm64-v8a" else 1):
            raise ValueError("bundled probe ABI mismatch")
        selection.update(path=str(path.resolve()), implementation="native_c", abi=abi)
    except Exception as error:
        selection["reason"] = "native_probe_preflight_failed: " + str(error)[:300]
    return selection


def _number(record, key):
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ProbeError("invalid " + key)
    text = str(value)
    if not text.isascii() or not text.isdigit() or len(text) > 19:
        raise ProbeError("invalid " + key)
    return int(text)


class CpuCounterDecoder:
    """Device-clock deltas, with PID/starttime and sequence discontinuity guards."""
    def __init__(self, token, pid, name, clk_tck):
        self.token, self.pid, self.name_hex = token, pid, name.encode("utf-8").hex()
        if not isinstance(clk_tck, (int, float)) or not 50 <= clk_tck <= 1000:
            raise ValueError("invalid CLK_TCK")
        self.clk_tck = clk_tck
        self.previous = None
        self.seq = 0

    def decode(self, row):
        if type(row.get("v")) is not int or row.get("v") != 1 or row.get("token") != self.token:
            raise ProbeError("protocol identity mismatch")
        if row.get("kind") != "sample":
            raise ProbeError(row.get("reason", "unexpected protocol message"))
        if _number(row, "seq") != self.seq + 1:
            raise ProbeError("probe sequence gap")
        if _number(row, "pid") != self.pid or row.get("name_hex") != self.name_hex:
            raise ProbeError("target identity mismatch")
        values = {key: _number(row, key) for key in
                  ("start_ticks", "proc_ticks", "total", "busy", "t0_ns", "t1_ns")}
        if values["t1_ns"] < values["t0_ns"] or values["busy"] > values["total"]:
            raise ProbeError("invalid counters or time window")
        result = {"pid": self.pid, "cpu_total_pct": None, "cpu_proc_pct": None,
                  "source": "device_cpu_probe"}
        old = self.previous
        if old is not None:
            dt = values["t0_ns"] - old["t0_ns"]
            if dt <= 0:
                raise ProbeError("device clock did not advance")
            if values["total"] < old["total"] or values["busy"] < old["busy"]:
                raise ProbeError("system counters regressed")
            if values["start_ticks"] == old["start_ticks"]:
                proc_delta = values["proc_ticks"] - old["proc_ticks"]
                if proc_delta < 0:
                    raise ProbeError("process counter regressed")
                result["cpu_proc_pct"] = round(proc_delta / self.clk_tck / (dt / 1e9) * 100, 2)
            else:
                result["baseline_reset"] = "process_start_changed"
            total_delta = values["total"] - old["total"]
            busy_delta = values["busy"] - old["busy"]
            if busy_delta > total_delta:
                raise ProbeError("inconsistent system counter deltas")
            if total_delta:
                result["cpu_total_pct"] = round(busy_delta / total_delta * 100, 2)
        self.previous = values
        self.seq += 1
        return result


class ProbeTransport:
    """A dedicated pipe reader; never installed as a blocking metric.sample()."""
    def __init__(self, adb, dex_path, pid, name, interval):
        self.adb, self.artifact_path = adb, str(Path(dex_path).resolve())
        self.implementation = None
        self.remote_filename = "probe.dex"
        self.pid, self.name, self.interval = pid, name, interval
        self.token = uuid.uuid4().hex
        self.directory = "/data/local/tmp/perfcollect_cpu_" + self.token
        self.process = None
        self.reader = None
        self.messages = queue.Queue(maxsize=64)
        self.error = None
        self.eof = threading.Event()
        self.last_ping = 0
        self.launch_ns = None
        self.anchor = None
        self._owned = False

    def _shell(self, args):
        quoted = ["'" + str(a).replace("'", "'\\''") + "'" for a in args]
        return self.adb._run(["shell", *quoted], timeout=3)

    def start(self):
        try:
            self.implementation = probe_artifact_kind(self.artifact_path)
            self.remote_filename = "probe.elf" if self.implementation == "native_c" else "probe.dex"
            remote = self.directory + "/" + self.remote_filename
            self._shell(["mkdir", "-m", "700", self.directory])
            self._owned = True
            self.adb._run(["push", self.artifact_path, remote], timeout=15)
            # Native code needs execute permission; Android 14 DEX must be read-only.
            self._shell(["chmod", "500" if self.implementation == "native_c" else "400", remote])
            prefix = ([remote] if self.implementation == "native_c" else
                      ["env", "CLASSPATH=" + remote, "app_process", "/", "PerfCollectCpuProbe"])
            args = prefix + [self.token, str(self.pid), self.name.encode("utf-8").hex(),
                             str(round(self.interval * 1000)), self.directory]
            command = ("exec " if self.implementation == "native_c" else "") + " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)
            self.launch_ns = time.monotonic_ns()
            self.process = subprocess.Popen(self.adb._base + ["shell", "-T", command],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="strict", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.reader = threading.Thread(target=self._read, name="cpu-probe-reader", daemon=True)
            self.reader.start()
            row, received, mono = self.receive(timeout=3)
            if (row.get("kind") != "ready" or row.get("token") != self.token
                    or type(row.get("v")) is not int or row.get("v") != 1
                    or _number(row, "seq") != 0 or _number(row, "pid") != self.pid
                    or row.get("clock") != "device_monotonic"):
                raise ProbeError("probe ready handshake failed")
            device = _number(row, "device_ns")
            self.anchor = {"device_ns": str(device), "host_monotonic_lower_ns": str(self.launch_ns),
                           "host_monotonic_upper_ns": str(mono),
                           "offset_estimate_ns": str((self.launch_ns + mono) // 2 - device),
                           "uncertainty_ns": str((mono - self.launch_ns + 1) // 2),
                           "clock_domain": "device_monotonic", "method": "ready_causal_bounds"}
            return self
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(16385)
                if not line:
                    return
                if len(line) > 16384 or not line.endswith("\n"):
                    raise ProbeError("oversize or incomplete probe line")
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ProbeError("invalid probe record")
                self.messages.put_nowait((row, time.time(), time.monotonic_ns()))
        except queue.Full:
            self.error = ProbeError(f"probe queue overflow: pending={self.messages.qsize()}, rejected=1")
        except Exception as error:
            self.error = ProbeError(f"{type(error).__name__}: {error}; pending={self.messages.qsize()}")
        finally:
            self.eof.set()

    def receive(self, timeout=1):
        if self.error:
            raise ProbeError(str(self.error))
        now = time.monotonic()
        if now - self.last_ping >= 1:
            self.process.stdin.write("PING " + self.token + "\n")
            self.process.stdin.flush()
            self.last_ping = now
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            if self.error or self.eof.is_set():
                raise ProbeError(str(self.error or "probe stream ended"))
            raise TimeoutError("probe stream timeout")

    def close(self):
        confirmed_exit = self.process is None
        if self.process is not None:
            try:
                self.process.stdin.write("STOP " + self.token + "\n")
                self.process.stdin.flush()
                self.process.wait(timeout=1)
                confirmed_exit = True
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
            for pipe in (self.process.stdin, self.process.stdout):
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass
            if self.reader:
                self.reader.join(timeout=1)
            self.process = None
        if self._owned and confirmed_exit:
            try:
                self._shell(["rm", "-f", self.directory + "/" + self.remote_filename])
                self._shell(["rmdir", self.directory])
            except Exception:
                pass  # The probe shutdown hook also removes only its own files.
            self._owned = False


class CpuProbeWorker:
    """One CPU producer, stream or baseline query; never both at once."""
    def __init__(self, session, target_context, mailbox, adb, path, interval,
                 fallback_interval, writer, transport_factory=ProbeTransport):
        self.session, self.context, self.mailbox = session, target_context, mailbox
        self.adb, self.path, self.interval = adb, path, interval
        self.fallback_interval, self.writer = fallback_interval, writer
        self.transport_factory = transport_factory
        self._lock = threading.Lock()
        self._status = {"requested": "device_cpu_probe", "active": "starting", "probe_samples": 0, "query_samples": 0,
                        "probe_interval_s": interval, "query_interval_s": fallback_interval}
        self._fatal = None

    def snapshot(self):
        with self._lock:
            return dict(self._status)

    def _status_update(self, **values):
        with self._lock:
            self._status.update(values)

    def _event(self, state, active, reason=None, **extra):
        if active != "device_cpu_probe":
            extra.setdefault("clock_anchor", None)
        self._status_update(active=active, reason=reason, generation=state.generation, **extra)
        self.context.record_event_if_current(self.writer, {"event": "cpu_backend", "ts": time.time(), "target": state.package,
                           "target_generation": state.generation, "active": active,
                           "reason": reason, **extra}, state.generation)

    def start(self):
        try:
            return self.session.start_worker(self.run, name="cpu-backend")
        except RuntimeError:
            if not self.session.is_stopping:
                raise
            return None

    def raise_if_failed(self):
        if self._fatal:
            raise RuntimeError("CPU backend failed: " + str(self._fatal)) from self._fatal

    def run(self):
        transport = None
        generation = None
        identity = None
        fallback = False
        last_record = time.monotonic()
        try:
            while not self.session.is_stopping:
                sampler, current_generation = self.context.capture_sampler("cpu")
                state = self.context.state()
                if state.generation != current_generation:
                    continue
                current_identity = (current_generation, state.pid, state.proc_name)
                if identity != current_identity:
                    if transport:
                        transport.close()
                        transport = None
                    generation, fallback = current_generation, False
                    identity = current_identity
                    try:
                        pid = state.resolver.current_pid()
                        name = state.resolver.proc_name
                        if not pid or not name:
                            raise ProbeError("target identity unavailable")
                        # Resolution may have refreshed the PID since the snapshot.
                        identity = (generation, pid, name)
                        transport = self.transport_factory(self.adb, self.path, pid, name, self.interval).start()
                        decoder = CpuCounterDecoder(transport.token, pid, name, sampler.clk_tck)
                        last_record = time.monotonic()
                        self._event(state, "device_cpu_probe", clock_anchor=transport.anchor,
                                    implementation=getattr(transport, "implementation", "java_dex"))
                    except Exception as error:
                        if transport:
                            transport.close()
                            transport = None
                        fallback = True
                        sampler.reset_baseline()
                        self._event(state, "adb_query", str(error))
                if self.session.is_stopping:
                    break
                if self.context.state().generation != generation:
                    continue
                if fallback:
                    before = time.monotonic()
                    sample_metric_once(self.context, self.mailbox, "cpu")
                    with self._lock:
                        self._status["query_samples"] += 1
                    self.session.wait(max(.05, self.fallback_interval - (time.monotonic() - before)))
                    continue
                try:
                    row, received, mono = transport.receive(timeout=.2)
                    result = decoder.decode(row)
                    last_record = time.monotonic()
                    published = self.context.publish_if_current(self.mailbox, "cpu", result, received, generation,
                        expected_identity=(pid, name), recording={"backend": "device_cpu_probe", "probe": row,
                                   "clock_anchor": transport.anchor,
                                   "timing": {"host_received_at": received,
                                              "host_received_monotonic_ns": str(mono),
                                              "device_started_ns": row["t0_ns"],
                                              "device_completed_ns": row["t1_ns"]}})
                    if published:
                        with self._lock:
                            self._status["probe_samples"] += 1
                except Exception as error:
                    if isinstance(error, TimeoutError) and time.monotonic() - last_record <= max(3, self.interval * 3):
                        continue
                    transport.close()
                    transport = None
                    fallback = True
                    sampler.reset_baseline()
                    self._event(state, "adb_query", str(error))
        except Exception as error:
            self._fatal = error
            self._status_update(active="failed", reason=str(error))
            self.session.request_stop()
        finally:
            if transport:
                transport.close()
