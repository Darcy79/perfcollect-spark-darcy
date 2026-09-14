# -*- coding: utf-8 -*-
"""v70：采集端缺数原因码补全（mem / network / thermal）。

背景（2026-09-11 用户提问）：报告「数据完整度」里出现「该指标无值 2 / 该指标无值 1」，
用户看不出含义。核查发现 mem.py / network.py / thermal.py 在「进程未解析到」与「读取失败」
时**只返回空值、不写 error 字段**（只有 cpu.py / fps.py 写了），前端只能兜底显示
「该指标无值」——run 20260911_162353 里内存 41 个缺数点、网络 41、温度 40 **全部**落在
这个兜底分类里，无法判读是链路问题还是进程问题。

本文件锁定两条不变量：
  1) 对应失败分支必须带正确 error 码（no_pid / read_fail）；
  2) **正常路径不得带 error 码**（否则会污染 ChannelAlertTracker 的缺数告警口径）。
"""

import os
import sys
import unittest

_COLLECTOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "collector")
if _COLLECTOR not in sys.path:
    sys.path.insert(0, _COLLECTOR)

from metrics.mem import MemCollector           # noqa: E402
from metrics.network import NetworkCollector   # noqa: E402
from metrics.thermal import ThermalCollector   # noqa: E402


class MockAdb:
    """按子串匹配返回响应；fail 命中的命令抛异常（模拟 adb 通道错误）。"""

    def __init__(self, responses=None, fail=()):
        self.responses = responses or {}
        self.fail = tuple(fail)
        self.calls = []

    def shell(self, args):
        self.calls.append(list(args))
        joined = " ".join(args)
        for pat in self.fail:
            if pat in joined:
                raise RuntimeError("adb: device offline")
        for pat, out in self.responses.items():
            if pat in joined:
                return out
        return ""


class MockResolver:
    def __init__(self, pid):
        self.pid = pid

    def current_pid(self, ts=0.0):
        return self.pid


SMAPS_OK = "Rss:  100000 kB\nPss:  90000 kB\n"
NET_OK = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets\n"
    "    lo: 999 1 0 0 0 0 0 0 999 1 0 0 0 0 0 0\n"
    " wlan0: 1000000 5 0 0 0 0 0 0 2000000 6 0 0 0 0 0 0\n"
)


class TestMemErrorCodes(unittest.TestCase):
    def test_no_pid_sets_no_pid_code(self):
        adb = MockAdb()
        c = MemCollector(adb, MockResolver(None), package="com.tencent.mm")
        r = c.sample(1.0)
        self.assertIsNone(r["pss_kb"])
        self.assertEqual(r["error"], "no_pid")
        self.assertEqual([x for x in adb.calls if "meminfo" in " ".join(x)], [],
                         "pid 缺失不得回退包名维度")

    def test_read_failure_sets_read_fail_code(self):
        adb = MockAdb(fail=("smaps_rollup", "meminfo"))
        c = MemCollector(adb, MockResolver(5838))
        r = c.sample(1.0)
        self.assertEqual(r["error"], "read_fail")

    def test_unparsable_output_sets_read_fail_code(self):
        # 命令成功但没有可解析的 PSS → 同样是"读取失败"，不能静默空值
        adb = MockAdb({"dumpsys meminfo": "no usable summary here\n"},
                      fail=("smaps_rollup",))
        c = MemCollector(adb, MockResolver(5838))
        r = c.sample(1.0)
        self.assertIsNone(r["pss_kb"])
        self.assertEqual(r["error"], "read_fail")

    def test_normal_path_has_no_error_code(self):
        adb = MockAdb({"cat /proc/5838/smaps_rollup": SMAPS_OK})
        c = MemCollector(adb, MockResolver(5838))
        r = c.sample(1.0)
        self.assertEqual(r["pss_kb"], 90000)
        self.assertNotIn("error", r)

    def test_throttled_point_has_no_error_code(self):
        # 节流点（未到采样间隔）是设计内的空点，不该被当成错误
        adb = MockAdb({"cat /proc/5838/smaps_rollup": SMAPS_OK})
        c = MemCollector(adb, MockResolver(5838), min_interval=2.0)
        c.sample(1.0)
        r = c.sample(1.5)
        self.assertTrue(r.get("throttled"))
        self.assertNotIn("error", r)


class TestNetworkErrorCodes(unittest.TestCase):
    def test_no_pid_sets_no_pid_code(self):
        c = NetworkCollector(MockAdb(), MockResolver(None))
        r = c.sample(1.0)
        self.assertEqual(r["error"], "no_pid")

    def test_read_failure_sets_read_fail_code(self):
        adb = MockAdb(fail=("net/dev",))
        c = NetworkCollector(adb, MockResolver(5838))
        r = c.sample(1.0)
        self.assertEqual(r["error"], "read_fail")

    def test_first_point_is_not_error_then_values_appear(self):
        # 首点无上次基准 → 无值但**不是错误**（前端显示"未取到值(采样未就绪)"）
        adb = MockAdb({"net/dev": NET_OK})
        c = NetworkCollector(adb, MockResolver(5838))
        r1 = c.sample(1.0)
        self.assertIsNone(r1["rx_kbps"])
        self.assertNotIn("error", r1)
        adb.responses["net/dev"] = NET_OK.replace("1000000", "2000000").replace("2000000 6", "4000000 6")
        r2 = c.sample(2.0)
        self.assertNotIn("error", r2)
        self.assertIsNotNone(r2["rx_kbps"])

    def test_normal_path_has_no_error_code(self):
        adb = MockAdb({"net/dev": NET_OK})
        c = NetworkCollector(adb, MockResolver(5838))
        c.sample(1.0)
        r = c.sample(2.0)
        self.assertNotIn("error", r)


class TestThermalErrorCodes(unittest.TestCase):
    def test_all_sources_fail_sets_read_fail_code(self):
        c = ThermalCollector(MockAdb())          # 空响应 → sys 与 dumpsys 都读不到
        r = c.sample(1.0)
        self.assertIsNone(r["temp_c"])
        self.assertEqual(r["error"], "read_fail")

    def test_normal_path_has_no_error_code(self):
        adb = MockAdb({
            "/battery/temp": "280",              # 0.1°C
            "/battery/current_now": "500000",
            "/battery/voltage_now": "4300000",
        })
        c = ThermalCollector(adb)
        r = c.sample(1.0)
        self.assertEqual(r["temp_c"], 28.0)
        self.assertNotIn("error", r)

    def test_out_of_range_keeps_its_own_code(self):
        # 温度超量程仍报自己的码（不被 read_fail 覆盖）
        adb = MockAdb({"/battery/temp": "99999", "/battery/voltage_now": "4300000"})
        c = ThermalCollector(adb)
        r = c.sample(1.0)
        self.assertIsNone(r["temp_c"])
        self.assertEqual(r["error"], "temperature_out_of_range")


if __name__ == "__main__":
    unittest.main()
