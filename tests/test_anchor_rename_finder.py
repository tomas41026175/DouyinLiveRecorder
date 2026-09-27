"""Regression tests for src/anchor_rename_finder.py (pure logic: detecting an
anchor's display name changing over time for the same recording URL -- see
AGENT.md). Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, unittest
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
arf = load("anchor_rename_finder")


def _rec(url, anchor, start, platform="抖音直播"):
    return arf.SessionRecord(url=url, anchor=anchor, start_time=start, platform=platform)


class FindRenameEventsTests(unittest.TestCase):
    def test_simple_rename_detected(self):
        t0 = datetime(2026, 1, 1)
        records = [
            _rec("https://live.douyin.com/1", "序号3 舊名字", t0),
            _rec("https://live.douyin.com/1", "序号3 舊名字", t0 + timedelta(days=1)),
            _rec("https://live.douyin.com/1", "序号3 新名字", t0 + timedelta(days=5)),
        ]
        events = arf.find_rename_events(records)
        self.assertEqual(len(events), 1)
        e = events[0]
        self.assertEqual(e.url, "https://live.douyin.com/1")
        self.assertEqual(e.old_name, "舊名字")
        self.assertEqual(e.new_name, "新名字")
        self.assertEqual(e.old_last_seen, t0 + timedelta(days=1))
        self.assertEqual(e.new_first_seen, t0 + timedelta(days=5))
        self.assertEqual(e.platform, "抖音直播")

    def test_no_rename_when_single_name(self):
        t0 = datetime(2026, 1, 1)
        records = [
            _rec("https://live.douyin.com/1", "序号3 名字", t0),
            _rec("https://live.douyin.com/1", "序号5 名字", t0 + timedelta(days=1)),
        ]
        # "序号N " prefix differs but canonical name is identical -> no event
        events = arf.find_rename_events(records)
        self.assertEqual(events, [])

    def test_multi_hop_rename_consolidates_into_latest(self):
        t0 = datetime(2026, 1, 1)
        records = [
            _rec("u1", "序号1 A", t0),
            _rec("u1", "序号1 B", t0 + timedelta(days=2)),
            _rec("u1", "序号1 C", t0 + timedelta(days=4)),
        ]
        events = arf.find_rename_events(records)
        names = {(e.old_name, e.new_name) for e in events}
        self.assertEqual(names, {("A", "C"), ("B", "C")})

    def test_flip_flop_still_merges_into_currently_active_name(self):
        t0 = datetime(2026, 1, 1)
        records = [
            _rec("u1", "A", t0),
            _rec("u1", "B", t0 + timedelta(days=1)),
            _rec("u1", "A", t0 + timedelta(days=2)),  # flipped back, and it's the latest
        ]
        events = arf.find_rename_events(records)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].old_name, "B")
        self.assertEqual(events[0].new_name, "A")

    def test_independent_urls_do_not_cross_contaminate(self):
        t0 = datetime(2026, 1, 1)
        records = [
            _rec("u1", "序号1 甲舊名", t0),
            _rec("u1", "序号1 甲新名", t0 + timedelta(days=1)),
            _rec("u2", "序号2 乙名字", t0),
            _rec("u2", "序号2 乙名字", t0 + timedelta(days=1)),
        ]
        events = arf.find_rename_events(records)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].url, "u1")

    def test_empty_canonical_name_skipped(self):
        t0 = datetime(2026, 1, 1)
        records = [_rec("u1", "", t0), _rec("u1", "   ", t0 + timedelta(days=1))]
        self.assertEqual(arf.find_rename_events(records), [])

    def test_records_without_url_ignored(self):
        t0 = datetime(2026, 1, 1)
        records = [_rec("", "序号1 名字", t0)]
        self.assertEqual(arf.find_rename_events(records), [])


if __name__ == "__main__":
    unittest.main()
