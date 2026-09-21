# -*- coding: utf-8 -*-
"""只启动 Web 看板（不采集），用于离线查看历史报告。"""

import argparse
import time

from web import WebServer, open_browser_when_ready


DEFAULT_PORT = 8080


class DashboardPortError(RuntimeError):
    """请求的端口无法绑定。"""

    def __init__(self, port, cause=None):
        super().__init__(f"端口 {port} 已被占用")
        self.port = port
        self.cause = cause


def _port_arg(value):
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("端口必须是整数") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口必须在 0~65535 之间")
    return port


def port_candidates(preferred, explicit):
    """显式端口只试一次；默认端口可一直向上寻找。"""
    if explicit or preferred == 0:
        return (preferred,)
    return range(preferred, 65536)


def start_dashboard_server(preferred, explicit, output_dir,
                           server_factory=WebServer):
    """按端口策略创建并实际绑定服务，返回 ``(server, actual_port)``。"""
    last_error = None
    for candidate in port_candidates(preferred, explicit):
        server = server_factory(port=candidate, output_dir=output_dir)
        try:
            return server, server.start()
        except OSError as exc:
            server.stop()
            last_error = exc
            if explicit:
                raise DashboardPortError(candidate, exc) from exc
    raise DashboardPortError(preferred, last_error)


def build_parser():
    ap = argparse.ArgumentParser(description="PerfCollect-CN 历史看板（无需手机）")
    # None 用来区分“未传 --port”与“显式 --port 8080”。
    ap.add_argument("--port", type=_port_arg, default=None,
                    help="历史看板端口；不传时优先 8080，占用则自动递增")
    ap.add_argument("--output", default="output")
    ap.add_argument("--no-browser", action="store_true",
                    help="服务就绪后不自动打开浏览器（无头/CI 场景用）")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    explicit = args.port is not None
    preferred = args.port if explicit else DEFAULT_PORT
    try:
        web, port = start_dashboard_server(
            preferred, explicit, args.output)
    except DashboardPortError as exc:
        if explicit:
            print(f"[ERROR] 端口 {exc.port} 已被占用：可能已有一个看板在运行。"
                  "请关闭它或改用其他 --port。", flush=True)
        else:
            print(f"[ERROR] 从端口 {preferred} 开始未找到可用端口。", flush=True)
        return 2

    if port != preferred:
        print(f"[!] 端口 {preferred} 已被占用，改用 {port}", flush=True)
    web.set_status(running=False, device="(离线)", pid=None)
    print(f"[+] 看板已启动: http://localhost:{port}")
    print(f"[+] 历史报告: http://localhost:{port}/report.html")
    print("[*] Ctrl+C 退出")
    if not args.no_browser:
        open_browser_when_ready(port, "/report.html")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        web.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
