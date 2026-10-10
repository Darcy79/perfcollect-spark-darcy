"""Optional C FPS stream; lossless raw-response deltas, existing metric algorithms."""
import json
from pathlib import Path
import queue
import re
import shlex
import subprocess
import threading
import time
import uuid

from adb_fps import FpsShell, FpsShellError
from cpu_probe import probe_artifact_kind


def select_fps_probe(adb, requested="auto"):
    """Unsupported devices/artifacts keep the established query backend."""
    try:
        properties = adb._run(["shell", "getprop ro.build.version.sdk; getprop ro.product.cpu.abilist; getprop ro.product.cpu.abi"], timeout=3).splitlines()
        if int(properties[0]) < 26:
            raise ValueError("FPS native probe requires API26")
        abi = next((x.strip() for x in ",".join(properties[1:]).split(",")
                    if x.strip() in ("arm64-v8a", "armeabi-v7a")), None)
        if abi is None:
            raise ValueError("FPS native probe unsupported ABI")
        path = (Path(__file__).parent / "probes/bin" / ("perfcollect_fps_" + abi + ".elf")
                if requested == "auto" else Path(requested))
        if probe_artifact_kind(path) != "native_c" or path.read_bytes()[4] != (2 if abi == "arm64-v8a" else 1):
            raise ValueError("FPS native probe ABI mismatch")
        return {"path": str(path.resolve()), "abi": abi, "implementation": "native_c", "reason": None}
    except Exception as error:
        return {"path": None, "reason": str(error)[:200]}


class FpsDeltaDecoder:
    """Reconstruct every response byte, including empty/sparse/reordered buffers."""
    MAX_RAW = 16384
    MAX_ROWS = 512

    def __init__(self, token):
        self.token, self.sequence, self.previous, self.last_t0 = token, 0, [], -1

    def decode(self, row):
        if (not isinstance(row, dict) or type(row.get("v")) is not int or row["v"] != 1
                or row.get("token") != self.token):
            raise FpsShellError("FPS probe identity mismatch")
        if row.get("kind") != "sample":
            raise FpsShellError("FPS probe: " + str(row.get("reason", "unexpected message")))
        if (type(row.get("seq")) is not int or row["seq"] != self.sequence + 1
                or type(row.get("base_seq")) is not int or row["base_seq"] != self.sequence):
            raise FpsShellError("FPS probe sequence/base gap")
        values = []
        for key in ("t0_ns", "t1_ns"):
            value = row.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,19}", value):
                raise FpsShellError("FPS probe invalid device time")
            values.append(int(value))
        if values[0] <= self.last_t0 or values[1] < values[0]:
            raise FpsShellError("FPS probe device clock regressed")
        parts = row.get("parts")
        if not isinstance(parts, list) or len(parts) > self.MAX_ROWS:
            raise FpsShellError("FPS probe response row limit")
        current, size = [], 0
        for part in parts:
            if type(part) is int and 0 <= part < len(self.previous):
                line = self.previous[part]
            elif isinstance(part, str) and len(part) <= self.MAX_RAW * 2 and re.fullmatch(r"(?:[0-9a-f]{2})+", part):
                line = bytes.fromhex(part)
            else:
                raise FpsShellError("FPS probe invalid response reference/hex")
            size += len(line)
            if size > self.MAX_RAW:
                raise FpsShellError("FPS probe response byte limit")
            current.append(line)
        # Atomic commit: bad deltas never change the reconstruction base.
        self.sequence, self.previous, self.last_t0 = row["seq"], current, values[0]
        return b"".join(current).decode("utf-8", "replace")


class FpsProbe(FpsShell):
    MAX_LINE = 65536

    def __init__(self, adb, path, layer, interval=.5):
        if not isinstance(layer, str) or not layer or len(layer.encode("utf-8")) > 2048 or any(c in layer for c in "\r\n\0"):
            raise FpsShellError("FPS probe invalid layer")
        if not .25 <= interval <= 60:
            raise FpsShellError("FPS probe invalid interval")
        self.adb, self.layer, self.interval = adb, layer, interval
        self.timeout, self.token = 3.0, uuid.uuid4().hex
        self.directory = "/data/local/tmp/perfcollect_fps_" + self.token
        self.lines, self.broken = queue.Queue(maxsize=16), threading.Event()
        self.process, self.reader, self._owned = None, None, False
        self.decoder, self.last_ping, self.recording = FpsDeltaDecoder(self.token), 0, None
        try:
            if probe_artifact_kind(path) != "native_c":
                raise FpsShellError("FPS probe requires own-source ARM ELF")
            self._shell(["mkdir", "-m", "700", self.directory])
            self._owned = True
            remote = self.directory + "/probe.elf"
            adb._run(["push", str(path), remote], timeout=15)
            self._shell(["chmod", "500", remote])
            command = "exec " + shlex.join([remote, self.token, layer.encode("utf-8").hex(),
                                             str(round(interval * 1000)), self.directory])
            started = time.monotonic_ns()
            self.process = subprocess.Popen(adb._base + ["shell", "-T", command],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.reader = threading.Thread(target=self._read, args=(self.process.stdout,), name="fps-probe-reader", daemon=True)
            self.reader.start()
            row = self._receive()
            finished = time.monotonic_ns()
            if (row.get("kind") != "ready" or type(row.get("v")) is not int or row["v"] != 1
                    or row.get("token") != self.token or type(row.get("seq")) is not int or row["seq"] != 0
                    or row.get("clock") != "device_monotonic"
                    or not isinstance(row.get("device_ns"), str) or not re.fullmatch(r"[0-9]{1,19}", row["device_ns"])):
                raise FpsShellError("FPS probe ready handshake failed")
            self.anchor = {"device_ns": row["device_ns"], "host_monotonic_lower_ns": str(started),
                           "host_monotonic_upper_ns": str(finished),
                           "uncertainty_ns": str((finished - started + 1) // 2),
                           "method": "ready_causal_bounds", "clock_domain": "device_monotonic"}
        except Exception:
            self.close()
            raise

    def _shell(self, args):
        return self.adb._run(["shell", shlex.join(args)], timeout=3)

    def _receive(self):
        if self.broken.is_set():
            raise FpsShellError("FPS probe stream ended or bounded queue overflowed")
        now = time.monotonic()
        if now - self.last_ping >= 1:
            self.process.stdin.write(("PING " + self.token + "\n").encode("ascii"))
            self.process.stdin.flush()
            self.last_ping = now
        try:
            wire = self._line(now + self.timeout)
            self.last_wire_bytes = len(wire)
            if not wire.endswith(b"\n"):
                raise FpsShellError("FPS probe incomplete record")
            row = json.loads(wire)
        except (ValueError, UnicodeError) as error:
            raise FpsShellError("FPS probe invalid JSON") from error
        if not isinstance(row, dict):
            raise FpsShellError("FPS probe invalid record")
        return row

    def query(self, args):
        if list(args) != ["dumpsys", "SurfaceFlinger", "--latency", self.layer]:
            raise FpsShellError("FPS probe layer mismatch")
        row = self._receive()
        result = self.decoder.decode(row)
        self.recording = {"probe": {"v": 1, "token": self.token, "seq": row["seq"],
                                     "base_seq": row["base_seq"], "layer": self.layer,
                                     "wire_bytes": self.last_wire_bytes,
                                     "response_bytes": len(result.encode("utf-8"))},
                          "device_started_ns": row["t0_ns"], "device_completed_ns": row["t1_ns"],
                          "clock_anchor": self.anchor}
        return result

    def close(self):
        process = self.process
        try:
            super().close()  # EOF, bounded wait, then owned host-client termination.
        finally:
            # Device self-removes on EOF/lease. Host cleanup only after confirmed device exit.
            if self._owned and (process is None or process.returncode == 0):
                try:
                    self._shell(["rm", "-f", self.directory + "/probe.elf"])
                    self._shell(["rmdir", self.directory])
                except Exception:
                    pass
                self._owned = False
