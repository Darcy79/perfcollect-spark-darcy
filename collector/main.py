# -*- coding: utf-8 -*-
"""自研 PerfCollect 采集器 — 第一阶段骨架（FPS/CPU/内存）。

用法:
    python main.py                          # 默认 config.json，1s 间隔，Ctrl+C 停止
    python main.py --duration 120           # 采集 120 秒
    python main.py --interval 0.5           # 0.5s 间隔
    python main.py --output ../test1        # 指定输出目录

输出: <output>/perfcollect_<YYYYmmdd_HHMMSS>.jsonl（每行一个采样点）
"""

import argparse
import json
import os
import queue
import signal
import sys
import threading
import time
import webbrowser
from datetime import datetime

from adb import Adb, AdbError
from pidresolver import PidResolver
from metrics.fps import FpsCollector
from metrics.cpu import CpuCollector
from metrics.mem import MemCollector
from metrics.network import NetworkCollector
from metrics.thermal import ThermalCollector
from logcat import LogcatMonitor
from device_info import probe_device_info
from sampling import MetricMailbox, SampleAggregator, SamplerScheduler
from target_context import TargetContext
from target_monitor import TargetMismatchTracker
from target_switcher import TargetSwitcher
from jsonl_writer import JsonlWriter
from capture_session import CaptureSession
from console_output import format_sample_status
from event_sink import JsonlEventSink, stop_and_drain_event_capture
from runtime_health import (BACKOFF_MAX_S, FAIL_ALERT_STREAK,
                            ChannelAlertTracker, RuntimeHealthTracker,
                            backoff_sleep, row_has_any_value)

# 各指标独立采样间隔（秒）。FPS 高频（0.5s）让 Jank 及时出现；
# 内存/温度低频（2s）避免 dumpsys 拖慢整体。并行后互不阻塞。
SAMPLER_INTERVALS = {
    "fps": 0.5,
    "cpu": 1.0,
    "mem": 2.0,
    "net": 1.0,
    "therm": 2.0,
}


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_capture_timing(cli_interval, cli_duration, config):
    """解析采集节奏：命令行 > config.json > 内置默认值。

    config 使用对用户更直观的 interval_ms / duration_s；运行时统一换算为秒。
    返回 (interval_s, duration_s)，非法值抛出可直接展示给用户的 ValueError。
    """
    config = config or {}
    raw_interval = cli_interval if cli_interval is not None else config.get("interval_ms", 1000)
    raw_duration = cli_duration if cli_duration is not None else config.get("duration_s", 0)
    try:
        interval = float(raw_interval) if cli_interval is not None else float(raw_interval) / 1000.0
    except (TypeError, ValueError):
        raise ValueError("采样间隔必须是数字（--interval 秒，或 config.interval_ms 毫秒）")
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        raise ValueError("采集时长必须是数字（--duration 或 config.duration_s，单位秒）")
    if interval <= 0:
        raise ValueError("采样间隔必须大于 0")
    if duration < 0:
        raise ValueError("采集时长不能小于 0（0 表示手动停止）")
    return interval, duration


# 目标一致性自检间隔（秒，独立线程跑；2026-09-11）——不能放主采样循环：
# 设备半死时 dumpsys 会阻塞到 adb 超时（20s），把采样点间隔拉到 21s。
MISMATCH_CHECK_INTERVAL = 10.0


def main():
    ap = argparse.ArgumentParser(description="自研 PerfCollect 采集器（第一阶段：FPS/CPU/内存）")
    ap.add_argument("--config", default="config.json", help="配置文件路径")
    ap.add_argument("--package", default=None, help="覆盖目标包名（测其他 App，如 --package com.example.game）")
    ap.add_argument("--process-pattern", default=None,
                    help="覆盖进程匹配模式；测原生 App 时传空字符串 --process-pattern \"\"")
    ap.add_argument("--show-foreground", action="store_true",
                    help="仅打印当前前台应用的包名/窗口，然后退出（用于找要测的 App）")
    ap.add_argument("--serial", default="", help="ADB 设备序列号，默认自动选第一台")
    ap.add_argument("--duration", type=float, default=None,
                    help="采集时长(秒)，覆盖 config.duration_s；0=手动停止")
    ap.add_argument("--interval", type=float, default=None,
                    help="采样间隔(秒)，覆盖 config.interval_ms")
    ap.add_argument("--output", default="output", help="输出目录")
    ap.add_argument("--web", action="store_true", help="启动实时 Web 看板")
    ap.add_argument("--port", type=int, default=8080, help="Web 看板端口（默认 8080）")
    ap.add_argument("--no-browser", action="store_true",
                    help="启动看板后不自动打开浏览器（无头/CI 场景用）")
    ap.add_argument("--auto", action="store_true",
                    help="跳过启动向导：自动解析目标进程并立即开始采集（旧行为/脚本用）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    try:
        args.interval, args.duration = resolve_capture_timing(args.interval, args.duration, cfg)
    except ValueError as e:
        ap.error(str(e))
    package = args.package if args.package is not None else cfg.get("package", "com.tencent.mm")
    process_pattern = args.process_pattern if args.process_pattern is not None \
        else cfg.get("process_pattern", "appbrand")
    serial = args.serial or cfg.get("serial", "")

    outdir = args.output          # 输出根目录（output/）
    os.makedirs(outdir, exist_ok=True)

    try:
        adb = Adb(serial)
    except AdbError as e:
        print(f"[-] {e}")
        sys.exit(1)
    print(f"[+] 已连接设备: {adb.serial}")

    # 仅打印当前前台应用（方便确定要测哪个 App），然后退出
    if args.show_foreground:
        try:
            out = adb.shell(["dumpsys", "window"])
            for line in out.splitlines():
                if "mCurrentFocus" in line:
                    print(f"[+] 当前前台窗口: {line.strip()}")
                    break
            else:
                print("[-] 未取到前台窗口")
        except Exception as e:
            print(f"[-] 获取前台窗口失败: {e}")
        sys.exit(0)

    # Ctrl+C、Web 停止/退出与后台采集线程共用同一会话生命周期。
    session = CaptureSession()
    web = None

    def _handler(sig, frame):
        if session.is_stopping:
            # 第二次 Ctrl+C：彻底退出（含 Web 服务）
            if web:
                web.stop()
            sys.exit(0)
        session.request_stop()

    # 向导开始前注册：等待选择进程时按 Ctrl+C 也走标准会话收口路径。
    signal.signal(signal.SIGINT, _handler)

    def _cancel_before_capture(message="[=] 已取消，未创建任何采集数据。"):
        """初始化阶段的统一取消卡口，避免收到 SIGINT 后继续创建输出。"""
        if not session.is_stopping:
            return False
        print(message)
        if web:
            web.stop()
        return True

    # ---------------- 启动向导（2026-09-11）：先探测、用户确认后才开始记录 ----------------
    # 背景：微信可同时存在 appbrand0/1/2 三个实例，自动选进程曾两次采到闲置实例
    # （2026-09-11 实测：采到 appbrand0 PSS 231MB / CPU 增量近 0，而游戏实际在
    # appbrand1：PSS 1035MB；FPS 层名 AppBrandUI1 与进程 appbrand0 不匹配）。
    # 新流程：探测（只读，**不创建任何采集输出**）→ 网页列出候选与推荐 → 用户选定
    # → 才创建 jsonl 并开始采样。命令行 --auto（或不带 --web）保持旧的自动解析行为。
    wizard = bool(args.web and not args.auto)

    if args.web:
        from web import WebServer
        web = WebServer(port=args.port, output_dir=outdir, adb=adb,
                        process_pattern=process_pattern)
        port = web.start()

        def _stop_capture():
            """看板 POST /api/stop：请求停止，并中止后台应用名解析。"""
            session.request_stop()
            web.abort_label_resolve()
            print("[>] 已从看板收到停止采集请求", flush=True)

        def _shutdown_all():
            """看板 POST /api/shutdown：停止采集后自然退出程序。"""
            session.request_shutdown()
            web.abort_label_resolve()
            print("[>] 已从看板收到退出程序请求", flush=True)

        # 向导阶段即可停止/退出，不再等采集器初始化完成后才注册。
        web.set_stop_callback(_stop_capture)
        web.set_shutdown_callback(_shutdown_all)
        # 启动指引（exe 版没有 bat 的说明文字，关键信息必须在这里讲清楚）：
        # 看板地址 / 历史报告地址 / 数据目录绝对路径 / 如何开始与停止
        print("")
        print("=" * 60)
        print("  PerfCollect-CN 看板已启动")
        open_hint = "（即将自动打开浏览器）" if not args.no_browser else ""
        print(f"  实时看板  : http://localhost:{port}  {open_hint}".rstrip())
        print(f"  历史报告  : http://localhost:{port}/report.html")
        print(f"  数据目录  : {os.path.abspath(outdir)}")
        if wizard:
            print("  采集流程  : ① 网页上选择目标进程 ② 点「开始采集」→ 才开始记录数据")
        else:
            print("  停止方式  : 本窗口按 Ctrl+C 一次停采集，再按一次退出")
        print("=" * 60)
        print("")
        if not args.no_browser:
            # 延迟打开：等服务线程就绪（start() 已绑定端口，稍等更稳妥）
            def _open_browser():
                time.sleep(1.5)
                try:
                    webbrowser.open(f"http://localhost:{port}")
                except Exception:
                    pass
            threading.Thread(target=_open_browser, daemon=True,
                             name="open-browser").start()

    chosen_pid, chosen_name = None, None
    if wizard:
        from probe import probe_once
        print("[*] 正在探测微信候选进程（只读，不记录任何采集数据）…", flush=True)
        probe_info = probe_once(adb, process_pattern)
        if probe_info.get("ok"):
            layer = probe_info.get("layer") or {}
            print("[+] 候选进程（网页上可选择，★为推荐）：")
            for c in probe_info["candidates"]:
                mark = " ★推荐" if c.get("recommended") else ""
                print(f"    pid={c['pid']:<7} {c['name']:<30} 内存 {c['rss_mb']}MB  "
                      f"CPU增量 {c['cpu_delta_pct']}%{mark}")
            if layer.get("name"):
                print(f"[+] 游戏渲染层: {layer['name']}"
                      f"（AppBrandUI{layer.get('appbrand_index')}）")
            print(f"[+] 推荐: pid={probe_info.get('recommended_pid')}"
                  f"（{probe_info.get('recommend_detail')}）")
        else:
            print(f"[!] 探测未成功: {probe_info.get('error')}")
        if web:
            web.set_status(phase="waiting", candidates=probe_info,
                           device=adb.serial, target=package,
                           process_pattern=process_pattern)
        print("[*] 网页上选择目标进程 → 点「开始采集」；也可直接在本窗口按回车（用推荐项）"
              "或输入 pid 后回车；命令行 --auto 可跳过向导")
        # 终端兜底输入（网页界面做好之前的可用路径）：后台线程读一行，主循环轮询
        _in_q = queue.Queue()

        def _stdin_reader():
            try:
                _in_q.put(input())
            except Exception:
                pass

        threading.Thread(target=_stdin_reader, daemon=True, name="stdin-reader").start()
        _last_wait_log = 0.0
        while not session.is_stopping:
            req = web.take_start_request() if web else None
            if req:
                chosen_pid = req.get("pid")
                chosen_name = req.get("name")
                print(f"[>] 收到开始指令（网页）: pid={chosen_pid} {chosen_name}", flush=True)
                break
            try:
                line = (_in_q.get_nowait() or "").strip()
            except queue.Empty:
                line = None
            if line is not None:
                if line == "":
                    rec = (probe_info or {}).get("recommended_pid")
                    if rec:
                        chosen_pid = rec
                        for c in (probe_info or {}).get("candidates", []):
                            if c["pid"] == rec:
                                chosen_name = c["name"]
                                break
                        print(f"[>] 回车确认 → 使用推荐 pid={chosen_pid} {chosen_name}", flush=True)
                        break
                    print("[!] 无推荐项可用，请在网页上选择（或输入 pid）", flush=True)
                elif line.isdigit():
                    chosen_pid = int(line)
                    for c in (probe_info or {}).get("candidates", []):
                        if c["pid"] == chosen_pid:
                            chosen_name = c["name"]
                            break
                    print(f"[>] 已选定 pid={chosen_pid} {chosen_name}", flush=True)
                    break
                elif line:
                    print(f"[!] 无法识别输入 {line!r}：回车用推荐项，或输入候选 pid", flush=True)
                threading.Thread(target=_stdin_reader, daemon=True,
                                 name="stdin-reader").start()
            if time.time() - _last_wait_log > 30:
                _last_wait_log = time.time()
                print("[*] 仍在等待「开始采集」…（网页点按钮 / 本窗口回车 / Ctrl+C 退出）",
                      flush=True)
            time.sleep(0.3)
        if session.is_stopping:
            _cancel_before_capture()
            return
        if not chosen_pid:
            # 理论上 /api/start 会带 pid；这里兜底用推荐项，避免"开始了却没有目标"
            rec = (probe_info or {}).get("recommended_pid")
            if rec:
                chosen_pid = rec
                for c in (probe_info or {}).get("candidates", []):
                    if c["pid"] == rec:
                        chosen_name = c["name"]
                        break
                print(f"[!] 未指定进程，回退到推荐 pid={chosen_pid}")

    if _cancel_before_capture():
        return

    # 目标进程：向导模式用用户选定的 pid（固定，不再自动改选）；否则旧行为自动解析
    if chosen_pid:
        resolver = PidResolver(adb, package, process_pattern,
                               fixed_pid=chosen_pid, fixed_name=chosen_name)
        pid = resolver.resolve()
        print(f"[+] 目标进程（用户指定）: {chosen_name or package} pid={pid}")
    else:
        resolver = PidResolver(adb, package, process_pattern)
        pid = resolver.resolve()
        if pid:
            print(f"[+] 目标进程: {package}（匹配 {process_pattern or '主进程'}） pid={pid}")
        else:
            print(f"[!] 未找到 {package} 的进程，请确认小游戏已打开且在前台。")

    collectors = {
        "fps": FpsCollector(adb, package, process_pattern),
        "cpu": CpuCollector(adb, resolver),
        # 由线程间隔(2s)控制，不再内部节流
        "mem": MemCollector(adb, resolver, package, min_interval=0),
        "net": NetworkCollector(adb, resolver),
        "therm": ThermalCollector(adb),
    }
    # v75：FPS 0.5s、主循环通常 1s；只存 latest 会覆盖前一窗口。
    # 保留最多 256 个待落盘窗口（约 128s），溢出会显式记录 dropped，不静默丢失。
    metric_mailbox = MetricMailbox({"fps": 256})
    target_context = TargetContext(
        package, process_pattern, resolver, pid, collectors)

    # 探测核数（2026-08-26）：供前端 CPU 图"进程占整机%"派生曲线（cpu_proc_pct ÷ 核数）。
    # 写入 status（实时看板）+ jsonl meta 行（历史报告），让历史报告不依赖当前是否连设备。
    cores = CpuCollector.probe_cores(adb)
    print(f"[+] CPU 核数: {cores}")

    # 探测设备信息（2026-08-27）：型号/市场名/平台/CPU/分辨率，一次 shell 往返。
    # 失败项静默 None，不影响采集；写入 status + meta 行供报告展示。
    device_info = probe_device_info(adb)
    if device_info.get("model"):
        print(f"[+] 设备: {device_info.get('market_name') or device_info.get('model')}"
              f"（{device_info.get('model')}）")

    if _cancel_before_capture():
        return

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 每次采集新建一个按时间命名的文件夹，内含 jsonl 与 html 报告，避免历史数据混淆
    run_dir = os.path.join(outdir, run_id)
    os.makedirs(run_dir, exist_ok=True)
    out_file = os.path.join(run_dir, f"perfcollect_{run_id}.jsonl")
    jsonl_writer = JsonlWriter(out_file).start()
    # meta 必须先于 Web 回调和采样线程注册，确保任何并发事件都排在首行之后。
    jsonl_writer.write({
        "ts": round(time.time(), 3),
        "event": "meta",
        "schema_version": 3,
        "fps_window_mode": "preserved",
        "metric_freshness_mode": "sampled_at",
        "cores": cores,
        "proc_name": target_context.state().proc_name or package,
        "device": device_info,
    }, flush=True)
    print(f"[*] 开始采集（间隔 {args.interval}s，{'Ctrl+C 停止' if not args.duration else f'{args.duration}s'}）")
    print(f"[*] 数据目录: {run_dir}")

    # 模式1：logcat 事件监听（零侵入捞小游戏 console.log，叠加看板标注层）
    # 目标为微信小游戏时启用；原生 App 采集无需日志标注
    monitor = None
    events_file = None
    events_sink = None
    if package.lower() == "com.tencent.mm":
        try:
            monitor = LogcatMonitor(adb, serial)
            monitor.start()
            events_file = os.path.join(run_dir, f"perfcollect_{run_id}.events.jsonl")
            events_sink = JsonlEventSink(events_file)
            print(f"[+] logcat 事件监听已启动（模式1：捞 console.log，tag/关键词过滤，限流 {monitor._min_gap}s）")
        except Exception as e:
            monitor = None
            print(f"[!] logcat 监听启动失败（不影响性能采集）: {e}")

    def _stop_event_capture():
        """先停 logcat 生产者，再落盘尾部事件并关闭 sink。"""
        try:
            stop_and_drain_event_capture(monitor, events_sink)
        except Exception as e:
            print(f"[!] logcat 停止/尾部事件落盘失败: {e}")

    # 实时 Web 看板：服务启动与"打开浏览器"已在启动向导阶段提前完成，
    # 这里只在**用户确认、采集真正开始后**更新状态——向导模式下此前不产生任何输出文件。
    if web:
        web.set_status(running=True, device=adb.serial, pid=pid, run_id=run_id,
                       report_name=(run_id + "/perfcollect_" + run_id + ".jsonl"),
                       target=package, process_pattern=process_pattern,
                       cores=cores, device_info=device_info,
                       started_at=datetime.now().strftime("%H:%M:%S"),
                       phase="running",
                       target_source=("user" if chosen_pid else "auto"))

        # 热切换编排已从 main.py 收口到独立组件；目标相关采集器重建，
        # therm 保留，切换事件仍在新目标可见前进入唯一 JsonlWriter。
        target_switcher = TargetSwitcher(
            adb, cfg, args.config, target_context, metric_mailbox,
            jsonl_writer, web)
        web.set_switch_callback(target_switcher.apply)

    if session.is_stopping:
        _stop_event_capture()
        jsonl_writer.close()
        if web:
            web.set_status(running=False)
            web.stop()
        print("[=] 已在采样线程启动前停止，仅含 meta 的采集文件已安全关闭。")
        return

    # ---------------- 并行采集（性能优化 2026-08-12） ----------------
    # 每个指标独立线程按各自间隔采样；mailbox 同时保存 latest，并为 FPS 保留
    # 主循环两次落盘之间的完整 0.5s 窗口。慢指标（mem/therm）仍只保留最新值。

    sampler_scheduler = SamplerScheduler(
        session, target_context, metric_mailbox, SAMPLER_INTERVALS)
    sampler_scheduler.start()

    # 目标一致性自检（2026-09-11）：层里的 AppBrandUI(n) 应与正在采集的 appbrand(n)
    # 一致；不一致说明微信把渲染切到了别的实例（cpu/mem/net 采错进程）。
    # ⚠️ 必须独立线程：设备半死时 dumpsys 会阻塞到 adb 超时（20s），若放在主采样
    # 循环内会把采样点间隔拉长到 ~21s（2026-09-11 真机实测到该现象）。
    # **不自动切换**——切换前必然先采一段错数据；只写事件行 + 页面告警。
    def _mismatch_watch():
        mismatch_tracker = TargetMismatchTracker()
        while not session.is_stopping:
            if session.wait(MISMATCH_CHECK_INTERVAL):
                break
            if not chosen_pid:
                continue
            try:
                target_state = target_context.state()
                lname, lidx, pidx = None, None, None
                if target_state.package.lower() == TargetMismatchTracker.WECHAT_PACKAGE:
                    from probe import pick_game_layer, appbrand_index
                    lname, lidx = pick_game_layer(
                        adb.shell(["dumpsys", "SurfaceFlinger", "--list"]))
                    pidx = appbrand_index(target_state.proc_name or "")
                action = mismatch_tracker.update(
                    target_state.package, target_state.pid, lname, lidx, pidx)
                if not action:
                    continue
                if action["kind"] == "mismatch":
                    print(f"[!] 目标错配: {action['message']}", flush=True)
                    try:
                        jsonl_writer.write({
                            "ts": round(time.time(), 3), **action["event"],
                        }, flush=True)
                    except Exception:
                        pass
                    if web:
                        web.set_status(mismatch=action["status"])
                elif web:
                    web.set_status(mismatch=None)
            except Exception:
                pass

    if chosen_pid and not session.is_stopping:
        session.start_worker(_mismatch_watch, name="mismatch-watch")

    start = time.time()
    sample_aggregator = SampleAggregator(start)
    runtime_health = RuntimeHealthTracker(SAMPLER_INTERVALS)
    n = 0
    wrote_any = False   # 首点门槛：写出第一行有效数据前跳过全空行（任务⑤）
    try:
        while not session.is_stopping:
            ts = time.time()
            if args.duration and (ts - start) >= args.duration:
                break
            # 指标窗口与目标标签来自同一原子快照，避免热切换时跨代错标。
            latest, pending, dropped, target_state = target_context.snapshot_mailbox(
                metric_mailbox, SAMPLER_INTERVALS, drain_keys=("fps",))
            row = sample_aggregator.build(
                ts, target_state.package, latest, pending, dropped)

            # 运行健康状态只计算状态沿；ADB/Web/落盘副作用仍由主流程执行。
            health = runtime_health.update(row)
            # 缺数/断连事件落盘（2026-09-11 事故复盘）：状态沿触发，去重不刷屏。
            # 事件行带 event 字段，前端 prepareRows / 导出 data_rows /
            # data_health 均按该字段跳过，不参与采样点统计。
            for ev in health["channel_events"]:
                jsonl_writer.write({"ts": round(ts, 3), **ev})
            if health["probe_required"]:
                if adb.is_device_alive():
                    print("[!] 连续采样失败但设备在线：请确认目标应用在前台/渲染层存在", flush=True)
                    if web:
                        web.set_status(running=False)
                else:
                    print("[!] 检测到设备断连！请检查 USB 连接（采集线程持续重试，恢复后自动继续）", flush=True)
                    if web:
                        web.set_status(running=False, device="断连")
            elif health["recovered"] and web:
                web.set_status(running=True, device=adb.serial)

            for msg in health["health_alerts"]:
                print(f"[!] 数据健全性告警: {msg}", flush=True)

            # 首点门槛（2026-09-11）：采集启动后各指标线程尚未产出首份快照时，
            # 首行全空（实测首点 t≈0.4s 全指标 None）。跳过"任何指标都无值"的行
            # 直到写出第一行有效数据；之后即使某行暂时全空也照写（中断/恢复形态
            # 要留痕）。主循环节奏未变，不影响 t_ms 起点语义与 --duration 计时。
            if wrote_any or row_has_any_value(row):
                jsonl_writer.write(row, flush=True)
                n += 1
                wrote_any = True
                if web:
                    web.add_sample(row)

            # logcat 事件轮询落盘（与采样点同目录，供看板叠加标注层）
            if monitor and events_sink:
                try:
                    events_sink.write_many(monitor.get_events())
                except Exception as e:
                    print(f"[!] 事件落盘失败: {e}")

            print(format_sample_status(row))
            # v61：连续失败退避——设备半死（每次 adb shell 阻塞至 timeout）时
            # 降低主循环空转频率，避免把断连告警拖到最坏 3 分钟；恢复即回正常节奏。
            session.wait(backoff_sleep(args.interval, health["fail_streak"]))
    finally:
        # duration 到期时也统一停止生产者；阻塞中的 adb 调用只有限等待，不强杀线程。
        session.request_stop()
        session.join_workers(timeout=1.0)
        _stop_event_capture()
        jsonl_writer.close()

    print(f"[=] 采集结束，共 {n} 个采样点。已保存: {out_file}")

    if monitor:
        if events_sink and events_sink.count:
            print(f"[+] logcat 事件已保存: {events_file}（{events_sink.count} 条）")
        else:
            print(f"[!] 本次未捕获到 logcat 事件（游戏内无 console.log 输出，或 tag 未命中过滤规则）")

    # 自动生成 HTML 报告（自包含，双击即看），与 jsonl 同目录
    try:
        from export_report import load_rows, export_html
        rows = load_rows(out_file)
        if rows:
            html_path = os.path.join(run_dir, f"perfcollect_{run_id}.html")
            export_html(rows, html_path)
            print(f"[+] 已生成 HTML 报告: {html_path}（双击打开即可查看）")
    except Exception as e:
        print(f"[!] HTML 报告生成失败（不影响数据）: {e}")

    if web:
        web.set_status(running=False)
        if session.shutdown_requested:
            # 看板"退出程序"：停止流程已走完（含 HTML 报告生成），直接结束进程
            print("[*] 已收到看板退出请求，程序退出。")
            web.stop()
            return
        print(f"[*] Web 看板仍在运行（可查看刚采集的数据与历史报告）:")
        print(f"[*]   实时看板/历史: http://localhost:{web.port}")
        print(f"[*]   历史报告页: http://localhost:{web.port}/report.html")
        print(f"[*] 再次按 Ctrl+C 退出")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
