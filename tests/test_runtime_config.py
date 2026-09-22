# -*- coding: utf-8 -*-
"""公共默认配置与本机目标选择的隔离回归。"""

import json
import os
import sys
import tempfile
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from runtime_config import (  # noqa: E402
    load_config, persist_runtime_target, runtime_config_path,
)


class TestRuntimeConfig(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base_path = os.path.join(self.tempdir.name, "config.json")
        self.base = {
            "package": "com.tencent.mm", "process_pattern": "appbrand",
            "serial": "device", "interval_ms": 1000,
        }
        with open(self.base_path, "w", encoding="utf-8") as stream:
            json.dump(self.base, stream)

    def test_missing_local_file_uses_public_defaults(self):
        self.assertEqual(load_config(self.base_path), self.base)

    def test_persist_keeps_base_immutable_and_writes_newline(self):
        with open(self.base_path, "rb") as stream:
            before = stream.read()
        local_path = persist_runtime_target(self.base_path, " game.pkg ", "")
        with open(self.base_path, "rb") as stream:
            self.assertEqual(stream.read(), before)
        self.assertEqual(local_path, runtime_config_path(self.base_path))
        with open(local_path, "rb") as stream:
            self.assertTrue(stream.read().endswith(b"\n"))
        merged = load_config(self.base_path)
        self.assertEqual((merged["package"], merged["process_pattern"]),
                         ("game.pkg", ""))
        self.assertEqual(merged["serial"], "device")

    def test_invalid_local_file_falls_back_without_stopping_startup(self):
        with open(runtime_config_path(self.base_path), "w", encoding="utf-8") as stream:
            stream.write("{broken")
        messages = []
        self.assertEqual(load_config(self.base_path, messages.append), self.base)
        self.assertIn("本机目标配置读取失败", messages[0])


if __name__ == "__main__":
    unittest.main()
