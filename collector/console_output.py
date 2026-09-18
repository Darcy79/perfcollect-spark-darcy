# -*- coding: utf-8 -*-
"""采集主循环的纯文本输出格式化。"""

from datetime import datetime


FPS_ERROR_LABELS = {
    "no_layer": "无渲染层(游戏请在微信前台)",
    "probe_fail": "渲染层读取失败(链路抖动)",
    "layer_read_fail": "渲染层失效,重匹配中",
}


def format_sample_status(row, captured_at=None):
    """把单个采样行格式化为控制台状态行，不修改输入。"""
    captured_at = captured_at or datetime.now()
    fps = row.get("fps") or {}
    cpu = row.get("cpu") or {}
    mem = row.get("mem") or {}
    net = row.get("net") or {}
    thermal = row.get("therm") or {}

    fps_text = FPS_ERROR_LABELS.get(fps.get("error"), "-")
    if fps.get("error") not in FPS_ERROR_LABELS and fps.get("fps") is not None:
        fps_text = fps["fps"]
    mem_text = "(节流)" if mem.get("throttled") else mem.get("pss_kb", "-")
    temperature = thermal.get("temp_c")
    network = f"↓{net.get('rx_kbps', '-')}/↑{net.get('tx_kbps', '-')}KB/s"
    return (
        f"[{captured_at.strftime('%H:%M:%S')}] "
        f"FPS={fps_text} Jank%={fps.get('jank_rate', '-')} "
        f"CPU总%={cpu.get('cpu_total_pct', '-')} "
        f"CPU进程%={cpu.get('cpu_proc_pct', '-')} "
        f"PSS={mem_text}kB {network} "
        f"温度={temperature if temperature is not None else '-'}°C"
    )
