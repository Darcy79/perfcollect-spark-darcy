# -*- coding: utf-8 -*-
"""校验项目状态文档、Python 测试数与前端资源版本是否一致。

用法（项目根目录）：
    python tools/check_consistency.py

本脚本只读，不执行测试；Python 数量通过 unittest discovery 统计。
"""

import os
import re
import sys
import unittest


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(relative_path):
    with open(os.path.join(ROOT, relative_path), encoding="utf-8") as stream:
        return stream.read()


def _match(text, pattern, label, errors):
    found = re.search(pattern, text)
    if found:
        return found.groups()
    errors.append(f"{label}：未找到预期格式")
    return None


def _expect_equal(label, values, errors):
    present = {key: value for key, value in values.items() if value is not None}
    if len(set(present.values())) > 1:
        details = "，".join(f"{key}={value}" for key, value in present.items())
        errors.append(f"{label}不一致：{details}")


def count_python_tests():
    suite = unittest.defaultTestLoader.discover(
        os.path.join(ROOT, "tests"), pattern="test_*.py")
    return suite.countTestCases()


def check():
    errors = []
    changelog = _read("CHANGELOG.md")
    agents = _read("AGENTS.md")
    architecture = _read("架构设计.md")
    memory = _read("开发交接记忆-20260917.md")
    assessment = _read("项目评估与优化待办-20260917.md")

    changelog_version = _match(
        changelog, r"## 当前状态（[^，]+，(v[0-9.]+)）",
        "CHANGELOG 当前版本", errors)
    agents_version = _match(
        agents, r"## 2\. 当前状态（截至 [^，]+，(v[0-9.]+)）",
        "AGENTS 当前版本", errors)
    architecture_versions = _match(
        architecture, r"对齐工作区 (v[0-9.]+) / 前端 (v[0-9.]+)",
        "架构设计版本", errors)
    memory_versions = _match(
        memory, r"工作区 (v[0-9.]+)，前端资源 (v[0-9.]+)",
        "交接记忆版本", errors)

    project_versions = {
        "CHANGELOG": changelog_version[0] if changelog_version else None,
        "AGENTS": agents_version[0] if agents_version else None,
        "架构设计": architecture_versions[0] if architecture_versions else None,
        "交接记忆": memory_versions[0] if memory_versions else None,
    }
    _expect_equal("项目版本", project_versions, errors)

    changelog_counts = _match(
        changelog, r"\| 测试 \| Python \*\*(\d+)\*\* 条 \+ JS \*\*(\d+)\*\*",
        "CHANGELOG 测试数", errors)
    agents_counts = _match(
        agents, r"\| 测试 \| Python \*\*(\d+)\*\* 条.*JS \*\*(\d+)\*\*",
        "AGENTS 测试数", errors)
    architecture_counts = _match(
        architecture, r"\*\*(\d+) 条 Python \+ (\d+) 条 JS 断言全绿\*\*",
        "架构设计测试数", errors)
    memory_counts = _match(
        memory, r"回归基线：Python (\d+) 项、JS (\d+) 项",
        "交接记忆测试数", errors)
    assessment_counts = _match(
        assessment, r"本轮修改后：Python (\d+) 项、JS (\d+) 项",
        "优化待办测试数", errors)

    python_counts = {"实际 discovery": str(count_python_tests())}
    js_counts = {}
    for label, counts in (
        ("CHANGELOG", changelog_counts), ("AGENTS", agents_counts),
        ("架构设计", architecture_counts), ("交接记忆", memory_counts),
        ("优化待办", assessment_counts),
    ):
        if counts:
            python_counts[label] = counts[0]
            js_counts[label] = counts[1]
    _expect_equal("Python 测试数", python_counts, errors)
    _expect_equal("JS 断言数", js_counts, errors)

    changelog_frontend = _match(
        changelog, r"\| 前端资源版本 \| \*\*(v\d+)\*\*",
        "CHANGELOG 前端版本", errors)
    frontend_versions = {
        "CHANGELOG": changelog_frontend[0] if changelog_frontend else None,
        "架构设计": architecture_versions[1] if architecture_versions else None,
        "交接记忆": memory_versions[1] if memory_versions else None,
    }
    expected_frontend = next(
        (value for value in frontend_versions.values() if value is not None), None)
    for page in ("web/index.html", "web/report.html"):
        refs = re.findall(r"\?v=(\d+)", _read(page))
        frontend_versions[page] = "v" + refs[0] if refs else None
        if len(refs) != 3:
            errors.append(f"{page}：应有 3 个资源版本引用，实际 {len(refs)} 个")
        if len(set(refs)) > 1:
            errors.append(f"{page}：资源版本引用不一致 {refs}")
        if expected_frontend and refs and "v" + refs[0] != expected_frontend:
            errors.append(
                f"{page}：资源版本 v{refs[0]} 与文档 {expected_frontend} 不一致")
    _expect_equal("前端资源版本", frontend_versions, errors)

    return errors, {
        "project": next((v for v in project_versions.values() if v), "unknown"),
        "frontend": expected_frontend or "unknown",
        "python": python_counts["实际 discovery"],
        "js": next(iter(js_counts.values()), "unknown"),
    }


def main():
    errors, values = check()
    if errors:
        print("[x] 项目一致性检查失败：")
        for error in errors:
            print("    -", error)
        return 1
    print(
        "[+] 项目一致性检查通过："
        f"项目 {values['project']} · 前端 {values['frontend']} · "
        f"Python {values['python']} · JS {values['js']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
