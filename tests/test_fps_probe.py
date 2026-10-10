"""Optional FPS probe contract, equivalent metric results and guarded fallback."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
from adb import Adb
from adb_fps import FpsShellError
from fps_probe import FpsDeltaDecoder, FpsProbe, select_fps_probe
from metrics.fps import FpsCollector
from sampling import SamplerScheduler, MetricMailbox, sample_metric_once
from target_context import TargetContext

TOKEN = "a" * 32
LAYER = "SurfaceView[game/demo](BLAST)#8"


def row(sequence, parts, **changes):
    return {"v": 1, "kind": "sample", "token": TOKEN, "seq": sequence,
            "base_seq": sequence - 1, "t0_ns": str(sequence * 1000),
            "t1_ns": str(sequence * 1000 + 100), "parts": parts, **changes}


def encode(text, previous):
    lines = text.encode().splitlines(keepends=True)
    return [previous.index(line) if line in previous else line.hex() for line in lines], lines


class TestFpsProbe(unittest.TestCase):
    def test_lossless_delta_reorders_shrinks_repeats_and_empty_response(self):
        decoder = FpsDeltaDecoder(TOKEN)
        previous = []
        for seq, text in enumerate(["16666666\r\n1 2 3\r\n4 5 6", "16666666\r\n4 5 6\n1 2 3\r\n", "\n", "", "16666666\n0 0 0\n"], 1):
            parts, previous = encode(text, previous)
            self.assertEqual(decoder.decode(row(seq, parts)), text)

    def test_invalid_identity_sequence_references_and_times_never_commit(self):
        decoder = FpsDeltaDecoder(TOKEN)
        decoder.decode(row(1, [b"header\n".hex()]))
        bad = [row(2, [False]), row(2, [1]), row(2, ["gg"]), row(2, [" 20"]),
               row(2, [0], token="bad"), row(3, [0]), row(2, [0], base_seq=0),
               row(2, [0], t0_ns="1000"), row(2, [0], t1_ns="1"),
               row(2, [0], t0_ns="-1"), row(2, [0], v=True)]
        for message in bad:
            with self.subTest(message=message), self.assertRaises(FpsShellError):
                decoder.decode(message)
            self.assertEqual(decoder.sequence, 1)
        self.assertEqual(decoder.decode(row(2, [0])), "header\n")

    def test_response_budgets_and_error_record(self):
        for parts in (["00"] * 513, ["00" * 10000] * 2):
            with self.assertRaises(FpsShellError):
                FpsDeltaDecoder(TOKEN).decode(row(1, parts))
        with self.assertRaisesRegex(FpsShellError, "read failed"):
            FpsDeltaDecoder(TOKEN).decode(row(1, [], kind="error", reason="read failed"))

    def test_metric_equivalence_for_continuous_sparse_stale_and_reset_buffers(self):
        stamps = [10**9 + i * 16666666 for i in range(12)]
        responses = ["16666666\n" + "".join(f"{x}\t{x}\t{x}\n" for x in seq)
                     for seq in [stamps, stamps[4:] + [stamps[-1] + 16666666, stamps[-1] + 50000000],
                                 stamps[4:] + [stamps[-1] + 16666666, stamps[-1] + 50000000],
                                 [3*10**9, 3*10**9+16666666], [], stamps[:8]]]
        decoder = FpsDeltaDecoder(TOKEN)
        legacy, modern = Mock(), Mock()
        legacy.fps_query_transport, modern.fps_query_transport = "persistent", "native_fps_probe"
        legacy.fps_probe_recording = modern.fps_probe_recording = None
        legacy.fps_query_fallback_reason = modern.fps_query_fallback_reason = None
        legacy.fps_query_mode = modern.fps_query_mode = "persistent"
        old, new = FpsCollector(legacy, "game", ""), FpsCollector(modern, "game", "")
        old._set_layer(LAYER); new._set_layer(LAYER)
        previous = []
        for i, text in enumerate(responses, 1):
            parts, previous = encode(text, previous)
            legacy.fps_shell.return_value = text
            modern.fps_shell.return_value = decoder.decode(row(i, parts))
            a, b = old.sample(i * .5), new.sample(i * .5)
            a.pop("query_transport"); b.pop("query_transport")
            self.assertEqual(a, b)

    def test_probe_failure_closes_once_and_keeps_persistent_fallback(self):
        adb = Adb.__new__(Adb)
        adb._base, adb.fps_query_mode, adb.fps_probe_path = ["adb"], "probe", "artifact"
        failed = Mock(layer=LAYER); failed.query.side_effect = FpsShellError("sequence gap")
        fallback = Mock(); fallback.query.return_value = "latency"
        with patch("fps_probe.FpsProbe", return_value=failed) as factory, patch("adb_fps.FpsShell", return_value=fallback):
            for _ in range(2):
                self.assertEqual(adb.fps_shell(["dumpsys", "SurfaceFlinger", "--latency", LAYER]), "latency")
            factory.assert_called_once()
        failed.close.assert_called_once()
        self.assertEqual(adb.fps_query_transport, "persistent")
        self.assertIn("sequence gap", adb.fps_query_fallback_reason)
        self.assertIsNone(adb.fps_probe_recording)
        adb.close_fps_shell(); fallback.close.assert_called_once()

    def test_layer_change_restarts_owned_stream(self):
        adb = Adb.__new__(Adb)
        adb._base, adb.fps_query_mode, adb.fps_probe_path = ["adb"], "probe", "artifact"
        first, second = Mock(layer=LAYER), Mock(layer=LAYER+"new")
        first.query.return_value = second.query.return_value = "data"
        with patch("fps_probe.FpsProbe", side_effect=[first, second]) as factory:
            adb.fps_shell(["dumpsys", "SurfaceFlinger", "--latency", LAYER])
            adb.fps_shell(["dumpsys", "SurfaceFlinger", "--latency", LAYER+"new"])
            self.assertEqual(factory.call_count, 2)
        first.close.assert_called_once(); adb.close_fps_shell(); second.close.assert_called_once()

    def test_new_collector_or_process_reset_closes_old_probe(self):
        adb = Mock(fps_query_mode="probe")
        adb.shell.return_value = ""
        collector = FpsCollector(adb, "game", "")
        collector._ever_surfaceview = True
        collector._try_resolve = Mock()
        collector.sample(1)
        self.assertIs(adb._fps_probe_owner, collector)
        self.assertTrue(adb.close_fps_probe.called)
        adb.close_fps_probe.reset_mock()
        collector._set_layer(LAYER)
        collector._process_reset_requested.set()
        collector.sample(2)
        self.assertTrue(adb.close_fps_probe.called)

    def test_device_paced_scheduler_does_not_add_full_period(self):
        for transport, expected in [("native_fps_probe", .05), ("persistent", .5)]:
            session = Mock(is_stopping=False)
            session.wait.side_effect = lambda delay: setattr(session, "is_stopping", True)
            scheduler = SamplerScheduler(session, Mock(), Mock(), {"fps": .5},
                sample_once=lambda *a: {"published": True, "value": {"query_transport": transport}}, clock=lambda: 0)
            scheduler._run_metric_loop("fps", .5)
            session.wait.assert_called_once_with(expected)

    def test_device_times_recorded_without_private_fields_in_main_data(self):
        sampler = Mock()
        sampler.sample.return_value = {"fps": 60, "query_transport": "native_fps_probe",
            "_raw_frames": {"available": True}, "_fps_probe_recording": {
                "probe": {"seq": 1}, "clock_anchor": {"method": "ready_causal_bounds"},
                "device_started_ns": "100", "device_completed_ns": "200"}}
        resolver = Mock(pid=1, proc_name="game")
        context = TargetContext("game", "", resolver, 1, {"fps": sampler})
        sink = Mock(); context.set_record_sink(sink)
        result = sample_metric_once(context, MetricMailbox(), "fps")
        self.assertNotIn("_fps_probe_recording", result["value"])
        recording = sink.call_args.args[3]
        self.assertEqual(recording["backend"], "device_fps_probe")
        self.assertEqual(recording["timing"]["device_started_ns"], "100")

    def test_selection_supports_both_abis_and_falls_back_on_preflight(self):
        adb = Mock()
        for abi in ("arm64-v8a", "armeabi-v7a"):
            adb._run.return_value = "34\n" + abi + "\n"
            self.assertEqual(select_fps_probe(adb)["abi"], abi)
        for properties in ("25\narm64-v8a\n", "34\nx86_64\n", "broken", ""):
            adb._run.return_value = properties
            self.assertIsNone(select_fps_probe(adb)["path"])
        adb._run.side_effect = TimeoutError("offline")
        self.assertIsNone(select_fps_probe(adb)["path"])

    def test_bad_layer_never_deploys_and_failed_push_cleans_only_owned_path(self):
        adb = Mock(_base=["adb"])
        for layer in ("", "bad\nlayer", "bad\0layer", "x" * 2049):
            with self.assertRaises(FpsShellError):
                FpsProbe(adb, "unused", layer)
        adb._run.assert_not_called()
        artifact = Path(__file__).parents[1] / "collector/probes/bin/perfcollect_fps_arm64-v8a.elf"
        def run(args, **kwargs):
            if args[0] == "push":
                raise OSError("push failed")
        adb._run.side_effect = run
        with self.assertRaisesRegex(OSError, "push failed"):
            FpsProbe(adb, artifact, LAYER)
        cleanup = str(adb._run.call_args_list[-2:])
        self.assertIn("perfcollect_fps_", cleanup)
        self.assertIn("probe.elf", cleanup)
        self.assertNotIn("-rf", cleanup)

    def test_distribution_includes_fps_bundles_and_preflight_errors_fall_back(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("fps_probe_distribution", Path(__file__).parents[1] / "tools/make_zip.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        paths = {relative for _, relative in module.collect()}
        for abi in ("arm64-v8a", "armeabi-v7a"):
            self.assertIn("collector/probes/bin/perfcollect_fps_" + abi + ".elf", paths)
        adb = Mock(); adb._run.side_effect = RuntimeError("ADB preflight unavailable")
        self.assertIn("ADB preflight unavailable", select_fps_probe(adb)["reason"])
