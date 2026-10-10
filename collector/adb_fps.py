# -*- coding: utf-8 -*-
"""Bounded, request-driven SurfaceFlinger queries over one owned ADB shell."""
import queue
import shlex
import subprocess
import threading
import time
import uuid


class FpsShellError(RuntimeError):
    pass


class FpsShell:
    MAX_LINE = 16384
    MAX_RESPONSE = 131072

    def __init__(self, base, timeout=3.0):
        self.timeout = timeout
        self.token = "pcfps_" + uuid.uuid4().hex
        self.sequence = 0
        self.lines = queue.Queue(maxsize=256)
        self.broken = threading.Event()
        self.process = None
        self.reader = None
        self.lock = threading.Lock()
        # No device files/process injection. Idle lease and EOF release the shell.
        self.process = subprocess.Popen(
            list(base) + ["shell", "-T", 'while IFS= read -r -t 10 pc_fps_command; do eval "$pc_fps_command"; done'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.reader = threading.Thread(target=self._read, args=(self.process.stdout,), name="fps-shell-reader", daemon=True)
        self.reader.start()

    def _read(self, stream):
        try:
            while True:
                line = stream.readline(self.MAX_LINE + 1)
                if not line or len(line) > self.MAX_LINE:
                    self.broken.set()
                    return
                self.lines.put_nowait(line)
        except (OSError, ValueError, queue.Full):
            self.broken.set()

    def _line(self, deadline):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FpsShellError("FPS shell response timeout")
            try:
                return self.lines.get(timeout=min(remaining, .1))
            except queue.Empty:
                if self.broken.is_set():
                    raise FpsShellError("FPS shell disconnected or output limit exceeded")

    def query(self, args):
        # Only the read-only latency query uses this channel; other collectors retain their paths.
        if len(args) != 4 or list(args[:3]) != ["dumpsys", "SurfaceFlinger", "--latency"]:
            raise ValueError("FPS shell accepts only SurfaceFlinger latency queries")
        if any(c in args[-1] for c in "\n\r\0"):
            raise FpsShellError("FPS layer contains a control character")
        with self.lock:
            if self.process is None or self.broken.is_set():
                raise FpsShellError("FPS shell unavailable")
            self.sequence += 1
            marker = self.token + "_" + str(self.sequence)
            begin, end = marker + "_begin", marker + "_end:"
            # dumpsys has a bounded device-side lifetime even if the host disconnects.
            command = shlex.join(["dumpsys", "-t", "2", *args[1:]])
            # Markers are fixed ASCII without echo options/escapes. Android sh has
            # builtin echo; printf may be an external executable on the device.
            # Save dumpsys status first and keep the separator even without its LF.
            script = ("echo " + shlex.quote(begin) + "; " + command +
                      "; pc_fps_status=$?; echo; echo " + shlex.quote(end) +
                      '"$pc_fps_status"\n')
            try:
                self.process.stdin.write(script.encode("utf-8"))
                self.process.stdin.flush()
                deadline = time.monotonic() + self.timeout
                first = self._line(deadline).rstrip(b"\r\n")
                if first != begin.encode("ascii"):
                    raise FpsShellError("FPS shell response identity mismatch: " + repr(first[:160]))
                output, size = [], 0
                while True:
                    line = self._line(deadline)
                    if line.startswith(end.encode("ascii")):
                        status = line[len(end):].strip()
                        if status != b"0":
                            raise FpsShellError("FPS shell command failed: " + status.decode("ascii", "replace"))
                        return b"".join(output).decode("utf-8", "replace")
                    size += len(line)
                    if size > self.MAX_RESPONSE:
                        raise FpsShellError("FPS shell response size exceeded")
                    output.append(line)
            except (OSError, ValueError) as error:
                raise FpsShellError(str(error)) from error

    def close(self):
        process, self.process = self.process, None
        self.broken.set()
        if process is None:
            return
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=.5)
        if self.reader is not None:
            self.reader.join(timeout=.5)
        process.stdout.close()
