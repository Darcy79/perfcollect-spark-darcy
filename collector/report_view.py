# -*- coding: utf-8 -*-
"""为看板打开的历史自包含 HTML 补界面，不改写磁盘上的报告。"""

import json
import os
import re
from datetime import datetime
from html import escape


_MOTION_STYLE = '<link rel="stylesheet" href="/assets/report_page_motion.css?v=89">\n'
_MOTION_SCRIPT = '<script src="/assets/report_page_motion.js?v=89"></script>\n'

_ADDON_STYLE = """
<style>
.report-live-addon { width:100%; min-width:0; color:#78909c; font-size:11px; }
.report-live-context, .report-live-actions {
  display:flex; flex-wrap:wrap; align-items:center; gap:4px 12px; min-width:0;
}
.report-live-context span { overflow-wrap:anywhere; }
.report-live-context em { color:#607d8b; font-style:normal; }
.report-live-actions { margin-top:5px; }
.report-live-actions button {
  border:1px solid #365066; border-radius:4px; background:#192b39;
  color:#90cbe7; padding:3px 7px; cursor:pointer; font:inherit;
}
.report-live-actions button:hover { border-color:#4fc3f7; color:#e1f5fe; }
#report-action-message { color:#ffab40; }
</style>
"""

_ADDON_SCRIPT = """
<script src="/assets/report_actions.js?v=89"></script>
<script>
(function () {
  var name = window.PerfReportActions.reportNameFromLocation(window.location);
  var message = document.getElementById('report-action-message');
  function notify(text) { message.textContent = text; }
  document.getElementById('open-report-folder').addEventListener('click', function () {
    window.PerfReportActions.run('folder', name, notify);
  });
  document.getElementById('download-report-zip').addEventListener('click', function () {
    window.PerfReportActions.run('zip', name, notify);
  });
})();
</script>
"""


def report_display_name(html_path, jsonl_path=None):
    """目录备注优先，兼容旧备注旁车；未改名时使用采集时间戳。"""
    filename = os.path.basename(html_path)
    match = re.search(r"(\d{8}_\d{6})\.html$", filename, flags=re.I)
    stamp = match.group(1) if match else None
    directory = os.path.basename(os.path.dirname(os.path.abspath(html_path)))
    if stamp and directory.endswith("_" + stamp):
        renamed = directory[:-(len(stamp) + 1)].strip()
        if renamed:
            return renamed
    if jsonl_path:
        try:
            with open(jsonl_path + ".remark.txt", encoding="utf-8") as stream:
                remark = stream.read().strip()
            if remark:
                return remark
        except OSError:
            pass
    return stamp or os.path.splitext(filename)[0]


def _set_report_title(html_text, html_path, jsonl_path):
    """只改 head 中的 title，不扫描内联图表脚本。"""
    head = re.search(r"<head\b[^>]*>.*?</head\s*>", html_text, flags=re.I | re.S)
    if not head:
        return html_text
    title = "<title>perfcollect·" + escape(
        report_display_name(html_path, jsonl_path)) + "</title>"
    updated, count = re.subn(r"<title\b[^>]*>.*?</title\s*>",
                             lambda _: title, head.group(0), count=1,
                             flags=re.I | re.S)
    if not count:
        updated = re.sub(r"<head\b[^>]*>", lambda match: match.group(0) + title,
                         updated, count=1, flags=re.I)
    return html_text[:head.start()] + updated + html_text[head.end():]


def read_report_facts(jsonl_path, html_path):
    """逐行读元信息与时间边界，长测不再复制一份完整 rows 到内存。"""
    meta = {}
    count = 0
    first_time = last_time = None
    target = None
    try:
        with open(jsonl_path, encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("event") == "meta":
                    if not meta:
                        meta = row
                    continue
                if row.get("event"):
                    continue
                count += 1
                if target is None:
                    target = row.get("target")
                t_ms = row.get("t_ms")
                if isinstance(t_ms, (int, float)) and not isinstance(t_ms, bool):
                    if first_time is None:
                        first_time = t_ms
                    last_time = t_ms
    except OSError:
        pass
    device = meta.get("device") if isinstance(meta.get("device"), dict) else {}
    captured = "—"
    try:
        captured = datetime.fromtimestamp(float(meta["ts"])).strftime("%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        match = re.search(r"(\d{8}_\d{6})\.html$", os.path.basename(html_path))
        if match:
            try:
                captured = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S").strftime(
                    "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
    duration = (f"{(last_time - first_time) / 1000:.1f} 秒"
                if first_time is not None and last_time is not None else "—")
    return (
        ("当前目录", os.path.basename(os.path.dirname(html_path)) or "—"),
        ("采集时间", captured),
        ("机型", device.get("market_name") or device.get("model") or "—"),
        ("系统", device.get("system_version") or "—"),
        ("被测", meta.get("proc_name") or target or "—"),
        ("采样点", str(count)),
        ("时长", duration),
        ("CPU", str(meta.get("cores")) if meta.get("cores") else "—"),
    )


def enhance_served_report(html_text, jsonl_path, html_path):
    """只增强服务响应；已有按钮的报告仅补动效，避免重复组件。"""
    # 图表库脚本中可能含有字符串 '</body>'。只有文档终止标签可作为注入点；
    # 若旧报告结构不完整，宁可原样返回，也不能破坏原有图表脚本。
    if not re.search(r"</body\s*>\s*</html\s*>\s*\Z", html_text, flags=re.I):
        return html_text
    html_text = _set_report_title(html_text, html_path, jsonl_path)
    if 'id="open-report-folder"' in html_text:
        if "report-page-reveal" in html_text or "report_page_motion.js" in html_text:
            return html_text
        if not re.search(r"</head>", html_text, flags=re.I):
            return html_text
        html_text = re.sub(r"</head>", lambda _: _MOTION_STYLE + "</head>",
                           html_text, count=1, flags=re.I)
        closing = re.search(r"</body\s*>\s*</html\s*>\s*\Z", html_text, flags=re.I)
        return html_text[:closing.start()] + _MOTION_SCRIPT + html_text[closing.start():]
    facts = read_report_facts(jsonl_path, html_path)
    context = "".join(
        "<span><em>" + escape(label) + "：</em>" + escape(str(value)) + "</span>"
        for label, value in facts)
    addon = (
        '<div class="report-live-addon"><div class="report-live-context">' + context +
        '</div><div class="report-live-actions">'
        '<button type="button" id="open-report-folder" title="打开报告所在文件夹">'
        '📂 打开所在文件夹</button>'
        '<button type="button" id="download-report-zip" title="下载该报告全部产物">'
        '⬇ 下载 ZIP</button><span id="report-action-message" aria-live="polite"></span>'
        '</div></div>'
    )
    html_text = re.sub(r"</head>", lambda _: _MOTION_STYLE + _ADDON_STYLE + "</head>", html_text,
                       count=1, flags=re.I)
    if re.search(r"</header>", html_text, flags=re.I):
        html_text = re.sub(r"</header>", lambda _: addon + "</header>", html_text,
                           count=1, flags=re.I)
    elif re.search(r"<body[^>]*>", html_text, flags=re.I):
        html_text = re.sub(r"(<body[^>]*>)", lambda match: match.group(1) + addon,
                           html_text,
                           count=1, flags=re.I)
    else:
        return html_text
    closing = re.search(r"</body\s*>\s*</html\s*>\s*\Z", html_text, flags=re.I)
    return html_text[:closing.start()] + _ADDON_SCRIPT + _MOTION_SCRIPT + html_text[closing.start():]
