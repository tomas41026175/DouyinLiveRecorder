"""Regression tests for src/adhoc_record.py（工作檯看直播的臨時錄製）。
Run from repo root: python -m unittest discover -s tests -v"""
import os, sys, unittest
from datetime import datetime
from pathlib import Path
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
ar = load("adhoc_record")


class FakeProc:
    def __init__(self):
        self.code = None
    def poll(self):
        return self.code


class PureTests(unittest.TestCase):
    def test_best_record_url_prefers_origin_flv_https(self):
        sd = {"hd": {"main": {"flv": "http://x/hd.flv", "hls": "http://x/hd.m3u8"}},
              "origin": {"main": {"flv": "http://x/or4.flv"}}}
        self.assertEqual(ar.best_record_url({}, sd), {"url": "https://x/or4.flv", "quality": "origin", "kind": "flv"})

    def test_best_record_url_falls_back(self):
        self.assertEqual(ar.best_record_url({}, {"ld": {"main": {"hls": "http://x/ld.m3u8"}}})["kind"], "hls")
        room = {"stream_url": {"flv_pull_url": {"HD1": "http://x/hd.flv"}}}
        self.assertEqual(ar.best_record_url(room, {})["url"], "https://x/hd.flv")
        self.assertIsNone(ar.best_record_url({}, {"ao": {"main": {"flv": "http://x/ao.flv"}}}))

    def test_safe_name_and_output(self):
        self.assertEqual(ar.safe_name('a/b:c*?"<>| '), "a_b_c______")
        self.assertEqual(ar.safe_name("  .. "), "未命名")
        out = ar.plan_output(Path("D:/rec"), "小甜🎤港澳", datetime(2026, 11, 6, 8, 5, 9))
        self.assertEqual(out.parts[-3:], ("手動錄製", "小甜🎤港澳", "小甜🎤港澳_2026-11-06_08-05-09.ts"))

    def test_cmds(self):
        c = ar.ffmpeg_cmd("ffmpeg", "https://x/a.flv", Path("o.ts"), 60)
        self.assertEqual(c[c.index("-i") + 1], "https://x/a.flv")
        self.assertEqual(c[c.index("-t") + 1], "60")
        self.assertIn("copy", c)
        self.assertEqual(ar.remux_cmd("ffmpeg", Path("d/o.ts"))[-1], str(Path("d/o.mp4")))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.procs, self.stopped = [], []
        def spawn(cmd):
            p = FakeProc(); self.procs.append(p); return p
        self.reg = ar.AdhocRegistry(spawn, lambda p: self.stopped.append(p), clock=lambda: self.now, max_active=2, timeout=30)

    def start(self, url):
        return self.reg.start(url, "A", ["ffmpeg"], Path(f"{url[-1]}.ts"))

    def test_start_heartbeat_stop(self):
        r = self.start("https://live.douyin.com/1")
        self.assertEqual(r["state"], "recording")
        self.now += 20
        self.assertEqual(self.reg.heartbeat(r["id"])["elapsedSec"], 20)
        s = self.reg.stop(r["id"])
        self.assertEqual((s["state"], s["reason"]), ("ended", "stopped"))
        self.assertEqual(self.stopped, [self.procs[0]])
        self.assertIsNone(self.reg.heartbeat("nope"))

    def test_same_url_reuses_and_limit(self):
        a = self.start("https://live.douyin.com/1")
        self.assertEqual(self.start("https://live.douyin.com/1")["id"], a["id"])
        self.assertEqual(len(self.procs), 1)
        self.start("https://live.douyin.com/2")
        with self.assertRaises(ar.LimitError):
            self.start("https://live.douyin.com/3")

    def test_no_heartbeat_stops_left(self):
        r = self.start("https://live.douyin.com/1")
        self.now += 25
        self.reg.heartbeat(r["id"])
        self.now += 25
        self.assertEqual(self.reg.reap(), [])
        self.now += 10
        ended = self.reg.reap()
        self.assertEqual([(e["id"], e["reason"]) for e in ended], [(r["id"], "left")])
        self.assertEqual(self.stopped, [self.procs[0]])

    def test_ffmpeg_exit_is_stream_ended(self):
        r = self.start("https://live.douyin.com/1")
        self.procs[0].code = 1
        self.assertEqual(self.reg.heartbeat(r["id"])["reason"], "stream_ended")
        self.assertEqual(self.stopped, [])

    def test_drain_finished_once_then_forget(self):
        r = self.start("https://live.douyin.com/1")
        self.reg.stop(r["id"])
        self.assertEqual([d["id"] for d in self.reg.drain_finished()], [r["id"]])
        self.assertEqual(self.reg.drain_finished(), [])
        self.now += 601
        self.reg.drain_finished()
        self.assertIsNone(self.reg.heartbeat(r["id"]))


if __name__ == "__main__":
    unittest.main()
