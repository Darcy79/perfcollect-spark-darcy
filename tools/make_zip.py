# -*- coding: utf-8 -*-
"""打包「分发 zip」——本项目已取消 exe / 安装包分发（v72 起统一走 zip）。

用法（在项目根目录执行）：
    python tools/make_zip.py                 # 版本号自动从 CHANGELOG.md 读取
    python tools/make_zip.py --version v72   # 手动指定版本号
    python tools/make_zip.py --out D:\\tmp    # 指定输出目录（默认 share/）
输出：<out>/perfdog-cn-<版本>-<日期>.zip

为什么改成 zip 分发（2026-09-17 决策）：
    源码本身就能直接跑（uv / python），zip 解压即用、内容可审计、不会被杀软误报、
    没有安装残留，也免去 PyInstaller / Inno Setup 的构建、签名、路径与卸载那一堆问题。
    对方只需要 uv（或 Python）。

包内不含：采集数据（collector/output/）、本机缓存、内部文档（AGENTS/CHANGELOG/
评估报告/需求梳理等）、打包脚本自身。
"""

import argparse
import datetime
import os
import re
import sys
import zipfile

# 输出编码自保（2026-09-17）：GitHub Actions 的 Windows runner 上 stdout 是 cp1252，
# 直接 print 中文会抛 UnicodeEncodeError 让 CI 失败（本机 Python 默认 UTF-8 模式，看不出来）。
# 这里显式把 stdout/stderr 重配为 UTF-8；环境不支持时忽略，不影响打包功能。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 打进分发包的顶层目录 / 文件（相对项目根）
INCLUDE_DIRS = ["collector", "web", "sdk", "tests"]
INCLUDE_FILES = [
    "start_perfdog.bat", "start_dashboard.bat",
    "README.md", "使用教程-保姆级.md", "指标说明.md", "devices.md",
]
# 进包时改名（源文件 → 包内路径）
RENAME = {"分享说明.md": "先读我-使用说明.md"}

# 排除规则
EXCLUDE_DIRS = {
    "__pycache__", "output", ".git", ".github", "share", "build", "dist",
    "packaging", "staging", "installer_output", ".venv", "venv", ".idea",
}
EXCLUDE_FILES = {
    ".app_labels.json", "AGENTS.md", "CHANGELOG.md", "架构设计.md",
    "UI优化建议.md", "代码评估与优化项目.md", "打包后操作流程与改动需求.md",
    "验证统计卡片.html",
}
EXCLUDE_PATTERNS = [
    re.compile(r"^项目评估与优化待办-.*\.md$"),
    re.compile(r"^评估报告-.*\.md$"),
    re.compile(r".*\.pyc$"),
    re.compile(r".*\.pyo$"),
    re.compile(r".*\.log$"),
]


def _excluded(name, is_dir):
    if is_dir:
        return name in EXCLUDE_DIRS
    if name in EXCLUDE_FILES:
        return True
    return any(p.match(name) for p in EXCLUDE_PATTERNS)


def detect_version():
    """从 CHANGELOG.md 的状态头读当前版本（形如「## 当前状态（2026-09-17，v72）」）。"""
    try:
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            txt = f.read()
        m = re.search(r"当前状态（[^，]*，\s*(v[0-9.]+)\s*）", txt)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "dev"


def collect():
    """返回 [(源绝对路径, 包内相对路径)]，已按排除规则过滤。"""
    items = []
    for d in INCLUDE_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            print("  [warn] 缺少目录，跳过:", d)
            continue
        for cur, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if not _excluded(x, True)]
            for fn in files:
                if _excluded(fn, False):
                    continue
                full = os.path.join(cur, fn)
                items.append((full, os.path.relpath(full, ROOT).replace("\\", "/")))
    for f in INCLUDE_FILES:
        full = os.path.join(ROOT, f)
        if os.path.isfile(full):
            items.append((full, f))
        else:
            print("  [warn] 缺少文件，跳过:", f)
    for src, dst in RENAME.items():
        full = os.path.join(ROOT, src)
        if os.path.isfile(full):
            items.append((full, dst))
        else:
            print("  [warn] 缺少文件，跳过:", src)
    return items


def version_txt(version, n_files):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        "PerfDog-CN 分发包\n"
        "=================\n"
        "版本：%s\n"
        "打包时间：%s\n"
        "文件数：%d\n"
        "\n"
        "怎么跑起来（3 步）\n"
        "------------------\n"
        "1) 解压到任意路径（建议英文路径，如 D:\\perfdog）\n"
        "2) 双击 start_perfdog.bat   —— 采集 + 实时看板（需要 uv 或 Python）\n"
        "   只想看历史报告：双击 start_dashboard.bat（不需要连手机）\n"
        "3) 浏览器会自动打开 http://localhost:8080\n"
        "\n"
        "环境要求\n"
        "--------\n"
        "- uv（推荐，会自动准备 Python）：pip install uv   或 https://docs.astral.sh/uv/\n"
        "  没有 uv 也可直接用系统 Python：python collector/main.py --web\n"
        "- 仅「导出 XLSX」需要 openpyxl（启动脚本已带 --with openpyxl 自动获取）\n"
        "- 采集需要手机开启 USB 调试，并建议把 adb 放到 C:\\platform-tools\\\n"
        "\n"
        "数据位置\n"
        "--------\n"
        "collector\\output\\<时间戳>\\  —— jsonl 原始数据 + 自包含 HTML 报告\n"
        "全部本地保存，不上传云端。\n"
        "\n"
        "详细说明见包内：先读我-使用说明.md / 使用教程-保姆级.md / 指标说明.md\n"
        % (version, now, n_files)
    )


def main():
    ap = argparse.ArgumentParser(description="打包 PerfDog-CN 分发 zip")
    ap.add_argument("--version", default=None, help="版本号（默认从 CHANGELOG 自动读取）")
    ap.add_argument("--out", default=None, help="输出目录（默认 项目根/share）")
    args = ap.parse_args()

    version = args.version or detect_version()
    out_dir = args.out or os.path.join(ROOT, "share")
    date = datetime.datetime.now().strftime("%Y%m%d")
    top = "perfdog-cn-%s" % version
    out_path = os.path.join(out_dir, "%s-%s.zip" % (top, date))

    items = collect()
    os.makedirs(out_dir, exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)

    vt = version_txt(version, len(items))
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for full, rel in items:
            z.write(full, "%s/%s" % (top, rel))
        z.writestr("%s/版本.txt" % top, vt.encode("utf-8"))

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print("已生成分发包：")
    print("  %s" % out_path)
    print("  版本 %s · %d 个文件 · %.2f MB" % (version, len(items) + 1, size_mb))
    print()
    print("包内顶层结构：")
    seen = {}
    for _, rel in items:
        top_name = rel.split("/")[0]
        seen[top_name] = seen.get(top_name, 0) + 1
    for k in sorted(seen):
        print("  %-28s %d 个文件" % (k, seen[k]))
    print("  %-28s %d" % ("版本.txt", 1))
    print()
    print("确认不含采集数据：", "OK（无 collector/output/）"
          if not any(r.startswith("collector/output/") for _, r in items)
          else "!! 异常：包含 output/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
