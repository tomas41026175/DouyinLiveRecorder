"""Tests for src/disk_share.py (per-streamer disk share / daily rate / limit what-if).
Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, unittest
from datetime import datetime
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
ds = load("disk_share")

GB = 1024 ** 3
NOW = datetime(2026, 9, 24, 12, 0, 0)


def f(name, size, mtime=None):
    return {"path": "/x/" + name, "size": size, "mtime": mtime}


class FileDayTests(unittest.TestCase):
    def test_name_stamp_wins_over_mtime(self):
        t = ds.file_day("a_2026-09-20_13-06-20_002.mp4", NOW.timestamp())
        self.assertEqual(t, datetime(2026, 9, 20, 13, 6, 20))

    def test_hevc_suffix_still_parsed(self):
        t = ds.file_day("a_2026-04-18_18-58-13_001_hevc.mp4", None)
        self.assertEqual(t.date().isoformat(), "2026-04-18")

    def test_falls_back_to_mtime(self):
        t = ds.file_day("noname.mp4", NOW.timestamp())
        self.assertEqual(t, NOW)

    def test_none_when_nothing(self):
        self.assertIsNone(ds.file_day("noname.mp4", None))


class RecentBytesTests(unittest.TestCase):
    def test_old_file_rewritten_recently_not_counted(self):
        # recorded in April, compressed (mtime rewritten) yesterday
        files = [f("a_2026-04-18_18-58-13_001_hevc.mp4", GB, NOW.timestamp())]
        self.assertEqual(ds.recent_bytes(files, NOW, 7), 0)

    def test_inside_window_counted(self):
        files = [f("a_2026-09-22_07-17-22_000.mp4", GB), f("a_2026-09-01_07-17-22_000.mp4", GB)]
        self.assertEqual(ds.recent_bytes(files, NOW, 7), GB)


class ComputeUsageTests(unittest.TestCase):
    def setUp(self):
        self.streamers = [
            {"url": "u1", "files": [f("a_2026-09-23_09-00-00_000.mp4", 6 * GB),
                                     f("a_2026-01-01_09-00-00_000.mp4", 4 * GB)]},
            {"url": "u2", "files": [f("b_2026-09-23_09-00-00_000.mp4", 2 * GB)]},
        ]
        self.sessions = [{"url": "u1", "duration_sec": 3600}, {"url": "u1", "duration_sec": 7200},
                         {"url": "u2", "duration_sec": 1800}]

    def test_shares_sum_to_100(self):
        r = ds.compute_usage(self.streamers, self.sessions, NOW, 7)
        self.assertEqual(r["total_bytes"], 12 * GB)
        self.assertAlmostEqual(r["per_url"]["u1"]["share_pct"], 83.3, places=1)
        self.assertAlmostEqual(r["per_url"]["u2"]["share_pct"], 16.7, places=1)
        self.assertAlmostEqual(r["per_url"]["u1"]["recent_share_pct"], 75.0, places=1)

    def test_rates(self):
        r = ds.compute_usage(self.streamers, self.sessions, NOW, 7)
        u1 = r["per_url"]["u1"]
        self.assertAlmostEqual(u1["bytes_per_day"], 6 * GB / 7)
        self.assertAlmostEqual(u1["sessions_per_day"], 2 / 7)
        self.assertAlmostEqual(u1["bytes_per_min"], 6 * GB / 180)
        self.assertAlmostEqual(r["total_bytes_per_day"], 8 * GB / 7)

    def test_no_sessions_means_no_bytes_per_min(self):
        r = ds.compute_usage(self.streamers, [], NOW, 7)
        self.assertIsNone(r["per_url"]["u1"]["bytes_per_min"])

    def test_empty_everything_no_crash(self):
        r = ds.compute_usage([], [], NOW, 7)
        self.assertEqual(r["total_bytes"], 0)
        self.assertEqual(r["per_url"], {})


class EstimateTests(unittest.TestCase):
    def test_cap_reduces_long_sessions_only(self):
        durs = [3600, 7200]            # 60 min, 120 min
        bpm = GB / 60.0               # 1 GB per hour
        unlimited = ds.estimate_daily_bytes_with_limit(durs, bpm, 7, None)
        capped = ds.estimate_daily_bytes_with_limit(durs, bpm, 7, 60)
        self.assertAlmostEqual(unlimited, 3 * GB / 7)
        self.assertAlmostEqual(capped, 2 * GB / 7)

    def test_zero_limit_means_unlimited(self):
        self.assertAlmostEqual(
            ds.estimate_daily_bytes_with_limit([3600], GB / 60.0, 7, 0), GB / 7)

    def test_none_without_rate(self):
        self.assertIsNone(ds.estimate_daily_bytes_with_limit([3600], None, 7, 30))


if __name__ == "__main__":
    unittest.main()
