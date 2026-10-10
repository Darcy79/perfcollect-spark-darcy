"""Device-free protocol, ownership, stream and fallback tests."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
from cpu_probe import CpuCounterDecoder, CpuProbeWorker, ProbeError, ProbeTransport, validate_probe_options, probe_artifact_kind, select_cpu_probe
from capture_session import CaptureSession
from sampling import MetricMailbox
from target_context import TargetContext

TOKEN = "a" * 32

def row(seq=1, **values):
    record = {"v": 1, "kind": "sample", "token": TOKEN, "seq": seq, "pid": 42,
              "name_hex": "game".encode().hex(), "start_ticks": "100", "proc_ticks": "10",
              "total": "1000", "busy": "400", "t0_ns": "1000000000", "t1_ns": "1001000000"}
    record.update(values)
    return record

class TestCpuProbeProtocol(unittest.TestCase):
    def decoder(self):
        return CpuCounterDecoder(TOKEN, 42, "game", 100)

    def test_device_clock_and_single_core_cpu_scaling(self):
        decoder = self.decoder()
        self.assertIsNone(decoder.decode(row())["cpu_proc_pct"])
        value = decoder.decode(row(2, proc_ticks="60", total="1200", busy="500", t0_ns="1250000000", t1_ns="1251000000"))
        self.assertEqual(value["cpu_proc_pct"], 200)
        self.assertEqual(value["cpu_total_pct"], 50)

    def test_pid_reuse_resets_process_baseline(self):
        decoder = self.decoder()
        decoder.decode(row())
        result = decoder.decode(row(2, start_ticks="200", proc_ticks="2", total="1200", busy="500", t0_ns="1250000000", t1_ns="1251000000"))
        self.assertIsNone(result["cpu_proc_pct"])
        self.assertEqual(result["baseline_reset"], "process_start_changed")
        self.assertEqual(result["cpu_total_pct"], 50)

    def test_protocol_identity_sequence_and_bad_fields(self):
        for invalid in ({"token": "wrong"}, {"v": True}, {"v": 2}, {"seq": 2}, {"pid": 43},
                        {"name_hex": "bad"}, {"kind": "error"}, {"total": True}, {"busy": "1001"},
                        {"proc_ticks": -1}, {"t1_ns": "1"}, {"total": "9" * 100}):
            with self.subTest(invalid=invalid), self.assertRaises(ProbeError):
                self.decoder().decode(row(**invalid))

    def test_regressed_counters_or_time_fail(self):
        for invalid in ({"proc_ticks": "1"}, {"total": "999"}, {"busy": "399"}, {"t0_ns": "1000000000"}, {"busy": "800"}):
            decoder = self.decoder()
            decoder.decode(row())
            args = {"total": "1100", "busy": "450", "t0_ns": "1250000000", "t1_ns": "1251000000"}
            args.update(invalid)
            with self.subTest(invalid=invalid), self.assertRaises(ProbeError):
                decoder.decode(row(2, **args))

    def test_probe_options_require_valid_interval_and_dex(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "classes.dex"
            path.write_bytes(b"dex\n035\x00payload")
            validate_probe_options(path, .25)
            for interval in (0, .1, float("nan"), float("inf"), 61):
                with self.assertRaises(ValueError):
                    validate_probe_options(path, interval)
            path.write_bytes(b"not dex")
            with self.assertRaises(ValueError):
                validate_probe_options(path, .25)

class Resolver:
    pid = 42
    proc_name = "game"
    def current_pid(self):
        return self.pid

class Query:
    clk_tck = 100
    def __init__(self, session):
        self.session = session
        self.calls = 0
        self.resets = 0
    def reset_baseline(self):
        self.resets += 1
    def sample(self, ts):
        self.calls += 1
        self.session.request_stop()
        return {"cpu_proc_pct": None, "pid": 42}

class FakeStream:
    token = TOKEN
    anchor = {"method": "test"}
    def __init__(self, records, on_receive=None):
        self.records = iter(records)
        self.closed = False
        self.on_receive = on_receive
    def start(self):
        return self
    def receive(self, timeout):
        if self.on_receive:
            self.on_receive()
        value = next(self.records)
        if isinstance(value, Exception):
            raise value
        return value, 100.0, 100000000000
    def close(self):
        self.closed = True

class TestCpuProbeWorker(unittest.TestCase):
    def setUp(self):
        self.session = CaptureSession()
        self.query = Query(self.session)
        self.context = TargetContext("game", "", Resolver(), 42, {"cpu": self.query})
        self.box = MetricMailbox()
        self.writer = Mock()
    def worker(self, factory):
        return CpuProbeWorker(self.session, self.context, self.box, object(), "fake.dex", .25, 1,
                              self.writer, transport_factory=factory)

    def test_stream_is_exclusive_and_preserves_raw_record(self):
        records = []
        self.context.set_record_sink(lambda *args: records.append(args))
        stream = FakeStream([row()], self.session.request_stop)
        worker = self.worker(lambda *args: stream)
        worker.run()
        worker.raise_if_failed()
        self.assertEqual(self.query.calls, 0)
        self.assertTrue(stream.closed)
        self.assertEqual(records[0][3]["backend"], "device_cpu_probe")
        self.assertEqual(records[0][3]["probe"]["proc_ticks"], "10")

    def test_start_failure_falls_back_and_resets(self):
        worker = self.worker(Mock(side_effect=ProbeError("not ready")))
        worker.run()
        worker.raise_if_failed()
        self.assertEqual((self.query.calls, self.query.resets), (1, 1))
        self.assertEqual(worker.snapshot()["active"], "adb_query")
        self.assertIn("not ready", worker.snapshot()["reason"])

    def test_stream_exit_falls_back_without_reusing_baseline(self):
        stream = FakeStream([row(), ProbeError("closed")])
        worker = self.worker(lambda *args: stream)
        worker.run()
        self.assertTrue(stream.closed)
        self.assertEqual((self.query.calls, self.query.resets), (1, 1))
        transitions = [call.args[0]["active"] for call in self.writer.write.call_args_list]
        self.assertEqual(transitions, ["device_cpu_probe", "adb_query"])

    def test_stall_falls_back_instead_of_stopping_capture(self):
        stream = FakeStream([TimeoutError("stalled")])
        worker = self.worker(lambda *args: stream)
        # After startup last_record=0; the receive timeout occurs at 10s.
        with patch("cpu_probe.time.monotonic", side_effect=[0, 0, 10, 10, 10]):
            worker.run()
        worker.raise_if_failed()
        self.assertEqual(self.query.calls, 1)
        self.assertEqual(worker.snapshot()["active"], "adb_query")

    def test_stale_backend_event_cannot_cross_target_switch(self):
        self.context.switch_target("other", "", Resolver(), 42, {"cpu": self.query}, self.box)
        self.assertFalse(self.context.record_event_if_current(self.writer, {"event": "cpu_backend"}, 0))
        self.writer.write.assert_not_called()

    def test_switch_drops_old_stream_sample_and_restarts_probe(self):
        other = Query(self.session)
        first = FakeStream([row()], lambda: self.context.switch_target("other", "", Resolver(), 42, {"cpu": other}, self.box))
        second = FakeStream([row()], self.session.request_stop)
        recorded = []
        self.context.set_record_sink(lambda key, value, target, info: recorded.append(target.generation))
        factory = Mock(side_effect=[first, second])
        self.worker(factory).run()
        self.assertEqual(recorded, [1])
        self.assertEqual(factory.call_count, 2)
        self.assertTrue(first.closed and second.closed)
        self.assertEqual(self.query.calls + other.calls, 0)

    def test_new_pid_retries_probe_after_query_fallback(self):
        resolver = self.context.state().resolver
        def query(ts):
            self.query.calls += 1
            resolver.pid = 84
            return {"pid": 84, "cpu_proc_pct": None}
        self.query.sample = query
        second = FakeStream([row(pid=84)], self.session.request_stop)
        factory = Mock(side_effect=[ProbeError("old process gone"), second])
        worker = self.worker(factory)
        worker.run()
        worker.raise_if_failed()
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(factory.call_args.args[2], 84)
        self.assertEqual(self.query.calls, 1)
        self.assertEqual(worker.snapshot()["active"], "device_cpu_probe")
        self.assertTrue(second.closed)

    def test_restart_during_receive_rejects_old_pid_and_rebuilds(self):
        resolver = self.context.state().resolver
        first = FakeStream([row()], lambda: setattr(resolver, "pid", 84))
        second = FakeStream([row(pid=84)], self.session.request_stop)
        records = []
        self.context.set_record_sink(lambda key, value, target, info: records.append((value["pid"], target.pid)))
        factory = Mock(side_effect=[first, second])
        worker = self.worker(factory)
        worker.run()
        worker.raise_if_failed()
        self.assertEqual(records, [(84, 84)])
        self.assertEqual(worker.snapshot()["probe_samples"], 1)
        self.assertEqual(self.context.seal_publication()["stale_samples_rejected"], 1)
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(self.query.calls, 0)
        self.assertTrue(first.closed and second.closed)

    def test_identity_guard_rejects_changed_process_name(self):
        self.context.state().resolver.proc_name = "other"
        records = []
        self.context.set_record_sink(lambda *args: records.append(args))
        self.assertFalse(self.context.publish_if_current(self.box, "cpu", {}, 1, 0,
                         expected_identity=(42, "game")))
        self.assertEqual(records, [])
        self.assertEqual(self.context.seal_publication()["stale_samples_rejected"], 1)

    def test_stop_race_during_worker_start_is_safe(self):
        worker = self.worker(Mock())
        self.session.request_stop()
        self.assertIsNone(worker.start())

    def test_event_write_failure_is_fatal(self):
        self.writer.write.side_effect = OSError("disk failed")
        worker = self.worker(Mock(side_effect=ProbeError("start failed")))
        worker.run()
        self.assertTrue(self.session.is_stopping)
        with self.assertRaisesRegex(RuntimeError, "disk failed"):
            worker.raise_if_failed()

class TestProbeTransport(unittest.TestCase):
    def transport(self, content):
        transport = ProbeTransport(Mock(_base=["adb", "-s", "test-device"]), "fake.dex", 42, "game", .25)
        transport.process = Mock(stdout=io.StringIO(content))
        return transport

    def test_reader_preserves_receive_time_and_raw_fields(self):
        transport = self.transport(json.dumps(row()) + "\n")
        transport._read()
        self.assertIsNone(transport.error)
        value, wall, mono = transport.messages.get_nowait()
        self.assertEqual(value["proc_ticks"], "10")
        self.assertGreater(wall, 0)
        self.assertGreater(mono, 0)

    def test_oversize_invalid_and_overflow_fail_explicitly(self):
        for content in ("x" * 16385, "[]\n", "bad\n", (json.dumps(row()) + "\n") * 65):
            with self.subTest(size=len(content)):
                transport = self.transport(content)
                transport._read()
                self.assertIsNotNone(transport.error)
                self.assertLessEqual(transport.messages.qsize(), 64)
        self.assertIn("queue overflow: pending=64, rejected=1", str(transport.error))

    def test_partial_deployment_cleanup_uses_only_owned_paths(self):
        adb = Mock()
        transport = ProbeTransport(adb, "fake.dex", 42, "game", .25)
        transport._owned = True
        transport.close()
        commands = [call.args[0] for call in adb._run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertTrue(all(transport.token in " ".join(command) for command in commands))
        self.assertNotIn("-rf", " ".join(str(command) for command in commands))

    def test_unowned_directory_is_never_removed(self):
        adb = Mock()
        transport = ProbeTransport(adb, "fake.dex", 42, "game", .25)
        transport.close()
        adb._run.assert_not_called()

    def test_ready_handshake_and_owned_cleanup(self):
        transport = ProbeTransport(Mock(_base=["adb", "-s", "test-device"]), "fake.dex", 42, "game", .25)
        ready = {"v": 1, "kind": "ready", "token": transport.token, "pid": 42,
                 "clock": "device_monotonic", "device_ns": "1234", "seq": 0}
        process = Mock(stdin=io.StringIO(), stdout=io.StringIO(json.dumps(ready) + "\n"))
        process.wait.return_value = 0
        with patch("cpu_probe.probe_artifact_kind", return_value="java_dex"), patch("cpu_probe.subprocess.Popen", return_value=process) as popen:
            transport.start()
            self.assertEqual(transport.anchor["device_ns"], "1234")
            self.assertGreaterEqual(int(transport.anchor["uncertainty_ns"]), 0)
            self.assertIn("app_process", popen.call_args.args[0][-1])
            transport.close()
        self.assertFalse(transport.reader.is_alive())
        self.assertFalse(transport._owned)

    def test_bad_ready_closes_transport(self):
        transport = ProbeTransport(Mock(_base=["adb", "-s", "test-device"]), "fake.dex", 42, "game", .25)
        process = Mock(stdin=io.StringIO(), stdout=io.StringIO('{"kind":"ready","token":"wrong"}\n'))
        process.wait.return_value = 0
        with patch("cpu_probe.probe_artifact_kind", return_value="java_dex"), patch("cpu_probe.subprocess.Popen", return_value=process), self.assertRaises(ProbeError):
            transport.start()
        self.assertIsNone(transport.process)
        self.assertFalse(transport._owned)


class TestNativeProbeArtifact(unittest.TestCase):
    @staticmethod
    def elf(bits=2, machine=183, kind=3):
        header = bytearray(64)
        header[:7] = b"\x7fELF" + bytes([bits, 1, 1])
        header[16:18] = kind.to_bytes(2, "little")
        header[18:20] = machine.to_bytes(2, "little")
        return header

    def test_arm_executables_accepted_and_other_formats_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "probe.elf"
            for bits, machine in ((2, 183), (1, 40)):
                path.write_bytes(self.elf(bits, machine))
                validate_probe_options(path, .25)
                self.assertEqual(probe_artifact_kind(path), "native_c")
            for data in (self.elf(2, 62), self.elf(2, 183, 1), self.elf(1, 183), b"\x7fELF", self.elf()[:52]):
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    validate_probe_options(path, .25)

    def test_native_launch_exec_permission_and_exact_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "probe.elf"
            path.write_bytes(self.elf())
            adb = Mock(_base=["adb", "-s", "test-device"])
            transport = ProbeTransport(adb, path, 42, "game", .25)
            ready = {"v": 1, "kind": "ready", "token": transport.token, "pid": 42,
                     "clock": "device_monotonic", "device_ns": "1234", "seq": 0}
            process = Mock(stdin=io.StringIO(), stdout=io.StringIO(json.dumps(ready) + "\n"))
            process.wait.return_value = 0
            with patch("cpu_probe.subprocess.Popen", return_value=process) as popen:
                transport.start()
                command = popen.call_args.args[0][-1]
                self.assertIn(transport.directory + "/probe.elf", command)
                self.assertNotIn("app_process", command)
                self.assertEqual(transport.implementation, "native_c")
                self.assertTrue(any("'500'" in str(c) for c in adb._run.call_args_list))
                transport.close()
            cleanup = adb._run.call_args_list[-2:]
            self.assertIn(transport.directory + "/probe.elf", str(cleanup[0]))
            self.assertNotIn("-rf", str(cleanup))

    def test_failed_native_push_cleans_only_its_deployment(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "probe.elf"
            path.write_bytes(self.elf())
            adb = Mock(_base=["adb"])
            def run(args, **kwargs):
                if args[0] == "push":
                    raise OSError("push failed")
            adb._run.side_effect = run
            transport = ProbeTransport(adb, path, 42, "game", .25)
            with self.assertRaisesRegex(OSError, "push failed"):
                transport.start()
            self.assertFalse(transport._owned)
            self.assertIn("probe.elf", str(adb._run.call_args_list[-2]))


class TestDefaultNativeProbe(unittest.TestCase):
    def test_device_abi_order_and_api_selection(self):
        adb = Mock()
        for properties, abi in (("34\narm64-v8a,armeabi-v7a\narm64-v8a\n", "arm64-v8a"),
                                ("26\n\narmeabi-v7a\n", "armeabi-v7a")):
            adb._run.return_value = properties
            result = select_cpu_probe(adb)
            self.assertEqual(result["abi"], abi)
            self.assertEqual(result["implementation"], "native_c")
            self.assertIsNone(result["reason"])
            self.assertTrue(Path(result["path"]).is_file())

    def test_unsupported_device_and_failed_preflight_keep_queries(self):
        adb = Mock()
        for properties, reason in (("25\narm64-v8a\n", "native_probe_requires_api26"),
                                   ("34\nx86_64,x86\nx86_64\n", "native_probe_unsupported_abi"),
                                   ("invalid\n", "native_probe_preflight_failed")):
            adb._run.return_value = properties
            result = select_cpu_probe(adb)
            self.assertIsNone(result["path"])
            self.assertTrue(result["reason"].startswith(reason))
        adb._run.side_effect = TimeoutError("unavailable")
        self.assertIn("unavailable", select_cpu_probe(adb)["reason"])

    def test_missing_invalid_or_wrong_abi_bundle_keeps_queries(self):
        adb = Mock(); adb._run.return_value = "34\narm64-v8a\n"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "perfcollect_cpu_arm64-v8a.elf"
            for content in (None, b"dex\n035\x00payload",
                            (Path(__file__).parents[1] / "collector/probes/bin/perfcollect_cpu_armeabi-v7a.elf").read_bytes()):
                if content is not None:
                    path.write_bytes(content)
                result = select_cpu_probe(adb, artifact_dir=folder)
                self.assertIsNone(result["path"])
                self.assertTrue(result["reason"].startswith("native_probe_preflight_failed"))

    def test_explicit_and_disabled_do_not_probe_device(self):
        adb = Mock()
        result = select_cpu_probe(adb, None)
        self.assertEqual(result["mode"], "query")
        self.assertEqual(result["reason"], "disabled_by_user")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "classes.dex"; path.write_bytes(b"dex\n035\x00payload")
            result = select_cpu_probe(adb, str(path))
            self.assertEqual(result["implementation"], "java_dex")
            self.assertEqual(result["path"], str(path.resolve()))
        adb._run.assert_not_called()

    def test_distribution_includes_both_production_bundles(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("probe_packaging_test", Path(__file__).parents[1] / "tools/make_zip.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        paths = {rel for _, rel in module.collect()}
        for abi in ("arm64-v8a", "armeabi-v7a"):
            self.assertIn("collector/probes/bin/perfcollect_cpu_" + abi + ".elf", paths)
        self.assertFalse(any("docs-local" in path or path.startswith("collector/output/") for path in paths))
