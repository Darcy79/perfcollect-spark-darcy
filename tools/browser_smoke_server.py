# -*- coding: utf-8 -*-
"""为真实浏览器最小回归提供自包含的本机合成数据服务。"""

import argparse
import json
import os
import sys
import tempfile
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTOR = os.path.join(ROOT, "collector")
if COLLECTOR not in sys.path:
    sys.path.insert(0, COLLECTOR)

from web import WebServer  # noqa: E402


def _sample(index, offset=0):
    t_ms = index * 1000
    return {
        "ts": 1_800_000_000 + offset + index,
        "t_ms": t_ms,
        "target": "com.tencent.mm",
        "fps": {
            "fps": round(60 - (index % 4) * 2.5 - offset, 1),
            "jank_rate": round((index % 3) / 20, 3),
            "frame_p50_ms": 16.7,
            "frame_p95_ms": 20 + index,
            "frame_max_ms": 28 + index * 2,
        },
        "cpu": {"cpu_total_pct": 25 + index, "cpu_proc_pct": 7 + index / 2},
        "mem": {"pss_kb": 300_000 + index * 2_000, "vmrss_kb": 320_000 + index * 2_000},
        "net": {"rx_kbps": 10 + index * 2, "tx_kbps": 3 + index},
        "therm": {"temp_c": 35 + index / 5, "power_w": 2.5 + index / 20},
    }


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def prepare_output(output_dir):
    for name, offset in (("smoke-a", 0), ("smoke-b", 2)):
        run_dir = os.path.join(output_dir, name)
        os.makedirs(run_dir, exist_ok=True)
        report = os.path.join(run_dir, f"{name}.jsonl")
        rows = [{
            "event": "meta", "schema_version": 3, "cores": 8,
            "proc_name": "com.tencent.mm:appbrand0",
            "device": {"model": "Browser-Smoke", "resolution": "1080x2400"},
        }]
        rows.extend(_sample(index, offset) for index in range(12))
        _write_jsonl(report, rows)
        _write_jsonl(report.replace(".jsonl", ".events.jsonl"), [
            {"t_ms": 3000, "tag": "console", "level": "I", "text": f"{name} 进入场景"},
            {"t_ms": 8000, "tag": "console", "level": "I", "text": f"{name} 对局开始"},
        ])
    return [_sample(index) for index in range(12)]


def main():
    parser = argparse.ArgumentParser(description="PerfCollect 浏览器最小回归服务")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="perfcollect-browser-smoke-") as output_dir:
        latest = prepare_output(output_dir)
        server = WebServer(port=args.port, output_dir=output_dir)
        port = server.start()
        server.set_status(
            running=True, device="Browser-Smoke", pid=12345,
            run_id="smoke-live", target="com.tencent.mm",
            process_pattern="appbrand", cores=8, phase="running")
        for row in latest:
            server.add_sample(row)
        print(f"BROWSER_SMOKE_URL=http://127.0.0.1:{port}", flush=True)
        print(f"BROWSER_SMOKE_REPORT=http://127.0.0.1:{port}/report.html", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
