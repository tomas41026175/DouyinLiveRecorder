"""Regression tests for src/disk_history.py (pure logic: daily disk-space
history + linear-trend low-space ETA). Run from repo root:
python -m unittest discover -s tests -v"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
dh = load("disk_history")

GB = 1024 ** 3


class RecordSampleTests(unittest.TestCase):
    def test_adds_first_sample(self):
        out = dh.record_sample([], "2026-01-01", 10 * GB, 100 * GB)
        self.assertEqual(out, [{"date": "2026-01-01", "free_bytes": 10 * GB, "total_bytes": 100 * GB}])

    def test_replaces_same_day_instead_of_duplicating(self):
        h = dh.record_sample([], "2026-01-01", 10 * GB, 100 * GB)
        h = dh.record_sample(h, "2026-01-01", 9 * GB, 100 * GB)
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["free_bytes"], 9 * GB)

    def test_sorted_by_date(self):
        h = dh.record_sample([], "2026-01-03", 3 * GB, None)
        h = dh.record_sample(h, "2026-01-01", 1 * GB, None)
        h = dh.record_sample(h, "2026-01-02", 2 * GB, None)
        self.assertEqual([s["date"] for s in h], ["2026-01-01", "2026-01-02", "2026-01-03"])

    def test_capped_to_max_days(self):
        h = []
        for i in range(1, 11):
            h = dh.record_sample(h, f"2026-01-{i:02d}", i * GB, None, max_days=5)
        self.assertEqual(len(h), 5)
        self.assertEqual([s["date"] for s in h],
                         ["2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09", "2026-01-10"])

    def test_none_free_bytes_is_noop(self):
        h = dh.record_sample([{"date": "2026-01-01", "free_bytes": 1, "total_bytes": None}],
                             "2026-01-02", None, None)
        self.assertEqual(len(h), 1)


def _points(*pairs):
    """pairs: (date_str, free_gb) -> history list."""
    return [{"date": d, "free_bytes": int(g * GB), "total_bytes": 100 * GB} for d, g in pairs]


class EstimateLowSpaceEtaTests(unittest.TestCase):
    def test_no_critical_threshold_configured(self):
        r = dh.estimate_low_space_eta(_points(("2026-01-01", 10), ("2026-01-02", 9), ("2026-01-03", 8)), None)
        self.assertFalse(r["available"])
        self.assertIn("門檻", r["reason"])

    def test_insufficient_history(self):
        r = dh.estimate_low_space_eta(_points(("2026-01-01", 10), ("2026-01-02", 9)), 2 * GB, min_points=3)
        self.assertFalse(r["available"])
        self.assertIn("資料不足", r["reason"])

    def test_steady_shrink_gives_eta(self):
        # loses exactly 1GB/day, starts at 10GB, critical at 2GB -> 8 more days
        pts = _points(("2026-01-01", 10), ("2026-01-02", 9), ("2026-01-03", 8), ("2026-01-04", 7))
        r = dh.estimate_low_space_eta(pts, 2 * GB, min_points=3)
        self.assertTrue(r["available"])
        self.assertAlmostEqual(r["days_left"], 5.0, delta=0.2)
        self.assertEqual(r["eta_date"], "2026-01-09")
        self.assertLess(r["daily_change_bytes"], 0)

    def test_already_below_critical(self):
        pts = _points(("2026-01-01", 5), ("2026-01-02", 3), ("2026-01-03", 1))
        r = dh.estimate_low_space_eta(pts, 2 * GB, min_points=3)
        self.assertTrue(r["available"])
        self.assertEqual(r["days_left"], 0.0)
        self.assertEqual(r["eta_date"], "2026-01-03")
        self.assertEqual(r["reason"], "已低於告急門檻")

    def test_stable_usage_has_no_eta(self):
        pts = _points(("2026-01-01", 10), ("2026-01-02", 10), ("2026-01-03", 10))
        r = dh.estimate_low_space_eta(pts, 2 * GB, min_points=3)
        self.assertFalse(r["available"])
        self.assertIn("沒有下降趨勢", r["reason"])

    def test_growing_free_space_has_no_eta(self):
        # user cleaned up disk / added a drive -- free space trending UP
        pts = _points(("2026-01-01", 5), ("2026-01-02", 8), ("2026-01-03", 12))
        r = dh.estimate_low_space_eta(pts, 2 * GB, min_points=3)
        self.assertFalse(r["available"])
        self.assertGreater(r["daily_change_bytes"], 0)

    def test_lookback_window_ignores_older_points(self):
        # Old block shrinks fast; recent block is flat. A short lookback should
        # see only the flat recent block and report no trend.
        pts = _points(
            ("2025-12-01", 50), ("2025-12-02", 40), ("2025-12-03", 30),
            ("2026-01-01", 10), ("2026-01-02", 10), ("2026-01-03", 10),
        )
        r = dh.estimate_low_space_eta(pts, 2 * GB, lookback_days=3, min_points=3)
        self.assertFalse(r["available"])
        self.assertIn("沒有下降趨勢", r["reason"])


if __name__ == "__main__":
    unittest.main()
