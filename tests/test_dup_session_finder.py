"""Regression tests for src/dup_session_finder.py (pure logic: detecting
duplicate recording sessions from orphaned ffmpeg processes -- see
AGENT.md/CLAUDE.md). Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, unittest
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
dup = load("dup_session_finder")


class ParseSegmentFilenameTests(unittest.TestCase):
    def test_indexed_segment(self):
        p = dup.parse_segment_filename("京圈太子_2026-07-26_13-05-21_000.ts")
        self.assertEqual(p.prefix, "京圈太子")
        self.assertEqual(p.start, datetime(2026, 7, 26, 13, 5, 21))
        self.assertEqual(p.index, 0)
        self.assertEqual(p.ext, "ts")

    def test_indexed_segment_with_title(self):
        p = dup.parse_segment_filename("小美_生日直播_2026-07-30_14-52-34_012.ts")
        self.assertEqual(p.prefix, "小美_生日直播")
        self.assertEqual(p.index, 12)

    def test_non_indexed_single_file(self):
        p = dup.parse_segment_filename("小美_2026-07-30_14-52-34.mp4")
        self.assertEqual(p.prefix, "小美")
        self.assertIsNone(p.index)
        self.assertEqual(p.ext, "mp4")

    def test_unrelated_file_returns_none(self):
        self.assertIsNone(dup.parse_segment_filename("readme.txt"))

    def test_no_prefix_returns_none(self):
        self.assertIsNone(dup.parse_segment_filename("2026-07-30_14-52-34_000.ts"))


def _seg(path, prefix, start, index, size):
    return dup.Segment(path=path, prefix=prefix, start=start, index=index, size=size)


class BuildSessionsTests(unittest.TestCase):
    def test_groups_by_prefix_and_start(self):
        t1 = datetime(2026, 7, 26, 13, 5, 21)
        t2 = datetime(2026, 7, 26, 13, 6, 30)
        segs = [
            _seg("a/000.ts", "小美", t1, 0, 100),
            _seg("a/001.ts", "小美", t1, 1, 200),
            _seg("b/000.ts", "小美", t2, 0, 100),
        ]
        sessions = dup.build_sessions(segs)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].start, t1)
        self.assertEqual(len(sessions[0].segments), 2)
        self.assertEqual(sessions[1].start, t2)
        self.assertEqual(len(sessions[1].segments), 1)


class FindDuplicateCandidatesTests(unittest.TestCase):
    def _matching_pair(self, gap_seconds=69, n_segments=12, size_diff_ratio=0.0):
        t1 = datetime(2026, 7, 26, 13, 5, 21)
        t2 = t1 + timedelta(seconds=gap_seconds)
        sizes = [1000 * (i + 1) for i in range(n_segments)]
        segs = []
        for i, size in enumerate(sizes):
            segs.append(_seg(f"a/{i:03d}.ts", "京圈太子", t1, i, size))
            segs.append(_seg(f"b/{i:03d}.ts", "京圈太子", t2, i, int(size * (1 + size_diff_ratio))))
        return dup.build_sessions(segs)

    def test_flags_close_start_matching_sizes(self):
        sessions = self._matching_pair(gap_seconds=69)
        candidates = dup.find_duplicate_candidates(sessions)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertAlmostEqual(c.gap_seconds, 69)
        self.assertEqual(c.matched_indices, c.compared_indices)
        self.assertEqual(c.compared_indices, 12)

    def test_does_not_flag_when_gap_too_large(self):
        sessions = self._matching_pair(gap_seconds=600)
        candidates = dup.find_duplicate_candidates(sessions, max_gap_seconds=90)
        self.assertEqual(candidates, [])

    def test_does_not_flag_when_sizes_diverge(self):
        # a genuine reconnect: same anchor, close start, but sizes don't line up
        sessions = self._matching_pair(gap_seconds=30, size_diff_ratio=0.5)
        candidates = dup.find_duplicate_candidates(sessions, size_tolerance=0.02)
        self.assertEqual(candidates, [])

    def test_does_not_flag_different_anchors(self):
        t1 = datetime(2026, 7, 26, 13, 5, 21)
        t2 = datetime(2026, 7, 26, 13, 6, 0)
        segs = [
            _seg(f"a/{i:03d}.ts", "主播甲", t1, i, 1000) for i in range(4)
        ] + [
            _seg(f"b/{i:03d}.ts", "主播乙", t2, i, 1000) for i in range(4)
        ]
        sessions = dup.build_sessions(segs)
        candidates = dup.find_duplicate_candidates(sessions)
        self.assertEqual(candidates, [])

    def test_ignores_pair_with_too_few_overlapping_segments(self):
        t1 = datetime(2026, 7, 26, 13, 5, 21)
        t2 = datetime(2026, 7, 26, 13, 5, 40)
        segs = [
            _seg("a/000.ts", "小美", t1, 0, 1000),
            _seg("b/000.ts", "小美", t2, 0, 1000),
        ]
        sessions = dup.build_sessions(segs)
        candidates = dup.find_duplicate_candidates(sessions, min_compared_indices=3)
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
