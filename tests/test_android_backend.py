"""共用装配的行为回归：真实解析器、固定输入、热切换与晚到样本。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
from android_backend import create_resolver, create_target_collectors
from sampling import MetricMailbox
from target_context import TargetContext
from target_switcher import TargetSwitcher


class FixtureAdb:
    serial = "fixture"
    fps_query_mode = "persistent"
    fps_query_transport = "persistent_shell"

    def __init__(self):
        self.calls = []
        self.cpu_reads = 0
        self.net_reads = 0

    def shell(self, args):
        self.calls.append(args)
        if args[0] == "ps":
            return "PID ARGS\n42 old.pkg\n43 new.pkg\n"
        if args == ["getconf", "CLK_TCK"]:
            return "100"
        if args[-1].endswith("/cmdline"):
            return "new.pkg" if "/43/" in args[-1] else "old.pkg"
        if args[0] == "sh":
            self.cpu_reads += 1
            busy, idle, ticks = (100, 900, 20) if self.cpu_reads == 1 else (140, 960, 70)
            proc = "S " + "0 " * 10 + f"{ticks} 0 " + "0 " * 10
            return f"cpu {busy} 0 0 {idle} 0 0 0\n__PDSEP__\n42 (game) {proc}"
        if args[-1].endswith("/smaps_rollup"):
            raise OSError("Permission denied")
        if args[:2] == ["dumpsys", "meminfo"]:
            return "TOTAL PSS: 347546 TOTAL RSS: 341080 TOTAL SWAP PSS: 89090"
        if args[-1].endswith("/net/dev"):
            self.net_reads += 1
            rx, tx = (1024, 2048) if self.net_reads == 1 else (3072, 3072)
            return f"lo: 99999 0 0 0 0 0 0 0 99999\nwlan0: {rx} 0 0 0 0 0 0 0 {tx}"
        if args[-1] == "--list":
            return "SurfaceView[old.pkg/Main](BLAST)#8"
        if "--latency" in args:
            return "16666666\n" + "".join(f"{x} {x} {x}\n" for x in (10**9 + i * 16666666 for i in range(12)))
        raise AssertionError(args)


class TestAndroidBackend(unittest.TestCase):
    def test_shared_factory_preserves_values_and_query_baselines(self):
        adb = FixtureAdb()
        resolver = create_resolver(adb, "old.pkg", "")
        self.assertEqual(resolver.resolve(), 42)
        collectors = create_target_collectors(adb, "old.pkg", "", resolver)
        self.assertEqual(adb.calls, [["ps", "-A", "-o", "PID,ARGS"], ["getconf", "CLK_TCK"]])
        self.assertIsNone(collectors["cpu"].sample(10)["cpu_proc_pct"])
        self.assertEqual(collectors["cpu"].sample(12), {"pid": 42, "cpu_total_pct": 40.0, "cpu_proc_pct": 25.0})
        self.assertEqual(collectors["mem"].sample(12), {
            "pid": 42, "pss_kb": 347546, "vmrss_kb": 341080, "swap_pss_kb": 89090})
        self.assertIsNone(collectors["net"].sample(10)["rx_kbps"])
        self.assertEqual(collectors["net"].sample(12), {"pid": 42, "rx_kbps": 1.0, "tx_kbps": 0.5})
        first = collectors["fps"].sample(10)
        self.assertEqual(first["fps"], 60.0)
        self.assertEqual(first["query_transport"], "persistent_shell")
        self.assertEqual(collectors["fps"].sample(12)["fps"], 0)
        self.assertEqual(adb.fps_query_mode, "persistent")

    def test_fixed_missing_pid_never_falls_back_to_another_process(self):
        adb = FixtureAdb()
        resolver = create_resolver(adb, "old.pkg", "", fixed_pid=99, fixed_name="old.pkg")
        self.assertIsNone(resolver.resolve())
        collectors = create_target_collectors(adb, "old.pkg", "", resolver)
        result = collectors["mem"].sample(10)
        self.assertEqual(result["error"], "no_pid")
        self.assertIsNone(result["pss_kb"])
        self.assertFalse(any(args[:2] == ["dumpsys", "meminfo"] for args in adb.calls))

    def test_default_switcher_uses_shared_factory_and_rejects_late_samples(self):
        adb = FixtureAdb()
        resolver = create_resolver(adb, "old.pkg", "")
        resolver.resolve()
        collectors = create_target_collectors(adb, "old.pkg", "", resolver)
        therm = collectors["therm"] = object()
        context = TargetContext("old.pkg", "", resolver, 42, collectors)
        old_cpu, generation = context.capture_sampler("cpu")
        old_cpu.sample(10)
        mailbox, writer, web = MetricMailbox(), Mock(), Mock()
        observed = []
        writer.write.side_effect = lambda event: observed.append((context._package, event))
        with tempfile.TemporaryDirectory() as folder:
            switcher = TargetSwitcher(adb, {}, os.path.join(folder, "config.json"), context,
                                      mailbox, writer, web, clock=lambda: 123.4567, log=lambda _: None)
            self.assertTrue(switcher.apply("new.pkg", "")[0])
        self.assertEqual(observed, [("old.pkg", {"ts": 123.457, "event": "target_switch", "to": "new.pkg", "process_pattern": ""})])
        self.assertEqual(context.state().pid, 43)
        self.assertFalse(context.publish_if_current(mailbox, "cpu", old_cpu.sample(12), 12, generation))
        new_cpu, new_generation = context.capture_sampler("cpu")
        self.assertIsNone(new_cpu.sample(12)["cpu_proc_pct"])
        self.assertTrue(context.publish_if_current(mailbox, "cpu", new_cpu.sample(14), 14, new_generation))
        self.assertIs(context.capture_sampler("therm")[0], therm)
        context.seal_publication()
        self.assertFalse(context.publish_if_current(mailbox, "cpu", {}, 15, new_generation))
