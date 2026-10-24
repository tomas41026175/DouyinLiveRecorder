"""Tests for src/streamer_stats.py (per-URL totals + last_recorded_at).
Run from repo root: python -m unittest discover -s tests -v"""
import os
import sqlite3
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
ss = load("streamer_stats")

# Same columns duration_tracker.py creates (only the ones the query touches).
SCHEMA = (
    "CREATE TABLE recording_sessions ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " anchor_name TEXT, live_url TEXT, start_time TEXT NOT NULL,"
    " end_time TEXT, duration_sec INTEGER)"
)


def _db(rows):
    con = sqlite3.connect(":memory:")
    con.execute(SCHEMA)
    con.executemany(
        "INSERT INTO recording_sessions (live_url, start_time, end_time, duration_sec)"
        " VALUES (?, ?, ?, ?)", rows)
    return con


def _totals(rows):
    con = _db(rows)
    try:
        return ss.build_url_totals(con.execute(ss.PER_URL_TOTALS_SQL).fetchall())
    finally:
        con.close()


class NormalizeIsoTests(unittest.TestCase):
    def test_stored_format_roundtrips(self):
        self.assertEqual(ss.normalize_iso("2026-10-24T14:30:05"), "2026-10-24T14:30:05")

    def test_space_separator_and_fraction(self):
        self.assertEqual(ss.normalize_iso("2026-10-24 14:30:05.123456"),
                         "2026-10-24T14:30:05")

    def test_datetime_object(self):
        self.assertEqual(ss.normalize_iso(datetime(2026, 1, 2, 3, 4, 5)),
                         "2026-01-02T03:04:05")

    def test_empty_and_none(self):
        self.assertIsNone(ss.normalize_iso(None))
        self.assertIsNone(ss.normalize_iso(""))
        self.assertIsNone(ss.normalize_iso("   "))

    def test_garbage_is_none(self):
        self.assertIsNone(ss.normalize_iso("not a date"))


class BuildUrlTotalsTests(unittest.TestCase):
    def test_basic_row(self):
        out = ss.build_url_totals([("u1", 120, 2, "2026-10-24T10:00:00")])
        self.assertEqual(out, {"u1": (120, 2, "2026-10-24T10:00:00")})

    def test_null_aggregates(self):
        out = ss.build_url_totals([("u1", None, None, None)])
        self.assertEqual(out, {"u1": (0, 0, None)})

    def test_skips_null_url(self):
        self.assertEqual(ss.build_url_totals([(None, 5, 1, "2026-10-24T10:00:00")]), {})

    def test_short_legacy_row(self):
        # Old 3-column shape still yields a 3-tuple with last=None.
        self.assertEqual(ss.build_url_totals([("u1", 7, 1)]), {"u1": (7, 1, None)})


class PerUrlTotalsSqlTests(unittest.TestCase):
    def test_finished_only_counts_and_duration(self):
        out = _totals([
            ("u1", "2026-10-20T10:00:00", "2026-10-20T11:00:00", 3600),
            ("u1", "2026-10-22T10:00:00", "2026-10-22T10:30:00", 1800),
        ])
        self.assertEqual(out["u1"], (5400, 2, "2026-10-22T10:00:00"))

    def test_in_progress_session_is_last_but_not_counted(self):
        out = _totals([
            ("u1", "2026-10-20T10:00:00", "2026-10-20T11:00:00", 3600),
            # Recording right now: end_time NULL, duration not yet known.
            ("u1", "2026-10-24T14:30:00", None, None),
        ])
        self.assertEqual(out["u1"], (3600, 1, "2026-10-24T14:30:00"))

    def test_only_in_progress_session(self):
        out = _totals([("u2", "2026-10-24T14:30:00", None, None)])
        self.assertEqual(out["u2"], (0, 0, "2026-10-24T14:30:00"))

    def test_stale_duration_on_unfinished_row_ignored(self):
        # A NULL-end row that somehow carries a duration must not be summed.
        out = _totals([("u3", "2026-10-24T14:30:00", None, 999)])
        self.assertEqual(out["u3"], (0, 0, "2026-10-24T14:30:00"))

    def test_multiple_urls_independent(self):
        out = _totals([
            ("a", "2026-10-01T09:00:00", "2026-10-01T10:00:00", 3600),
            ("b", "2026-10-05T09:00:00", "2026-10-05T09:10:00", 600),
            ("a", "2026-09-30T09:00:00", "2026-09-30T09:01:00", 60),
        ])
        self.assertEqual(out["a"], (3660, 2, "2026-10-01T09:00:00"))
        self.assertEqual(out["b"], (600, 1, "2026-10-05T09:00:00"))

    def test_unknown_url_missing(self):
        self.assertEqual(_totals([]), {})


if __name__ == "__main__":
    unittest.main()
