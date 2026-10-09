"""Run the exact Java probe on JVM with synthetic proc files; never uses ADB."""
import argparse
import json
from pathlib import Path
import queue
import subprocess
import tempfile
import threading


def smoke(jdk):
    source = Path(__file__).with_name("PerfCollectCpuProbe.java")
    token = "a" * 32
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        process_dir = root / "42"
        process_dir.mkdir()
        fields = ["0"] * 20
        fields[0], fields[11], fields[12], fields[19] = "R", "10", "5", "100"
        (process_dir / "stat").write_text("42 (game with spaces) " + " ".join(fields), encoding="utf-8")
        (process_dir / "cmdline").write_bytes(b"game\x00")
        (root / "stat").write_text("cpu 100 50 250 600 0 0 0 0\n", encoding="utf-8")
        subprocess.run([str(Path(jdk) / "bin/javac.exe"), "--release", "8", "-Xlint:-options",
                        "-encoding", "UTF-8", "-d", folder, str(source)], check=True)
        def start(name="game"):
            proc = subprocess.Popen([str(Path(jdk) / "bin/java.exe"),
                "-Dperfcollect.procRoot=" + folder, "-cp", folder, "PerfCollectCpuProbe", token,
                "42", name.encode().hex(), "250", "/data/local/tmp/perfcollect_cpu_" + token],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8")
            output = queue.Queue()
            def read():
                for line in proc.stdout:
                    output.put(json.loads(line))
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            return proc, output, reader
        def cleanup(proc, reader):
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)
            reader.join(timeout=2)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                pipe.close()
        proc, output, reader = start()
        try:
            assert output.get(timeout=3)["kind"] == "ready"
            for seq in range(1, 4):
                row = output.get(timeout=2)
                assert row["kind"] == "sample", row
                assert row["seq"] == seq and row["proc_ticks"] == "15", row
                assert int(row["t1_ns"]) >= int(row["t0_ns"])
            proc.stdin.write("STOP " + token + "\n")
            proc.stdin.flush()
            assert proc.wait(timeout=2) == 0
        finally:
            cleanup(proc, reader)
        proc, output, reader = start("wrong")
        try:
            assert output.get(timeout=3)["kind"] == "ready"
            assert output.get(timeout=2)["kind"] == "error"
            assert proc.wait(timeout=2) == 0
        finally:
            cleanup(proc, reader)
        proc, output, reader = start()
        try:
            assert output.get(timeout=3)["kind"] == "ready"
            # Keep stdin open, withhold PING: the device-side lease must expire.
            assert proc.wait(timeout=7) == 0
        finally:
            cleanup(proc, reader)
        proc, output, reader = start()
        try:
            assert output.get(timeout=3)["kind"] == "ready"
            proc.stdin.close()
            assert proc.wait(timeout=2) == 0
        finally:
            cleanup(proc, reader)
    print("JVM probe smoke passed: counters, sequence, identity, STOP, lease expiry, stdin EOF")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jdk", required=True)
    smoke(parser.parse_args().jdk)
