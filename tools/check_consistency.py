# -*- coding: utf-8 -*-
"""校验已入库的公开文档、Python 测试数与前端资源版本是否一致。

用法（项目根目录）：
    python tools/check_consistency.py

本脚本只读，不执行测试；Python 数量通过 unittest discovery 统计。
自 v101 起：内部文档（AGENTS.md / 架构设计.md / 开发交接记忆等）已移入 docs-local/
且不入库，本门禁只校验入库的公开文档；并新增「公开文档禁词检查」——公开文档里
不得出现"下一步 / 未完成 / 待验证 / 待办 / 路线图"等计划性内容。
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


# 公开文档里不得出现的"计划性措辞"（2026-09-20 起，见 AGENTS.md「文档归属规约」）
PLAN_WORDS = ("下一步", "未完成", "待验证", "待办", "路线图", "TODO")
# 豁免说明：
#   CHANGELOG.md 是历史日志，回顾性描述（如"当时真机抽检未完成"）属事实记录；
#   devices.md 的「待验证场景」是给使用者的能力矩阵（哪些机型没验证过），不是内部计划；
#   AGENTS.md 是本地 AI 规约（不入库，仅本机存在），其规则文本本身含这些词。
PLAN_WORD_EXEMPT = {"CHANGELOG.md", "devices.md", "AGENTS.md"}


def check_public_docs_plan_words(errors):
    """扫描根目录下的公开文档（已入库的 *.md），禁止出现计划性措辞。"""
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".md") or name in PLAN_WORD_EXEMPT:
            continue
        for lineno, line in enumerate(_read(name).splitlines(), start=1):
            for word in PLAN_WORDS:
                if word in line:
                    errors.append(
                        f"{name}:{lineno}：公开文档不得出现计划性措辞「{word}」"
                        "（属内部信息，应移入 docs-local/）")


def check():
    errors = []
    changelog = _read("CHANGELOG.md")
    readme = _read("README.md")
    metrics = _read("指标说明.md")
    # 注：AGENTS.md / 架构设计.md / 开发交接记忆-20260917.md / 项目评估与优化待办-*.md
    # 自 v101 起已移入 docs-local/（不入库，规约见 AGENTS.md「文档归属规约」）。
    # 门禁**不得**读取它们，否则干净检出（CI）会直接 FileNotFoundError
    # —— 2026-09-18 的 CI 实际失败原因就是这个。门禁只校验已入库的公开文档。

    changelog_version = _match(
        changelog, r"## 当前状态（[^，]+，(v[0-9.]+)）",
        "CHANGELOG 当前版本", errors)

    project_versions = {
        "CHANGELOG": changelog_version[0] if changelog_version else None,
    }
    _expect_equal("项目版本", project_versions, errors)

    # README 的文档导航不能停在旧版本，必须与权威文档正文一致。
    metrics_doc_version = _match(
        metrics, r"> 版本：(V\d+\.\d+)", "指标说明版本", errors)
    readme_metrics_version = _match(
        readme, r"`指标说明\.md`\s*\|\s*✅ 现行 (V\d+\.\d+)",
        "README 指标说明版本", errors)
    _expect_equal("指标说明文档版本", {
        "权威文档": metrics_doc_version[0] if metrics_doc_version else None,
        "README": readme_metrics_version[0] if readme_metrics_version else None,
    }, errors)

    changelog_counts = _match(
        changelog, r"\| 测试 \| Python \*\*(\d+)\*\* 条 \+ JS \*\*(\d+)\*\*",
        "CHANGELOG 测试数", errors)

    python_counts = {"实际 discovery": str(count_python_tests())}
    js_counts = {}
    if changelog_counts:
        python_counts["CHANGELOG"] = changelog_counts[0]
        js_counts["CHANGELOG"] = changelog_counts[1]
    _expect_equal("Python 测试数", python_counts, errors)
    _expect_equal("JS 断言数", js_counts, errors)

    changelog_frontend = _match(
        changelog, r"\| 前端资源版本 \| \*\*(v\d+)\*\*",
        "CHANGELOG 前端版本", errors)
    frontend_versions = {
        "CHANGELOG": changelog_frontend[0] if changelog_frontend else None,
    }
    expected_frontend = next(
        (value for value in frontend_versions.values() if value is not None), None)
    expected_refs = {"web/index.html": 3, "web/report.html": 4}
    for page, expected_count in expected_refs.items():
        refs = re.findall(r"\?v=(\d+)", _read(page))
        frontend_versions[page] = "v" + refs[0] if refs else None
        if len(refs) != expected_count:
            errors.append(
                f"{page}：应有 {expected_count} 个资源版本引用，实际 {len(refs)} 个")
        if len(set(refs)) > 1:
            errors.append(f"{page}：资源版本引用不一致 {refs}")
        if expected_frontend and refs and "v" + refs[0] != expected_frontend:
            errors.append(
                f"{page}：资源版本 v{refs[0]} 与文档 {expected_frontend} 不一致")
    _expect_equal("前端资源版本", frontend_versions, errors)

    check_public_docs_plan_words(errors)

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
