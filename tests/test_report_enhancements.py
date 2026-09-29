# -*- coding: utf-8 -*-
"""自包含报告元信息：新旧 JSONL 均不依赖看板服务。"""

import os
import sys
import tempfile
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from export_report import export_html  # noqa: E402
from report_view import enhance_served_report, report_display_name  # noqa: E402


class TestReportEnhancements(unittest.TestCase):
    def test_new_report_embeds_device_target_and_actions(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "20260929_100010")
            os.mkdir(folder)
            html_path = os.path.join(folder, "perfcollect_20260929_100010.html")
            rows = [
                {"event": "meta", "device": {"model": "ADT-AN00",
                 "market_name": "Magic3 Pro"}, "proc_name": "com.example.game",
                 "cores": 8},
                {"t_ms": 0}, {"t_ms": 2500},
            ]
            export_html(rows, html_path)
            with open(html_path, encoding="utf-8") as stream:
                page = stream.read()
            for value in ("生成时目录", "20260929_100010", "Magic3 Pro",
                          "com.example.game", "2.5 秒", "8", "打开所在文件夹",
                          "下载 ZIP", "reportNameFromLocation"):
                self.assertIn(value, page)
            self.assertEqual(page.count("var ROWS = "), 1)
            self.assertIn("<title>perfcollect·20260929_100010</title>", page)
            self.assertIn("body.report-page-reveal main .chart-card", page)
            self.assertIn("body.report-page-reveal #report-label-timeline .label-track", page)
            self.assertIn("document.body.classList.add('report-page-reveal')", page)
            self.assertNotIn('src="/assets/report_page_motion.js', page)

    def test_legacy_report_shows_missing_fields_without_error(self):
        with tempfile.TemporaryDirectory() as root:
            html_path = os.path.join(root, "legacy.html")
            export_html([{"t_ms": 0}], html_path)
            with open(html_path, encoding="utf-8") as stream:
                page = stream.read()
            for field in ("系统", "机型", "被测", "CPU"):
                self.assertIn(field + "：</em>—", page)

    def test_new_html_is_not_double_enhanced(self):
        page = ('<html><body><button id="open-report-folder">📂</button>'
                '<script>document.body.classList.add("report-page-reveal")</script>'
                '</body></html>')
        self.assertEqual(enhance_served_report(page, "missing.jsonl", "report.html"), page)

    def test_existing_report_with_actions_gets_motion_only(self):
        page = ('<html><head></head><body><header>报告</header>'
                '<button id="open-report-folder">📂</button>'
                '<script>window.chartReady = true;</script></body></html>')
        enhanced = enhance_served_report(page, "missing.jsonl", "report.html")
        self.assertIn('report_page_motion.css?v=89', enhanced)
        self.assertGreater(enhanced.index('report_page_motion.js'),
                           enhanced.index('window.chartReady'))
        self.assertEqual(enhanced.count('id="open-report-folder"'), 1)
        self.assertNotIn('report_actions.js', enhanced)

    def test_existing_report_title_tracks_renamed_directory_without_rewriting(self):
        page = ('<html><head><title>旧标题</title></head><body>'
                '<button id="open-report-folder">📂</button>'
                '<script>document.body.classList.add("report-page-reveal")</script>'
                '</body></html>')
        html_path = os.path.join("登录页_20260929_100010",
                                 "perfcollect_20260929_100010.html")
        enhanced = enhance_served_report(page, html_path[:-5] + ".jsonl", html_path)
        self.assertIn("<title>perfcollect·登录页</title>", enhanced)
        self.assertEqual(enhanced.count('id="open-report-folder"'), 1)
        self.assertNotIn("report_page_motion.js", enhanced)

    def test_legacy_chart_script_with_body_string_stays_intact(self):
        chart_script = (
            '<script>var preview = "<body><img src=\"x\"></body>";'
            'window.chartReady = true;</script>'
        )
        page = ('<html><head><title>旧报告</title></head><body>'
                '<header><h1>旧报告</h1></header><main>图表</main>' +
                chart_script + '</body></html>')
        enhanced = enhance_served_report(page, "missing.jsonl", "report.html")
        self.assertIn(chart_script, enhanced)
        self.assertEqual(enhanced.count('id="open-report-folder"'), 1)
        self.assertGreater(enhanced.index('report_actions.js'), enhanced.index(chart_script))
        self.assertIn('report_page_motion.css?v=89', enhanced)
        self.assertGreater(enhanced.index('report_page_motion.js'), enhanced.index(chart_script))
        self.assertTrue(enhanced.rstrip().endswith("</body></html>"))

    def test_incomplete_legacy_html_is_returned_unchanged(self):
        page = '<html><body><script>var sample = "</body>";</script>'
        self.assertEqual(enhance_served_report(page, "missing.jsonl", "report.html"), page)

    def test_report_display_name_prefers_renamed_directory_then_legacy_remark(self):
        with tempfile.TemporaryDirectory() as root:
            stamp = "20260929_100010"
            filename = "perfcollect_" + stamp + ".html"
            plain = os.path.join(root, stamp, filename)
            renamed = os.path.join(root, "交易行_" + stamp, filename)
            self.assertEqual(report_display_name(plain), stamp)
            self.assertEqual(report_display_name(renamed), "交易行")
            os.mkdir(os.path.dirname(plain))
            jsonl_path = plain[:-5] + ".jsonl"
            with open(jsonl_path + ".remark.txt", "w", encoding="utf-8") as stream:
                stream.write("旧版备注")
            self.assertEqual(report_display_name(plain, jsonl_path), "旧版备注")

    def test_generated_report_escapes_renamed_title(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "战斗&大厅_20260929_100010")
            os.mkdir(folder)
            html_path = os.path.join(folder, "perfcollect_20260929_100010.html")
            export_html([{"t_ms": 0}], html_path)
            with open(html_path, encoding="utf-8") as stream:
                page = stream.read()
            self.assertIn("<title>perfcollect·战斗&amp;大厅</title>", page)
