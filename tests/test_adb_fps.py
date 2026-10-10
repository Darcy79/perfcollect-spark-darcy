# -*- coding: utf-8 -*-
import io
import os
import queue
import re
import sys
import threading
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
from adb import Adb
from adb_fps import FpsShell, FpsShellError
from sampling import SamplerScheduler

ARGS = ["dumpsys", "SurfaceFlinger", "--latency", "SurfaceView[game/Main](BLAST)#1"]


class Pipe:
    def __init__(self):
        self.q = queue.Queue()
        self.closed = False
    def readline(self, limit):
        return self.q.get()
    def close(self):
        self.closed = True


class Input:
    def __init__(self, process):
        self.process = process
        self.writes = []
    def write(self, data):
        self.writes.append(data)
        return len(data)
    def flush(self):
        self.process.reply(self.process, self.writes[-1])
    def close(self):
        self.process.stdout.q.put(b"")


class Process:
    def __init__(self, reply):
        self.reply = reply
        self.stdout = Pipe()
        self.stdin = Input(self)
    def wait(self, timeout):
        return 0


def response(process, data):
    # Requests must remain a single physical shell input line, including printf formats.
    assert data.count(b"\n") == 1
    marker = re.search(rb"(pcfps_[a-f0-9]+_[0-9]+)_begin", data)[1]
    for line in [marker+b"_begin\r\n", b"16666666\r\n", b"1 2 3\r\n", b"\r\n", marker+b"_end:0\r\n"]:
        process.stdout.q.put(line)


class TestFpsShell(unittest.TestCase):
    def channel(self, reply=response):
        process = Process(reply)
        patcher = patch("adb_fps.subprocess.Popen", return_value=process)
        factory = patcher.start()
        self.addCleanup(patcher.stop)
        shell = FpsShell(["adb", "-s", "serial"], timeout=.05)
        self.addCleanup(shell.close)
        return shell, process, factory

    def test_two_requests_reuse_process_and_preserve_response(self):
        shell, process, factory = self.channel()
        first = shell.query(ARGS)
        second = shell.query(ARGS)
        self.assertEqual(first, "16666666\r\n1 2 3\r\n\r\n")
        self.assertEqual(first, second)
        factory.assert_called_once()
        self.assertIn(b"_1_begin", process.stdin.writes[0])
        self.assertIn(b"_2_begin", process.stdin.writes[1])
        shell.close()
        shell.close()
        self.assertFalse(shell.reader.is_alive())
        self.assertTrue(process.stdout.closed)

    def test_layer_is_shell_quoted_and_control_characters_rejected(self):
        shell, process, _ = self.channel()
        shell.query(ARGS[:-1]+["SurfaceView[game/'$(echo surprise)]#1"])
        self.assertIn(bytes([39, 34, 39, 34, 39]), process.stdin.writes[-1])
        with self.assertRaises(FpsShellError):
            shell.query(ARGS[:-1]+["bad\nlayer"])
        with self.assertRaises(ValueError):
            shell.query(["rm", "something"])

    def test_timeout_never_returns_partial_response(self):
        shell, _, _ = self.channel(lambda process, data: None)
        with self.assertRaisesRegex(FpsShellError, "timeout"):
            shell.query(ARGS)

    def test_wrong_response_identity_is_rejected(self):
        shell, _, _ = self.channel(lambda p, data: p.stdout.q.put(b"wrong_begin\n"))
        with self.assertRaisesRegex(FpsShellError, "identity"):
            shell.query(ARGS)

    def test_nonzero_exit_and_oversize_responses_are_rejected(self):
        def nonzero(p, data):
            marker = re.search(rb"(pcfps_[a-f0-9]+_[0-9]+)_begin", data)[1]
            p.stdout.q.put(marker+b"_begin\n")
            p.stdout.q.put(marker+b"_end:1\n")
        shell, _, _ = self.channel(nonzero)
        with self.assertRaisesRegex(FpsShellError, "command failed"):
            shell.query(ARGS)
        shell.close()
        shell, _, _ = self.channel(lambda p,data:p.stdout.q.put(b"x"*(FpsShell.MAX_LINE+1)))
        with self.assertRaises(FpsShellError):
            shell.query(ARGS)

    def test_transport_failure_falls_back_once_without_respawning(self):
        adb = Adb.__new__(Adb)
        adb._base = ["adb"]
        adb.shell = Mock(return_value="legacy result")
        channel = Mock()
        channel.query.side_effect = FpsShellError("broken")
        with patch("adb_fps.FpsShell", return_value=channel) as factory:
            self.assertEqual(adb.fps_shell(ARGS), "legacy result")
            self.assertEqual(adb.fps_shell(ARGS), "legacy result")
        factory.assert_called_once()
        channel.close.assert_called_once()
        self.assertEqual(adb.shell.call_count, 2)
        self.assertEqual(adb.fps_query_transport, "oneshot")
        self.assertEqual(adb.fps_query_fallback_reason, "broken")

    def test_oneshot_mode_never_opens_stream(self):
        adb = Adb.__new__(Adb)
        adb.fps_query_mode = "oneshot"
        adb.shell = Mock(return_value="legacy")
        with patch("adb_fps.FpsShell") as factory:
            self.assertEqual(adb.fps_shell(ARGS), "legacy")
        factory.assert_not_called()

    def test_fps_values_and_raw_frames_match_oneshot_for_same_buffer(self):
        from metrics.fps import FpsCollector
        layer = ARGS[-1]
        stamps = [1_000_000_000 + i*16_666_666 for i in range(8)]
        latency = "16666666\n" + "".join(f"{t} {t} {t}\n" for t in stamps)
        def reply(process, data):
            marker = re.search(rb"(pcfps_[a-f0-9]+_[0-9]+)_begin", data)[1]
            payload = marker+b"_begin\n"+latency.encode()+b"\n"+marker+b"_end:0\n"
            for line in payload.splitlines(keepends=True):
                process.stdout.q.put(line)
        shell, process, _ = self.channel(reply)
        legacy = type("Legacy", (), {"shell": lambda self,args: layer+"\n" if "--list" in args else latency})()
        modern = Adb.__new__(Adb)
        modern._fps_channel = shell
        modern.shell = legacy.shell
        old, new = FpsCollector(legacy, "game", ""), FpsCollector(modern, "game", "")
        for ts in [1.0, 1.5]:
            before, after = old.sample(ts), new.sample(ts)
            self.assertEqual(after.pop("query_transport"), "persistent")
            before.pop("query_transport")
            self.assertEqual(before, after)

    def test_fps_worker_releases_channel_on_stop_and_failure(self):
        for fails in [False, True]:
            with self.subTest(fails=fails):
                session = Mock()
                session.is_stopping = False
                sampler = Mock()
                target = Mock()
                target.capture_sampler.return_value = (sampler, 0)
                def once(*args):
                    session.is_stopping = True
                    if fails:
                        raise RuntimeError("worker failed")
                    return {"published":True}
                scheduler = SamplerScheduler(session, target, object(), {"fps": .5}, sample_once=once)
                scheduler._run_metric("fps", .5)
                sampler.adb.close_fps_shell.assert_called_once()
                if fails:
                    session.request_stop.assert_called_once()
