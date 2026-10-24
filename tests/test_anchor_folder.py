"""Tests for src/anchor_folder.py（由 DB file_path 反推主播資料夾 / 共用資料夾檔案歸屬）。
Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
af = load("anchor_folder")

TS = "2026-10-24_12-00-00"
P = "抖音直播"


class CleanNameTests(unittest.TestCase):
    """與 main.py clean_name 同規則。"""

    def test_illegal_chars_and_fullwidth_parens(self):
        self.assertEqual(af.clean_name("京圈太子 （4/25生日版）"), "京圈太子_(4_25生日版)")

    def test_strip_underscores_and_blank(self):
        self.assertEqual(af.clean_name("  ..A.. "), "A")
        self.assertEqual(af.clean_name("..."), "空白昵称")

    def test_emoji_toggle(self):
        self.assertEqual(af.clean_name("小美\U0001F496"), "小美")
        self.assertEqual(af.clean_name("小美\U0001F496", clean_emoji=False), "小美\U0001F496")

    def test_idempotent(self):
        once = af.clean_name("a/b（c）")
        self.assertEqual(af.clean_name(once), once)


class PlatformFolderTests(unittest.TestCase):
    def test_suffix_and_exceptions(self):
        self.assertEqual(af.platform_folder_name("抖音"), "抖音直播")
        self.assertEqual(af.platform_folder_name("SOOP"), "SOOP")
        self.assertEqual(af.platform_folder_name("Shopee"), "shopee")
        self.assertEqual(af.platform_folder_name(""), "未知平台")
        self.assertEqual(af.platform_folder_name("其他"), "未知平台")


class DeriveAuthorTests(unittest.TestCase):
    """以作者区分（預設）+ 時間 / 標題的各種組合。"""

    def d(self, path, platform=P):
        return af.derive_from_file_path(path, platform)

    def test_author_only(self):
        r = self.d(f"F:/rec/{P}/A/A_{TS}.ts")
        self.assertEqual(r, {"kind": "author", "folder": f"F:/rec/{P}/A"})

    def test_author_time(self):
        r = self.d(f"F:/rec/{P}/A/2026-10-24/A_{TS}_000.ts")
        self.assertEqual(r["folder"], f"F:/rec/{P}/A")

    def test_author_title_without_time(self):
        r = self.d(f"F:/rec/{P}/A/2026-10-24_今晚唱歌/A_今晚唱歌_{TS}.ts")
        self.assertEqual(r["folder"], f"F:/rec/{P}/A")

    def test_author_time_title(self):
        r = self.d(f"F:/rec/{P}/A/2026-10-24/今晚唱歌_A/A_今晚唱歌_{TS}.flv")
        self.assertEqual(r["folder"], f"F:/rec/{P}/A")

    def test_windows_backslashes_and_mixed(self):
        # main.py 自己會把 \\ 換成 /，但 glob 解析分段檔時在 Windows 會混入反斜線
        r = self.d(f"f:\\Rec\\{P}/京圈太子_(4_25生日版)\\京圈太子_(4_25生日版)_{TS}_000.ts")
        self.assertEqual(r["folder"], f"f:/Rec/{P}/京圈太子_(4_25生日版)")

    def test_platform_case_insensitive(self):
        r = self.d(f"D:/rec/tiktok直播/A/A_{TS}.ts", platform="TikTok直播")
        self.assertEqual(r["folder"], "D:/rec/tiktok直播/A")

    def test_root_containing_platform_name(self):
        # 儲存根目錄本身就叫「抖音直播」，主播名剛好也是根目錄某段的前綴
        r = self.d(f"D:/{P}/A/{P}/A/A_{TS}.ts")
        self.assertEqual(r["folder"], f"D:/{P}/A/{P}/A")

    def test_relative_and_posix(self):
        self.assertEqual(self.d(f"downloads/{P}/A/A_{TS}.ts")["folder"], f"downloads/{P}/A")
        self.assertEqual(self.d(f"/mnt/r/{P}/A/A_{TS}.ts")["folder"], f"/mnt/r/{P}/A")

    def test_unc(self):
        r = self.d(f"\\\\nas\\share\\{P}\\A\\A_{TS}.ts")
        self.assertEqual(r["folder"], f"//nas/share/{P}/A")

    def test_unknown_platform_still_finds_author(self):
        r = self.d(f"F:/rec/{P}/A/A_{TS}.ts", platform=None)
        self.assertEqual(r["folder"], f"F:/rec/{P}/A")

    def test_unresolved_segment_pattern(self):
        r = self.d(f"F:/rec/{P}/A/A_{TS}_%03d.ts")
        self.assertEqual(r["folder"], f"F:/rec/{P}/A")

    def test_unusable(self):
        self.assertIsNone(self.d(None))
        self.assertIsNone(self.d(""))
        self.assertIsNone(self.d("x.ts"))


class DeriveSharedTests(unittest.TestCase):
    """沒以作者区分：多位主播共用資料夾，只能靠檔名前綴歸屬。"""

    def test_platform_only(self):
        r = af.derive_from_file_path(f"F:/rec/{P}/A_{TS}_000.ts", P)
        self.assertEqual(r, {"kind": "shared", "dir": f"F:/rec/{P}", "prefix": f"A_{TS}"})

    def test_time_only(self):
        r = af.derive_from_file_path(f"F:/rec/{P}/2026-10-24/A_{TS}.ts", P)
        self.assertEqual(r["kind"], "shared")
        self.assertEqual(r["dir"], f"F:/rec/{P}/2026-10-24")

    def test_title_only(self):
        r = af.derive_from_file_path(f"F:/rec/{P}/2026-10-24_唱歌/A_唱歌_{TS}.ts", P)
        self.assertEqual(r["kind"], "shared")
        self.assertEqual(r["prefix"], f"A_唱歌_{TS}")

    def test_time_title(self):
        r = af.derive_from_file_path(f"F:/rec/{P}/2026-10-24/唱歌_A/A_唱歌_{TS}.ts", P)
        self.assertEqual(r["kind"], "shared")
        self.assertEqual(r["dir"], f"F:/rec/{P}/2026-10-24/唱歌_A")

    def test_anchor_named_like_root_component_is_not_author(self):
        # 根目錄某段 "rec" 剛好是檔名前綴，但上一層不是平台資料夾 → 不可誤判成主播資料夾
        r = af.derive_from_file_path(f"F:/rec/{P}/rec_{TS}.ts", P)
        self.assertEqual(r["kind"], "shared")


class SessionPrefixTests(unittest.TestCase):
    def test_prefix_cuts_at_timestamp(self):
        self.assertEqual(af.session_prefix(f"A_{TS}_000.ts"), f"A_{TS}")
        self.assertEqual(af.session_prefix(f"A_{TS}.flv"), f"A_{TS}")

    def test_prefix_without_timestamp(self):
        self.assertEqual(af.session_prefix("weird_007.ts"), "weird")

    def test_compressed_and_converted_files_match(self):
        """H.265 壓縮（compressor: stem + '_hevc' + '.mp4'，壓縮中 '.part.<enc>.mp4'）、
        轉 mp4、其他分段都要算進同一場。"""
        listing = [{"name": n, "size": 1} for n in [
            f"A_{TS}_000.ts",                  # DB 記的第一段（可能已被壓縮刪掉）
            f"A_{TS}_001_hevc.mp4",            # 壓縮後
            f"A_{TS}_002_hevc.part.nvenc.mp4",  # 壓縮中
            f"A_{TS}_003.mp4",                 # 轉檔後
            f"A_{TS}.srt",                     # 字幕
            "A_2026-10-24_12-00-001.ts",       # 不同（較長）的前綴，不可吃到
            f"B_{TS}_000.ts",                  # 別人的
            f"AB_{TS}_000.ts",                 # 名稱以 A 開頭的別人
        ]]
        got = [f["name"] for f in af.select_shared_files(listing, f"A_{TS}")]
        self.assertEqual(got, [f"A_{TS}_000.ts", f"A_{TS}_001_hevc.mp4",
                               f"A_{TS}_002_hevc.part.nvenc.mp4", f"A_{TS}_003.mp4",
                               f"A_{TS}.srt"])

    def test_custom_compress_suffix(self):
        self.assertTrue(af.name_matches_prefix(f"A_{TS}_000_x265.mp4", f"A_{TS}"))
        self.assertFalse(af.name_matches_prefix("A", ""))


class BuildLocationsTests(unittest.TestCase):
    U1, U2, U3 = "https://live.douyin.com/1", "https://live.douyin.com/2", "https://live.douyin.com/3"

    def test_author_latest_first_and_dedupe(self):
        rows = [  # ORDER BY id DESC
            (self.U1, P, f"F:/rec/{P}/A2/A2_{TS}.ts", "序号3 A2"),   # 改名後
            (self.U1, P, f"f:/REC/{P}/A2/A2_2026-10-23_10-00-00.ts", "序号3 A2"),
            (self.U1, P, f"F:/rec/{P}/A/A_2026-10-01_10-00-00.ts", "序号3 A"),
            (self.U1, P, None, "序号3 A"),
        ]
        loc = af.build_locations(rows)[self.U1]
        self.assertEqual(loc["source"], "db")
        self.assertEqual(loc["folder"], f"F:/rec/{P}/A2")
        self.assertEqual(loc["folders"], [f"F:/rec/{P}/A2", f"F:/rec/{P}/A"])
        self.assertEqual(loc["shared"], [])
        self.assertEqual(loc["anchor"], "A2")

    def test_shared_sessions(self):
        rows = [(self.U2, P, f"F:/rec/{P}/B_{TS}_000.ts", "序号1 B"),
                (self.U2, P, f"F:/rec/{P}/B_2026-10-23_10-00-00.ts", "序号1 B")]
        loc = af.build_locations(rows)[self.U2]
        self.assertEqual(loc["folders"], [])
        self.assertEqual(loc["shared"], [(f"F:/rec/{P}", f"B_{TS}"),
                                         (f"F:/rec/{P}", "B_2026-10-23_10-00-00")])
        self.assertEqual(loc["folder"], f"F:/rec/{P}")

    def test_no_file_path_rows_keep_platform_and_anchor(self):
        rows = [(self.U3, "SOOP", None, "序号9 C"), (self.U3, "SOOP", "", "序号9 C")]
        loc = af.build_locations(rows)[self.U3]
        self.assertIsNone(loc["folder"])
        self.assertEqual(loc["platform"], "SOOP")
        self.assertEqual(loc["anchor"], "C")

    def test_three_column_rows_and_empty(self):
        self.assertEqual(af.build_locations([]), {})
        loc = af.build_locations([(self.U1, P, f"F:/rec/{P}/A/A_{TS}.ts")])[self.U1]
        self.assertEqual(loc["anchor"], "")
        self.assertEqual(loc["folder"], f"F:/rec/{P}/A")

    def test_strip_record_prefix(self):
        self.assertEqual(af.strip_record_prefix("序号12 京圈太子"), "京圈太子")
        self.assertEqual(af.strip_record_prefix("X"), "X")
        self.assertEqual(af.strip_record_prefix(None), "")


class GuessFallbackTests(unittest.TestCase):
    def test_author_guess_applies_clean_name(self):
        g = af.guess_location("F:\\rec\\", "抖音直播", "京圈太子 （4/25生日版）",
                              {"by_author": True})
        self.assertEqual(g, {"kind": "author",
                             "folder": "F:/rec/抖音直播/京圈太子_(4_25生日版)"})

    def test_shared_guess(self):
        g = af.guess_location("/r", "SOOP", "A", {"by_author": False})
        self.assertEqual(g["kind"], "shared_guess")
        self.assertEqual(g["folder"], "/r/SOOP")
        self.assertTrue(af.guess_name_matches(f"A_{TS}_000.ts", g["anchor_prefix"]))
        self.assertTrue(af.guess_name_matches(f"A_唱歌_{TS}.ts", g["anchor_prefix"]))
        self.assertFalse(af.guess_name_matches(f"AB_{TS}.ts", g["anchor_prefix"]))
        self.assertFalse(af.guess_name_matches("A_notes.txt", g["anchor_prefix"]))

    def test_no_anchor(self):
        self.assertIsNone(af.guess_location("/r", P, "", {"by_author": True}))


if __name__ == "__main__":
    unittest.main()
