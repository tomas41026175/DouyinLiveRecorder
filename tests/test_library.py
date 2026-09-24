"""Regression tests for src/library.py (pure logic: date grouping + file
resolver). Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
library = load("library")


class CanonicalAnchorNameTests(unittest.TestCase):
    def test_strips_seq_prefix(self):
        self.assertEqual(library.canonical_anchor_name("序号3 小美"), "小美")

    def test_strips_fullwidth_variant(self):
        self.assertEqual(library.canonical_anchor_name("序號12 阿明"), "阿明")

    def test_no_prefix_unchanged(self):
        self.assertEqual(library.canonical_anchor_name("小美"), "小美")

    def test_none_returns_empty(self):
        self.assertEqual(library.canonical_anchor_name(None), "")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(library.canonical_anchor_name("  小美  "), "小美")


class ResolveVideoTests(unittest.TestCase):
    def test_plain_path_when_exists(self):
        files = {"/r/a.mp4"}
        r = library.resolve_video("/r/a.mp4", exists=lambda p: p in files,
                                  glob_fn=lambda pat: [])
        self.assertEqual(r, "/r/a.mp4")

    def test_prefers_compressed_twin(self):
        files = {"/r/a.mp4", "/r/a_hevc.mp4"}
        r = library.resolve_video("/r/a.mp4", exists=lambda p: p in files,
                                  glob_fn=lambda pat: [])
        self.assertEqual(r, "/r/a_hevc.mp4")

    def test_format_conversion_ts_to_mp4(self):
        files = {"/r/a.mp4"}  # stored path was .ts, original converted+deleted
        r = library.resolve_video("/r/a.ts", exists=lambda p: p in files,
                                  glob_fn=lambda pat: [])
        self.assertEqual(r, "/r/a.mp4")

    def test_not_found_returns_none(self):
        r = library.resolve_video("/r/a.mp4", exists=lambda p: False,
                                  glob_fn=lambda pat: [])
        self.assertIsNone(r)

    def test_empty_stored_path_returns_none(self):
        self.assertIsNone(library.resolve_video("", exists=lambda p: True,
                                                 glob_fn=lambda pat: []))

    def test_segment_pattern_finds_first_real_segment(self):
        files = {"/r/a_000.mp4"}
        def fake_glob(pattern):
            if pattern.endswith(".mp4") and "_hevc" not in pattern:
                return sorted(files)
            return []
        r = library.resolve_video("/r/a_%03d.ts", exists=lambda p: p in files,
                                  glob_fn=fake_glob)
        self.assertEqual(r, "/r/a_000.mp4")

    def test_cache_hit_skips_glob(self):
        files = {"/r/a_002.mp4"}
        glob_calls = []
        def fake_glob(pattern):
            glob_calls.append(pattern)
            if pattern.endswith(".mp4") and "_hevc" not in pattern:
                return sorted(files)
            return []
        cache = {}
        stored = "/r/a_%03d.ts"
        r1 = library.resolve_video(stored, exists=lambda p: p in files,
                                   glob_fn=fake_glob, cache=cache)
        self.assertEqual(r1, "/r/a_002.mp4")
        n1 = len(glob_calls)
        self.assertGreater(n1, 0)
        r2 = library.resolve_video(stored, exists=lambda p: p in files,
                                   glob_fn=fake_glob, cache=cache)
        self.assertEqual(r2, "/r/a_002.mp4")
        self.assertEqual(len(glob_calls), n1, "cache hit must not re-glob")

    def test_cache_invalidates_when_cached_file_disappears(self):
        files = {"/r/a_002_hevc.mp4"}
        glob_calls = []
        def fake_glob(pattern):
            glob_calls.append(pattern)
            if pattern.endswith("_hevc.mp4"):
                return [p for p in files if p.endswith("_hevc.mp4")]
            if pattern.endswith(".mp4"):
                return [p for p in files if p.endswith(".mp4") and "_hevc" not in p]
            return []
        cache = {}
        stored = "/r/a_%03d.ts"
        r1 = library.resolve_video(stored, exists=lambda p: p in files,
                                   glob_fn=fake_glob, cache=cache)
        self.assertEqual(r1, "/r/a_002_hevc.mp4")
        files.clear(); files.add("/r/a_002.mp4")
        glob_calls.clear()
        r2 = library.resolve_video(stored, exists=lambda p: p in files,
                                   glob_fn=fake_glob, cache=cache)
        self.assertEqual(r2, "/r/a_002.mp4")
        self.assertGreater(len(glob_calls), 0, "must re-glob, not serve stale cache")
        self.assertEqual(cache[stored], "/r/a_002.mp4")

    def test_no_cache_arg_is_pure_no_side_effects(self):
        calls = []
        def fake_glob(pattern):
            calls.append(pattern)
            return []
        self.assertIsNone(library.resolve_video("/r/a.ts", exists=lambda p: False,
                                                 glob_fn=fake_glob))


class ResolveAllSegmentsTests(unittest.TestCase):
    def _fake_glob_factory(self, files):
        def fake_glob(pattern):
            if pattern.endswith(".mp4") and "_hevc" not in pattern:
                return sorted(f for f in files if f.endswith(".mp4") and "_hevc" not in f)
            if pattern.endswith("_hevc.mp4"):
                return sorted(f for f in files if f.endswith("_hevc.mp4"))
            return []
        return fake_glob

    def test_non_segment_path_returns_empty(self):
        self.assertEqual(library.resolve_all_segments("/r/a.mp4"), [])

    def test_finds_all_segments_sorted_by_index(self):
        files = {"/r/a_001.mp4", "/r/a_000.mp4", "/r/a_002.mp4"}
        segs = library.resolve_all_segments("/r/a_%03d.ts",
                                            exists=lambda p: p in files,
                                            glob_fn=self._fake_glob_factory(files))
        self.assertEqual(segs, [(0, "/r/a_000.mp4"), (1, "/r/a_001.mp4"), (2, "/r/a_002.mp4")])

    @unittest.expectedFailure
    def test_compressed_twin_preferred_per_index(self):
        """KNOWN BUG (not yet fixed, see AGENT.md §7 / PERF_PLAN.md): the
        segment-index regex in resolve_all_segments() only matches a digit
        immediately followed by the extension (e.g. "a_000.mp4"). A segment
        that has since been compressed to "..._hevc.mp4" (digits, then
        "_hevc", then the extension) does not match, so a compressed segment
        silently drops out of the playlist instead of being preferred over
        the uncompressed original. This test pins the exact expected
        (currently unmet) behavior; remove @expectedFailure once the regex
        is fixed -- if this test starts passing unexpectedly, that's the
        signal the fix landed."""
        files = {"/r/a_000.mp4", "/r/a_000_hevc.mp4"}
        segs = library.resolve_all_segments("/r/a_%03d.ts",
                                            exists=lambda p: p in files,
                                            glob_fn=self._fake_glob_factory(files))
        self.assertEqual(segs, [(0, "/r/a_000_hevc.mp4")])

    def test_cache_hit_skips_glob(self):
        files = {"/r/a_000.mp4", "/r/a_001.mp4"}
        glob_calls = []
        base_glob = self._fake_glob_factory(files)
        def fake_glob(pattern):
            glob_calls.append(pattern)
            return base_glob(pattern)
        cache = {}
        stored = "/r/a_%03d.ts"
        segs1 = library.resolve_all_segments(stored, exists=lambda p: p in files,
                                             glob_fn=fake_glob, cache=cache)
        self.assertEqual(len(segs1), 2)
        n1 = len(glob_calls)
        self.assertGreater(n1, 0)
        segs2 = library.resolve_all_segments(stored, exists=lambda p: p in files,
                                             glob_fn=fake_glob, cache=cache)
        self.assertEqual(segs2, segs1)
        self.assertEqual(len(glob_calls), n1, "cache hit must not re-glob")

    def test_cache_invalidates_if_any_segment_gone(self):
        files = {"/r/a_000.mp4", "/r/a_001.mp4"}
        cache = {}
        stored = "/r/a_%03d.ts"
        segs1 = library.resolve_all_segments(stored, exists=lambda p: p in files,
                                             glob_fn=self._fake_glob_factory(files),
                                             cache=cache)
        self.assertEqual(len(segs1), 2)
        files.discard("/r/a_001.mp4")
        files.add("/r/a_001.mkv")

        def fake_glob2(pattern):
            ext = pattern.rsplit(".", 1)[-1]
            if "_hevc" in pattern:
                return []
            return sorted(f for f in files if f.endswith("." + ext))
        glob_calls = []
        def fake_glob2_tracked(pattern):
            glob_calls.append(pattern)
            return fake_glob2(pattern)
        segs2 = library.resolve_all_segments(stored, exists=lambda p: p in files,
                                             glob_fn=fake_glob2_tracked, cache=cache)
        self.assertGreater(len(glob_calls), 0, "must re-glob when a cached segment vanished")
        self.assertIn((1, "/r/a_001.mkv"), segs2)


class GroupByDateTests(unittest.TestCase):
    def test_counts_real_segments_not_db_rows(self):
        files = {"/r/a_000.mp4", "/r/a_001.mp4", "/r/a_002.mp4"}
        def fake_glob(pattern):
            if "_hevc" in pattern:
                return []
            return sorted(files) if pattern.endswith(".mp4") else []
        sessions = [{"start_time": "2026-08-20T10:00:00", "duration_sec": 5400,
                     "file_path": "/r/a_%03d.ts"}]
        buckets = library.group_by_date(sessions, exists=lambda p: p in files,
                                        glob_fn=fake_glob)
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0]["date"], "2026-08-20")
        self.assertEqual(buckets[0]["count"], 3)
        self.assertEqual(buckets[0]["total_sec"], 5400)

    def test_non_segmented_session_counts_as_one(self):
        sessions = [{"start_time": "2026-08-20T10:00:00", "duration_sec": 600,
                     "file_path": "/r/a.mp4"}]
        buckets = library.group_by_date(sessions, exists=lambda p: True,
                                        glob_fn=lambda pat: [])
        self.assertEqual(buckets[0]["count"], 1)

    def test_sorted_newest_first(self):
        sessions = [
            {"start_time": "2026-08-01T10:00:00", "duration_sec": 60, "file_path": "/r/a.mp4"},
            {"start_time": "2026-08-20T10:00:00", "duration_sec": 60, "file_path": "/r/b.mp4"},
        ]
        buckets = library.group_by_date(sessions, exists=lambda p: True,
                                        glob_fn=lambda pat: [])
        self.assertEqual([b["date"] for b in buckets], ["2026-08-20", "2026-08-01"])

    def test_shares_segments_cache_across_calls(self):
        files = {"/r/a_000.mp4"}
        glob_calls = []
        def fake_glob(pattern):
            glob_calls.append(pattern)
            return sorted(files) if pattern.endswith(".mp4") and "_hevc" not in pattern else []
        cache = {}
        sessions = [{"start_time": "2026-08-20T10:00:00", "duration_sec": 60,
                     "file_path": "/r/a_%03d.ts"}]
        library.group_by_date(sessions, exists=lambda p: p in files,
                              glob_fn=fake_glob, segments_cache=cache)
        n1 = len(glob_calls)
        library.group_by_date(sessions, exists=lambda p: p in files,
                              glob_fn=fake_glob, segments_cache=cache)
        self.assertEqual(len(glob_calls), n1, "second call must hit cache")


class BuildPlaylistTests(unittest.TestCase):
    def test_expands_segmented_session_into_multiple_clips(self):
        files = {"/r/a_000.mp4", "/r/a_001.mp4"}
        def fake_glob(pattern):
            return sorted(files) if pattern.endswith(".mp4") and "_hevc" not in pattern else []
        sessions = [{"start_time": "2026-08-20T10:00:00", "duration_sec": 3600,
                     "file_path": "/r/a_%03d.ts", "anchor_name": "序号1 小美",
                     "platform": "douyin", "live_url": "https://x"}]
        items = library.build_playlist(sessions, "2026-08-20",
                                       exists=lambda p: p in files, glob_fn=fake_glob,
                                       segment_seconds=1800)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["anchor_name"], "小美")
        self.assertEqual(items[0]["path"], "/r/a_000.mp4")
        self.assertEqual(items[1]["path"], "/r/a_001.mp4")
        self.assertEqual(items[0]["duration_sec"], 1800)

    def test_single_file_session(self):
        files = {"/r/a.mp4"}
        sessions = [{"start_time": "2026-08-20T10:00:00", "duration_sec": 600,
                     "file_path": "/r/a.mp4", "anchor_name": "小美",
                     "platform": "douyin", "live_url": "https://x"}]
        items = library.build_playlist(sessions, "2026-08-20",
                                       exists=lambda p: p in files, glob_fn=lambda p: [])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["path"], "/r/a.mp4")

    def test_unresolvable_session_skipped(self):
        sessions = [{"start_time": "2026-08-20T10:00:00", "duration_sec": 600,
                     "file_path": "/r/gone.mp4", "anchor_name": "小美"}]
        items = library.build_playlist(sessions, "2026-08-20",
                                       exists=lambda p: False, glob_fn=lambda p: [])
        self.assertEqual(items, [])

    def test_filters_by_date(self):
        sessions = [{"start_time": "2026-08-19T10:00:00", "duration_sec": 600,
                     "file_path": "/r/a.mp4", "anchor_name": "小美"}]
        items = library.build_playlist(sessions, "2026-08-20",
                                       exists=lambda p: True, glob_fn=lambda p: [])
        self.assertEqual(items, [])


class MiscPureHelperTests(unittest.TestCase):
    def test_find_sidecar_subtitles_both_present(self):
        files = {"/r/a.danmaku.srt", "/r/a.danmaku.ass"}
        out = library.find_sidecar_subtitles("/r/a.mp4", exists=lambda p: p in files)
        self.assertEqual(out, {"srt": "/r/a.danmaku.srt", "ass": "/r/a.danmaku.ass"})

    def test_find_sidecar_subtitles_none_present(self):
        out = library.find_sidecar_subtitles("/r/a.mp4", exists=lambda p: False)
        self.assertEqual(out, {"srt": None, "ass": None})

    def test_estimate_segment_window_middle_segment(self):
        start, dur = library.estimate_segment_window(
            "2026-08-20T10:00:00", total_duration_sec=5400, index=1, segment_seconds=1800)
        self.assertEqual(start, "2026-08-20T10:30:00")
        self.assertEqual(dur, 1800)

    def test_estimate_segment_window_shorter_trailing_segment(self):
        start, dur = library.estimate_segment_window(
            "2026-08-20T10:00:00", total_duration_sec=4000, index=2, segment_seconds=1800)
        self.assertEqual(dur, 400)

    def test_list_anchors_groups_by_canonical_name_and_sorts_by_latest(self):
        sessions = [
            {"anchor_name": "序号1 小美", "start_time": "2026-08-01T10:00:00", "duration_sec": 60},
            {"anchor_name": "序号2 小美", "start_time": "2026-08-20T10:00:00", "duration_sec": 60},
            {"anchor_name": "阿明", "start_time": "2026-08-10T10:00:00", "duration_sec": 30},
        ]
        anchors = library.list_anchors(sessions)
        names = [a["anchor_name"] for a in anchors]
        self.assertEqual(names, ["小美", "阿明"])
        self.assertEqual(next(a for a in anchors if a["anchor_name"] == "小美")["count"], 2)


class LocateEpochInWindowsTests(unittest.TestCase):
    """回放標記 (playback markers) 需要把一個絕對 epoch 換算成『屬於哪個 clip、
    片段內第幾秒』，跨同一 session 相鄰片段時尤其重要——見 web_ui.py
    _session_clip_windows() / api_library_clip_marks()。"""

    def test_empty_windows_returns_none(self):
        self.assertIsNone(library.locate_epoch_in_windows([], 1000))

    def test_single_window_inside(self):
        windows = [(1000, 2000, "clipA")]
        self.assertEqual(library.locate_epoch_in_windows(windows, 1500), ("clipA", 500))

    def test_start_boundary_inclusive(self):
        windows = [(1000, 2000, "clipA")]
        self.assertEqual(library.locate_epoch_in_windows(windows, 1000), ("clipA", 0))

    def test_end_boundary_exclusive_falls_to_next_window(self):
        windows = [(1000, 2000, "clipA"), (2000, 3000, "clipB")]
        self.assertEqual(library.locate_epoch_in_windows(windows, 2000), ("clipB", 0))

    def test_picks_correct_segment_among_several(self):
        windows = [(0, 1800, "seg0"), (1800, 3600, "seg1"), (3600, 5400, "seg2")]
        self.assertEqual(library.locate_epoch_in_windows(windows, 4000), ("seg2", 400))

    def test_unordered_input_still_works(self):
        windows = [(3600, 5400, "seg2"), (0, 1800, "seg0"), (1800, 3600, "seg1")]
        self.assertEqual(library.locate_epoch_in_windows(windows, 100), ("seg0", 100))

    def test_before_first_window_clamps_to_first(self):
        windows = [(1000, 2000, "clipA"), (2000, 3000, "clipB")]
        self.assertEqual(library.locate_epoch_in_windows(windows, 500), ("clipA", 0))

    def test_after_last_window_clamps_to_last(self):
        # e.g. live duration overran the last estimated segment window slightly
        windows = [(1000, 2000, "clipA"), (2000, 3000, "clipB")]
        self.assertEqual(library.locate_epoch_in_windows(windows, 3050), ("clipB", 1050))

    def test_gap_between_windows_attaches_to_preceding(self):
        windows = [(1000, 1500, "clipA"), (2000, 2500, "clipB")]
        # epoch 1700 falls in the gap between clipA's end and clipB's start
        self.assertEqual(library.locate_epoch_in_windows(windows, 1700), ("clipA", 700))


if __name__ == "__main__":
    unittest.main()
