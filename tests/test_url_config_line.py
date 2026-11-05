"""Tests for src/url_config_line.py（web_ui 寫回 URL_config.ini 的單行 + 主播名逗號警告，workbench#107）。
Run from repo root: python -m unittest discover -s tests -v"""
import re
import sys, os, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
ucl = load("url_config_line")

URL = "https://live.douyin.com/745964462470"
NAME = "૮小雨炒鱿鱼ა"


def recorder_split(line):
    """照抄 main.py 讀 URL_config.ini 一行的切法（約 2125–2153 行；不能 import main，有副作用）。
    回傳 (quality, url, name)；quality 沒寫時是 None。4 段以上跟 main.py 一樣解包丟 ValueError。"""
    line = line.strip()
    if line.startswith("#"):
        line = line.lstrip("#")
    if re.search('[,，]', line):
        split_line = re.split('[,，]', line)
    else:
        split_line = [line, '']
    if len(split_line) == 1:
        return None, split_line[0], ''
    if len(split_line) == 2:
        if re.search(r"(https?://)?(www\.)?[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(:\d+)?(/.*)?", split_line[0]):
            url, name = split_line
            return None, url, name
        quality, url = split_line
        return quality, url, ''
    quality, url, name = split_line
    return quality, url, name


class SavedLineRecorderSplitTests(unittest.TestCase):
    """web_ui 存檔（新增／改名都走 entry_to_line）寫出的行，recorder 要切得出正確的網址與名稱。"""

    def assert_recorder_reads(self, entry, want_name, want_quality=None):
        line = ucl.entry_to_line(entry)
        quality, url, name = recorder_split(line)
        self.assertEqual(url, entry["url"], line)
        self.assertEqual(name, f"主播: {want_name}", line)
        self.assertEqual(quality, want_quality, line)

    def test_trailing_halfwidth_comma(self):
        self.assert_recorder_reads({"url": URL, "anchor_name": NAME + ","}, NAME + "_")

    def test_trailing_fullwidth_comma(self):
        self.assert_recorder_reads({"url": URL, "anchor_name": NAME + "，"}, NAME + "_")

    def test_middle_halfwidth_comma(self):
        self.assert_recorder_reads({"url": URL, "anchor_name": "小雨,炒鱿鱼"}, "小雨_炒鱿鱼")

    def test_middle_fullwidth_comma(self):
        self.assert_recorder_reads({"url": URL, "anchor_name": "小雨，炒鱿鱼"}, "小雨_炒鱿鱼")

    def test_several_commas_with_quality_and_disabled(self):
        # 改名前這種行在 main.py 會切成 5 段、解包丟例外，整份清單那一輪後面都讀不到
        entry = {"url": URL, "anchor_name": "a,b，c,", "quality": "原画", "enabled": False}
        line = ucl.entry_to_line(entry)
        self.assertTrue(line.startswith("#原画,"), line)
        self.assert_recorder_reads(entry, "a_b_c_", want_quality="原画")

    def test_old_line_really_misread(self):
        # 回歸樁：issue 裡那一行，recorder 會把「主播: …」當成網址
        _q, url, _n = recorder_split(f"{URL},主播: {NAME},")
        self.assertEqual(url, f"主播: {NAME}")


class NormalNameTests(unittest.TestCase):
    """一般名稱不受影響，特殊字元原樣保留。"""

    def test_special_chars_kept(self):
        line = ucl.entry_to_line({"url": URL, "anchor_name": NAME})
        self.assertEqual(line, f"{URL},主播: {NAME}")
        self.assertEqual(recorder_split(line), (None, URL, f"主播: {NAME}"))

    def test_other_punctuation_kept(self):
        name = "京圈太子 （4/25生日版）。！"
        self.assertEqual(ucl.entry_to_line({"url": URL, "anchor_name": name}),
                         f"{URL},主播: {name}")

    def test_quality_disabled_and_no_name(self):
        self.assertEqual(ucl.entry_to_line({"url": URL, "anchor_name": "", "quality": "蓝光", "enabled": False}),
                         f"#蓝光,{URL}")
        self.assertEqual(ucl.entry_to_line({"url": URL}), URL)

    def test_sanitize_only_touches_commas(self):
        self.assertEqual(ucl.sanitize_anchor_name(NAME), NAME)
        self.assertEqual(ucl.sanitize_anchor_name("a,b，c"), "a_b_c")
        self.assertEqual(ucl.sanitize_anchor_name(""), "")


class ListWarningTests(unittest.TestCase):
    """主播清單讀到既有的壞行要標警告（web_ui.parse_entry 讀出來的名稱帶著逗號）。"""

    def test_existing_bad_names_flagged(self):
        for name in (NAME + ",", NAME + "，", "小雨,炒鱿鱼", "a，b"):
            self.assertTrue(ucl.name_has_comma(name), name)

    def test_normal_names_not_flagged(self):
        for name in (NAME, "京圈太子 （4/25生日版）", "", None, NAME + "_"):
            self.assertFalse(ucl.name_has_comma(name), name)

    def test_saved_name_no_longer_flagged(self):
        line = ucl.entry_to_line({"url": URL, "anchor_name": NAME + ","})
        self.assertFalse(ucl.name_has_comma(line.split("主播: ", 1)[1]))


if __name__ == "__main__":
    unittest.main()
