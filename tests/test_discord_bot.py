"""Regression tests for src/discord_bot.py's pure command-parsing logic
(parse_command / normalize_target_url / find_matching_entries / format_list /
format_help). The networking half (discord.py Gateway connection) needs a
real Discord bot token + connection, same tradeoff as danmaku_capture.py's
websocket layer -- not covered here, exercised manually.
Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
discord_bot = load("discord_bot")


class NormalizeTargetUrlTests(unittest.TestCase):
    def test_bare_id_expanded_to_room_url(self):
        self.assertEqual(discord_bot.normalize_target_url("abc123"),
                         "https://live.douyin.com/abc123")

    def test_full_https_url_unchanged(self):
        url = "https://live.douyin.com/123456"
        self.assertEqual(discord_bot.normalize_target_url(url), url)

    def test_full_http_url_unchanged(self):
        url = "http://live.douyin.com/123456"
        self.assertEqual(discord_bot.normalize_target_url(url), url)

    def test_strips_whitespace(self):
        self.assertEqual(discord_bot.normalize_target_url("  abc123  "),
                         "https://live.douyin.com/abc123")

    def test_empty_returns_empty(self):
        self.assertEqual(discord_bot.normalize_target_url(""), "")
        self.assertEqual(discord_bot.normalize_target_url(None), "")


class ParseCommandTests(unittest.TestCase):
    def test_not_prefixed_returns_none(self):
        self.assertIsNone(discord_bot.parse_command("hello world", "!"))

    def test_empty_after_prefix_returns_none(self):
        self.assertIsNone(discord_bot.parse_command("!", "!"))
        self.assertIsNone(discord_bot.parse_command("!   ", "!"))

    def test_unrecognized_command_returns_none(self):
        self.assertIsNone(discord_bot.parse_command("!hello", "!"))

    def test_add_with_url_and_name(self):
        cmd = discord_bot.parse_command("!新增 https://live.douyin.com/123 小美", "!")
        self.assertEqual(cmd, {"action": "add",
                               "url": "https://live.douyin.com/123",
                               "anchor_name": "小美"})

    def test_add_alias_加(self):
        cmd = discord_bot.parse_command("!加 123456 阿明", "!")
        self.assertEqual(cmd["action"], "add")
        self.assertEqual(cmd["url"], "https://live.douyin.com/123456")
        self.assertEqual(cmd["anchor_name"], "阿明")

    def test_add_without_name(self):
        cmd = discord_bot.parse_command("!新增 123456", "!")
        self.assertEqual(cmd["anchor_name"], "")

    def test_add_bare_id_normalized(self):
        cmd = discord_bot.parse_command("!新增 my_douyin_id", "!")
        self.assertEqual(cmd["url"], "https://live.douyin.com/my_douyin_id")

    def test_remove(self):
        cmd = discord_bot.parse_command("!移除 小美", "!")
        self.assertEqual(cmd, {"action": "remove", "target": "小美"})

    def test_remove_aliases(self):
        self.assertEqual(discord_bot.parse_command("!刪除 小美", "!")["action"], "remove")
        self.assertEqual(discord_bot.parse_command("!删除 小美", "!")["action"], "remove")

    def test_list_variants(self):
        self.assertEqual(discord_bot.parse_command("!清單", "!"), {"action": "list"})
        self.assertEqual(discord_bot.parse_command("!列表", "!"), {"action": "list"})
        self.assertEqual(discord_bot.parse_command("!list", "!"), {"action": "list"})

    def test_help_variants(self):
        self.assertEqual(discord_bot.parse_command("!help", "!"), {"action": "help"})
        self.assertEqual(discord_bot.parse_command("!說明", "!"), {"action": "help"})

    def test_custom_prefix(self):
        cmd = discord_bot.parse_command("~清單", "~")
        self.assertEqual(cmd, {"action": "list"})
        self.assertIsNone(discord_bot.parse_command("!清單", "~"))

    def test_empty_prefix_never_matches(self):
        self.assertIsNone(discord_bot.parse_command("清單", ""))


class FindMatchingEntriesTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {"anchor_name": "小美", "url": "https://live.douyin.com/111"},
            {"anchor_name": "阿明", "url": "https://live.douyin.com/222"},
            {"anchor_name": "", "url": "https://live.douyin.com/333"},
        ]

    def test_exact_anchor_name_match(self):
        matches = discord_bot.find_matching_entries(self.entries, "小美")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["url"], "https://live.douyin.com/111")

    def test_exact_match_case_insensitive(self):
        entries = [{"anchor_name": "ABC", "url": "https://live.douyin.com/1"}]
        matches = discord_bot.find_matching_entries(entries, "abc")
        self.assertEqual(len(matches), 1)

    def test_falls_back_to_url_substring(self):
        matches = discord_bot.find_matching_entries(self.entries, "333")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["url"], "https://live.douyin.com/333")

    def test_no_match_returns_empty(self):
        self.assertEqual(discord_bot.find_matching_entries(self.entries, "不存在"), [])

    def test_empty_target_returns_empty(self):
        self.assertEqual(discord_bot.find_matching_entries(self.entries, ""), [])

    def test_exact_name_wins_over_url_substring_ambiguity(self):
        # anchor named "222" would also url-substring-match entry 2, but the
        # exact anchor_name match on a DIFFERENT entry should win outright.
        entries = [
            {"anchor_name": "222", "url": "https://live.douyin.com/999"},
            {"anchor_name": "阿明", "url": "https://live.douyin.com/222"},
        ]
        matches = discord_bot.find_matching_entries(entries, "222")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["anchor_name"], "222")

    def test_multiple_url_substring_matches(self):
        entries = [
            {"anchor_name": "A", "url": "https://live.douyin.com/abc111"},
            {"anchor_name": "B", "url": "https://live.douyin.com/abc222"},
        ]
        matches = discord_bot.find_matching_entries(entries, "abc")
        self.assertEqual(len(matches), 2)


class FormatListTests(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(discord_bot.format_list([]), "目前沒有設定任何主播")

    def test_marks_recording_disabled_idle(self):
        entries = [
            {"anchor_name": "小美", "url": "u1", "enabled": True, "is_recording": True},
            {"anchor_name": "阿明", "url": "u2", "enabled": False, "is_recording": False},
            {"anchor_name": "", "url": "u3", "enabled": True, "is_recording": False},
        ]
        text = discord_bot.format_list(entries)
        self.assertIn("小美", text)
        self.assertIn("阿明", text)
        self.assertIn("(未命名)", text)
        self.assertEqual(len(text.splitlines()), 3)

    def test_long_list_truncated(self):
        entries = [{"anchor_name": f"主播{i}", "url": f"u{i}", "enabled": True,
                   "is_recording": False} for i in range(200)]
        text = discord_bot.format_list(entries)
        self.assertLessEqual(len(text), 2000)
        self.assertIn("截斷", text)


class FormatHelpTests(unittest.TestCase):
    def test_includes_prefix(self):
        text = discord_bot.format_help("!")
        self.assertIn("!新增", text)
        self.assertIn("!移除", text)
        self.assertIn("!清單", text)


class SettingsTests(unittest.TestCase):
    def test_load_settings_defaults(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "discord_bot.json")
            s = discord_bot.load_settings(path)
            self.assertEqual(s, discord_bot.DEFAULT_DISCORD_BOT)

    def test_save_then_load_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "discord_bot.json")
            discord_bot.save_settings(path, {"enabled": True, "token": "abc",
                                             "channel_id": "999"})
            s = discord_bot.load_settings(path)
            self.assertTrue(s["enabled"])
            self.assertEqual(s["token"], "abc")
            self.assertEqual(s["channel_id"], "999")
            self.assertEqual(s["prefix"], "!")  # default preserved


if __name__ == "__main__":
    unittest.main()
