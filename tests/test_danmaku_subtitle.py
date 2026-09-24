"""Regression tests for src/danmaku_subtitle.py (pure ASS/SRT builders).
Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
danmaku_subtitle = load("danmaku_subtitle")


def _row(ts, content, user=""):
    return {"ts": ts, "content": content, "user": user}


class OffsetsFilterTests(unittest.TestCase):
    def test_drops_rows_before_start(self):
        rows = [_row(999, "before"), _row(1000, "at start"), _row(1005, "after")]
        out = danmaku_subtitle._offsets(rows, start_epoch=1000, duration=None)
        contents = [o["content"] for o in out]
        self.assertEqual(contents, ["at start", "after"])

    def test_drops_rows_past_duration(self):
        rows = [_row(1000, "in"), _row(1100, "past end")]
        out = danmaku_subtitle._offsets(rows, start_epoch=1000, duration=50)
        self.assertEqual([o["content"] for o in out], ["in"])

    def test_drops_empty_content(self):
        rows = [_row(1000, ""), _row(1001, "   "), _row(1002, "real")]
        out = danmaku_subtitle._offsets(rows, start_epoch=1000, duration=None)
        self.assertEqual([o["content"] for o in out], ["real"])

    def test_sorted_by_time(self):
        rows = [_row(1010, "later"), _row(1000, "earlier")]
        out = danmaku_subtitle._offsets(rows, start_epoch=1000, duration=None)
        self.assertEqual([o["t"] for o in out], [0.0, 10.0])
        self.assertEqual([o["content"] for o in out], ["earlier", "later"])

    def test_malformed_ts_skipped_not_crashed(self):
        rows = [{"ts": "not-a-number", "content": "x"}, _row(1000, "ok")]
        out = danmaku_subtitle._offsets(rows, start_epoch=1000, duration=None)
        self.assertEqual([o["content"] for o in out], ["ok"])


class BuildSrtTests(unittest.TestCase):
    def test_single_cue_basic_timing(self):
        rows = [_row(1000, "hello")]
        srt = danmaku_subtitle.build_srt(rows, start_epoch=1000)
        self.assertIn("1\n00:00:00,000 -->", srt)
        self.assertIn("hello", srt)

    def test_groups_messages_within_window(self):
        rows = [_row(1000, "a"), _row(1001, "b")]  # 1s apart, group_window default 2.0
        srt = danmaku_subtitle.build_srt(rows, start_epoch=1000)
        self.assertEqual(srt.count("-->"), 1, "close-together messages must share one cue")
        self.assertIn("a", srt); self.assertIn("b", srt)

    def test_far_apart_messages_get_separate_cues(self):
        rows = [_row(1000, "a"), _row(1010, "b")]
        srt = danmaku_subtitle.build_srt(rows, start_epoch=1000)
        self.assertEqual(srt.count("-->"), 2)

    def test_with_user_prefixes_name(self):
        rows = [_row(1000, "hi", user="小美")]
        srt = danmaku_subtitle.build_srt(rows, start_epoch=1000, with_user=True)
        self.assertIn("小美: hi", srt)

    def test_without_user_flag_no_prefix(self):
        rows = [_row(1000, "hi", user="小美")]
        srt = danmaku_subtitle.build_srt(rows, start_epoch=1000, with_user=False)
        self.assertNotIn("小美:", srt)

    def test_max_lines_per_cue_respected(self):
        rows = [_row(1000 + i, f"msg{i}") for i in range(6)]  # all within group_window chain
        srt = danmaku_subtitle.build_srt(rows, start_epoch=1000, max_lines=4)
        # first cue caps at 4 lines, remainder spill into a new cue
        first_cue = srt.split("\n\n")[0]
        line_count = sum(1 for ln in first_cue.split("\n")[2:] if ln)
        self.assertLessEqual(line_count, 4)

    def test_empty_rows_returns_empty_string(self):
        self.assertEqual(danmaku_subtitle.build_srt([], start_epoch=1000), "")


class BuildAssTests(unittest.TestCase):
    def test_contains_header_and_dialogue(self):
        rows = [_row(1000, "hello")]
        ass = danmaku_subtitle.build_ass(rows, start_epoch=1000)
        self.assertIn("[Script Info]", ass)
        self.assertIn("[Events]", ass)
        self.assertIn("Dialogue:", ass)
        self.assertIn("hello", ass)

    def test_empty_rows_no_dialogue_lines(self):
        ass = danmaku_subtitle.build_ass([], start_epoch=1000)
        self.assertIn("[Events]", ass)
        self.assertNotIn("Dialogue:", ass)

    def test_escapes_braces_and_backslash(self):
        rows = [_row(1000, "a{b}c\\d")]
        ass = danmaku_subtitle.build_ass(rows, start_epoch=1000)
        self.assertIn("a(b)c", ass)
        self.assertNotIn("{b}", ass)

    def test_many_concurrent_messages_use_multiple_lanes(self):
        rows = [_row(1000, f"m{i}") for i in range(5)]
        ass = danmaku_subtitle.build_ass(rows, start_epoch=1000, lanes=3)
        # all 5 must still appear even though only 3 lanes exist (lane reuse)
        for i in range(5):
            self.assertIn(f"m{i}", ass)


class SidecarPathsTests(unittest.TestCase):
    def test_basic(self):
        ass, srt = danmaku_subtitle.sidecar_paths("/r/video.mp4")
        self.assertEqual(ass, "/r/video.danmaku.ass")
        self.assertEqual(srt, "/r/video.danmaku.srt")

    def test_custom_suffix(self):
        ass, srt = danmaku_subtitle.sidecar_paths("/r/video.mp4", suffix=".x")
        self.assertEqual(ass, "/r/video.x.ass")
        self.assertEqual(srt, "/r/video.x.srt")

    def test_strips_only_extension(self):
        ass, srt = danmaku_subtitle.sidecar_paths("/r/a.b.mp4")
        self.assertEqual(ass, "/r/a.b.danmaku.ass")


if __name__ == "__main__":
    unittest.main()
