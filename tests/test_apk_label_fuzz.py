# -*- coding: utf-8 -*-
"""APK AXML/ARSC 解析器的确定性异常输入与轻量模糊测试。

不依赖真实 APK，也不追求证明解析正确；目标是锁定不可信二进制输入必须
快速失败、不得抛异常、不得因伪造长度/层级进行失控分配或无限遍历。
"""

import os
import random
import struct
import sys
import time
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

import apk_label  # noqa: E402


def _container(ctype, child=b""):
    return struct.pack("<HHI", ctype, 8, 8 + len(child)) + child


class TestApkLabelFuzz(unittest.TestCase):
    def test_random_bytes_never_escape_parser(self):
        rng = random.Random(0xA11CE)
        started = time.monotonic()
        for _ in range(800):
            size = rng.randrange(0, 4097)
            data = rng.randbytes(size)
            label, resource = apk_label.parse_axml_label(data)
            self.assertTrue(label is None or isinstance(label, str))
            self.assertTrue(resource is None or isinstance(resource, int))
            value = apk_label.parse_arsc_label(data, rng.getrandbits(32))
            self.assertTrue(value is None or isinstance(value, str))
        self.assertLess(time.monotonic() - started, 10.0)

    def test_mutated_chunk_headers_never_escape_parser(self):
        rng = random.Random(0xC0FFEE)
        seeds = [
            _container(0x0003, _container(0x0001)),
            _container(0x0002, _container(0x0200, _container(0x0201))),
            struct.pack("<HHI", 0x0102, 36, 36) + b"\0" * 28,
        ]
        for seed in seeds:
            for _ in range(120):
                data = bytearray(seed)
                for _ in range(1 + rng.randrange(8)):
                    if data:
                        index = rng.randrange(len(data))
                        data[index] ^= 1 << rng.randrange(8)
                apk_label.parse_axml_label(bytes(data))
                apk_label.parse_arsc_label(bytes(data), rng.getrandbits(32))

    def test_hostile_string_count_is_rejected_without_allocation(self):
        # 28 字节字符串池头，却声明 0xffffffff 个字符串；必须在 unpack 前拒绝。
        data = bytearray(28)
        struct.pack_into("<HHI", data, 0, 0x0001, 28, 28)
        struct.pack_into("<IIIII", data, 8, 0xFFFFFFFF, 0, 0x100, 28, 0)
        self.assertEqual(apk_label._parse_string_pool(bytes(data), 0), [])

    def test_unterminated_uleb_is_bounded(self):
        with self.assertRaises(ValueError):
            apk_label._read_uleb(b"\x80" * 64, 0)

    def test_deep_container_nesting_stops_at_limit(self):
        data = b""
        for index in range(apk_label._MAX_CHUNK_DEPTH + 20):
            data = _container(0x0003 if index % 2 else 0x0002, data)
        chunks = list(apk_label._chunks(data))
        self.assertLessEqual(len(chunks), apk_label._MAX_CHUNK_DEPTH + 1)

    def test_axml_oversize_guard_returns_empty_result(self):
        data = b"\0" * (apk_label._MAX_AXML_SIZE + 1)
        self.assertEqual(apk_label.parse_axml_label(data), (None, None))


if __name__ == "__main__":
    unittest.main()
