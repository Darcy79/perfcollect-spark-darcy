# -*- coding: utf-8 -*-
"""采集停止后的报告产物生成；不参与实时采样。"""

import os

from export_report import export_csv_atomic, export_html, load_rows


def finalize_capture_outputs(jsonl_path, generate_csv=True, emit=print):
    """生成 CSV 与 HTML；单个产物失败不影响另一产物和采集退出。"""
    stem = os.path.splitext(jsonl_path)[0]
    csv_path = stem + ".csv"
    html_path = stem + ".html"
    csv_failed = False
    try:
        rows = load_rows(jsonl_path)
    except Exception as exc:
        if generate_csv:
            emit(f"[!] CSV 未生成: 无法读取采集数据: {exc}")
        emit(f"[!] HTML 报告生成失败（不影响数据）: {exc}")
        return {"csv": None, "html": None, "csv_failed": generate_csv}

    csv_result = None
    if generate_csv:
        try:
            count = export_csv_atomic(rows, csv_path)
            emit(f"[+] 已生成 CSV: {csv_path}（{count} 个采样点）")
            csv_result = csv_path
        except Exception as exc:
            csv_failed = True
            emit(f"[!] CSV 未生成: {exc}")

    html_result = None
    if rows:
        try:
            export_html(rows, html_path, csv_failed=csv_failed)
            emit(f"[+] 已生成 HTML 报告: {html_path}（双击打开即可查看）")
            html_result = html_path
        except Exception as exc:
            emit(f"[!] HTML 报告生成失败（不影响数据）: {exc}")
    return {"csv": csv_result, "html": html_result, "csv_failed": csv_failed}
