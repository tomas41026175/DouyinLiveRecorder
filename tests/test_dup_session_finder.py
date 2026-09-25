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


def _session(prefix, start, sizes, dirname="d"):
    segs = tuple(_seg(f"{dirname}/{i:03d}.ts", prefix, start, i, size) for i, size in enumerate(sizes))
    return dup.Session(prefix=prefix, start=start, segments=segs)


class ClusterDuplicateSessionsTests(unittest.TestCase):
    def test_transitive_chain_forms_one_cluster(self):
        # A~B and B~C are each within max_gap, but A~C is not (checked
        # separately) -- clustering must still put all three together.
        t_a = datetime(2026, 7, 26, 13, 0, 0)
        t_b = t_a + timedelta(seconds=80)
        t_c = t_b + timedelta(seconds=80)
        sizes = [1000, 2000, 3000]
        a = _session("小美", t_a, sizes, "a")
        b = _session("小美", t_b, sizes, "b")
        c = _session("小美", t_c, sizes, "c")
        candidates = dup.find_duplicate_candidates([a, b, c], max_gap_seconds=90)
        # A-C gap is 160s, so only A-B and B-C should be flagged directly.
        pairs = {frozenset((cand.session_a.start, cand.session_b.start)) for cand in candidates}
        self.assertEqual(pairs, {frozenset((t_a, t_b)), frozenset((t_b, t_c))})

        clusters = dup.cluster_duplicate_sessions(candidates)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(set(clusters[0]), {a, b, c})

    def test_independent_pairs_form_separate_clusters(self):
        t1 = datetime(2026, 7, 26, 13, 0, 0)
        t2 = t1 + timedelta(seconds=30)
        t3 = datetime(2026, 7, 28, 9, 0, 0)
        t4 = t3 + timedelta(seconds=30)
        sizes = [1000, 2000, 3000]
        a = _session("小美", t1, sizes, "a")
        b = _session("小美", t2, sizes, "b")
        c = _session("小美", t3, sizes, "c")
        d = _session("小美", t4, sizes, "d")
        candidates = dup.find_duplicate_candidates([a, b, c, d])
        clusters = dup.cluster_duplicate_sessions(candidates)
        self.assertEqual(len(clusters), 2)
        cluster_sets = {frozenset(cl) for cl in clusters}
        self.assertEqual(cluster_sets, {frozenset({a, b}), frozenset({c, d})})


class PlanQuarantineTests(unittest.TestCase):
    def test_keep_largest_by_default(self):
        t1 = datetime(2026, 7, 26, 13, 0, 0)
        t2 = t1 + timedelta(seconds=30)
        small = _session("小美", t1, [1000, 1000, 1000], "small")
        # started later but ran longer / more complete -> should be kept
        large = _session("小美", t2, [1000, 1000, 1000, 5000], "large")
        candidates = dup.find_duplicate_candidates([small, large], min_compared_indices=3)
        self.assertEqual(len(candidates), 1)

        plans = dup.plan_quarantine(candidates, keep="largest")
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].keep, large)
        self.assertEqual(plans[0].remove, (small,))

    def test_keep_earliest(self):
        t1 = datetime(2026, 7, 26, 13, 0, 0)
        t2 = t1 + timedelta(seconds=30)
        first = _session("小美", t1, [1000, 1000, 1000], "first")
        second = _session("小美", t2, [1000, 1000, 1000, 5000], "second")
        candidates = dup.find_duplicate_candidates([first, second], min_compared_indices=3)

        plans = dup.plan_quarantine(candidates, keep="earliest")
        self.assertEqual(plans[0].keep, first)
        self.assertEqual(plans[0].remove, (second,))

    def test_cluster_of_three_keeps_one_removes_two(self):
        t_a = datetime(2026, 7, 26, 13, 0, 0)
        t_b = t_a + timedelta(seconds=60)
        t_c = t_b + timedelta(seconds=60)
        a = _session("小美", t_a, [1000, 1000, 1000], "a")
        b = _session("小美", t_b, [1000, 1000, 1000], "b")
        c = _session("小美", t_c, [1000, 1000, 1000, 9000], "c")
        candidates = dup.find_duplicate_candidates([a, b, c], min_compared_indices=3)

        plans = dup.plan_quarantine(candidates, keep="largest")
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].keep, c)
        self.assertEqual(set(plans[0].remove), {a, b})

    def test_invalid_keep_raises(self):
        with self.assertRaises(ValueError):
            dup.plan_quarantine([], keep="biggest")


if __name__ == "__main__":
    unittest.main()
