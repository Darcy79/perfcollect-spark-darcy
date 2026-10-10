"""Device protocol smoke for a TEST-ROOT build; synthetic counters, owned files only."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cpu_probe import CpuCounterDecoder


def smoke(adb, serial, artifact):
    token = uuid.uuid4().hex
    directory = "/data/local/tmp/perfcollect_cpu_" + token
    base = [adb, "-s", serial]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    def shell(args):
        quoted = " ".join("'" + str(a).replace("'", "'\\''") + "'" for a in args)
        return subprocess.run(base + ["shell", quoted], capture_output=True, text=True,
                              timeout=10, check=True, creationflags=flags)
    def push(path, remote):
        subprocess.run(base + ["push", str(path), remote], capture_output=True,
                       check=True, timeout=15, creationflags=flags)
    processes = []
    passed = []
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        fields = ["0"] * 20
        fields[0], fields[11], fields[12], fields[19] = "S", "20", "10", "123"
        stat = "42 (name with ) spaces) " + " ".join(fields) + "\n"
        (folder / "pid-stat").write_bytes(stat.encode("ascii"))
        (folder / "cmdline").write_bytes(b"game\x00arg\x00")
        (folder / "cpu-stat").write_bytes(b"cpu  100 0 20 1000 50 5 6 0 0 0\ncpu0 1 2 3 4\n")
        shell(["mkdir", "-m", "700", directory])
        try:
            shell(["mkdir", directory + "/proc", directory + "/proc/42"])
            push(artifact, directory + "/probe.elf")
            shell(["chmod", "500", directory + "/probe.elf"])
            push(folder / "pid-stat", directory + "/proc/42/stat")
            push(folder / "cmdline", directory + "/proc/42/cmdline")
            push(folder / "cpu-stat", directory + "/proc/stat")
            def launch():
                args = ["env", "PERFCOLLECT_PROC_ROOT=" + directory + "/proc", directory + "/probe.elf",
                        token, "42", "67616d65", "250", directory]
                command = " ".join("'" + a + "'" for a in args)
                process = subprocess.Popen(base + ["shell", "-T", command], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                    bufsize=1, creationflags=flags)
                processes.append(process)
                ready = json.loads(process.stdout.readline())
                assert ready["kind"] == "ready" and ready["implementation"] == "native_c"
                return process
            def finish(process, command=None):
                if command is not None:
                    process.stdin.write(command); process.stdin.flush()
                process.stdin.close()
                process.wait(timeout=3)
                assert process.returncode == 0
            p = launch()
            decoder = CpuCounterDecoder(token, 42, "game", 100)
            for _ in range(3):
                r = json.loads(p.stdout.readline())
                if r.get("kind") == "error":
                    p.wait(timeout=3)
                    raise RuntimeError(p.stderr.read())
                decoder.decode(r)
                assert r["start_ticks"] == "123" and r["proc_ticks"] == "30"
                assert r["total"] == "1181" and r["busy"] == "131"
                assert int(r["t1_ns"]) >= int(r["t0_ns"])
            p.stdin.write("PING " + token[:10]); p.stdin.flush(); time.sleep(.1)
            p.stdin.write(token[10:] + "\n"); p.stdin.flush()
            assert json.loads(p.stdout.readline())["kind"] == "sample"
            finish(p, "STOP " + token + "\n")
            passed.append("parser/identity/counters/device-clock/fragmented-PING/STOP")
            p = launch(); finish(p); passed.append("EOF self-exit")
            p = launch(); finish(p, "PING " + "0" * 32 + "\n"); passed.append("wrong-token self-exit")
            p = launch(); started = time.monotonic()
            drain = threading.Thread(target=lambda: list(p.stdout), daemon=True)
            drain.start(); p.wait(timeout=7); drain.join(timeout=1)
            assert 4 <= time.monotonic() - started < 7 and p.returncode == 0
            passed.append("5-second lease self-exit")
            p = launch(); json.loads(p.stdout.readline())
            (folder / "cmdline").write_bytes(b"wrong\x00")
            push(folder / "cmdline", directory + "/proc/42/cmdline")
            messages = [json.loads(line) for line in p.stdout]
            p.wait(timeout=3)
            assert any(r["kind"] == "error" and r["reason"] == "read_or_identity_failed" for r in messages)
            passed.append("identity change emits error and exits")
            print(json.dumps({"passed": passed, "owned_directory": directory}, indent=2))
        finally:
            for p in processes:
                if p.poll() is None:
                    try: finish(p)
                    except Exception: p.terminate(); p.wait(timeout=3)
                for pipe in (p.stdin, p.stdout, p.stderr):
                    if pipe and not pipe.closed: pipe.close()
            for name in ("proc/42/stat", "proc/42/cmdline", "proc/stat", "probe.elf"):
                shell(["rm", "-f", directory + "/" + name])
            for name in ("/proc/42", "/proc", ""):
                shell(["rmdir", directory + name])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--artifact", required=True)
    args = parser.parse_args()
    smoke(args.adb, args.serial, args.artifact)
