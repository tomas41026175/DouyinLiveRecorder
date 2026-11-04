"""Regression tests for src/live_view.py（工作檯直播即時觀看的純邏輯）。
Run from repo root: python -m unittest discover -s tests -v"""
import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
lv = load("live_view")


def _room(status=2, keys=("ld", "sd", "hd", "origin", "ao"), opts=("ld", "sd", "hd", "origin"), hmap=None):
    """依 2026-11-04 實測的 webcast/room/web/enter 回應形狀做的 fixture（網址為假值）"""
    names = {"ld": "ld", "sd": "sd", "hd": "hd", "origin": "or4", "ao": "ao", "md": "md"}
    data = {k: {"main": {"hls": f"http://pull-hls.example.com/third/s_{names[k]}.m3u8?expire=1&sign=x",
                         "flv": f"http://pull-flv.example.com/third/s_{names[k]}.flv"}} for k in keys}
    qualities = [{"sdk_key": k, "resolution": "720x1280", "v_bit_rate": 2000000} for k in opts]
    return {
        "status": status, "id_str": "7704113317882940200", "title": "測試", "anchor_name": "小甜",
        "cover": {"url_list": ["http://p11.example.com/c.jpg"]},
        "stream_url": {
            "stream_orientation": 1,
            "hls_pull_url_map": hmap if hmap is not None else {},
            "live_core_sdk_data": {"pull_data": {"stream_data": json.dumps({"data": data}),
                                                 "options": {"qualities": qualities}}},
        },
    }


class NormalizeInputTests(unittest.TestCase):
    def test_douyin_id_and_room_number(self):
        self.assertEqual(lv.normalize_live_input("abc_123"), "https://live.douyin.com/abc_123")
        self.assertEqual(lv.normalize_live_input(" 336950279642 "), "https://live.douyin.com/336950279642")

    def test_url_without_scheme(self):
        self.assertEqual(lv.normalize_live_input("live.douyin.com/123"), "https://live.douyin.com/123")
        self.assertEqual(lv.normalize_live_input("douyin.com/123"), "https://live.douyin.com/123")
        self.assertEqual(lv.normalize_live_input("v.douyin.com/iAbc/"), "https://v.douyin.com/iAbc/")

    def test_share_text_takes_last_url(self):
        text = "8- 長按複製此條消息，打開抖音搜索，查看TA的更多作品。 https://v.douyin.com/iRNBho6u/ 复制此链接"
        self.assertEqual(lv.normalize_live_input(text), "https://v.douyin.com/iRNBho6u/")

    def test_prefix_then_pasted_url(self):
        self.assertEqual(lv.normalize_live_input("https://live.douyin.com/https://live.douyin.com/999"),
                         "https://live.douyin.com/999")

    def test_unrecognized(self):
        self.assertIsNone(lv.normalize_live_input(""))
        self.assertIsNone(lv.normalize_live_input("小甜 港澳"))
        self.assertIsNone(lv.normalize_live_input("https://live.douyin.com/"))
        self.assertIsNone(lv.normalize_live_input("https://example.com/x"))


class CanonicalTests(unittest.TestCase):
    def test_strip_query_and_slash(self):
        self.assertEqual(lv.canonical_url("http://live.douyin.com/123/?show_type=highlight"),
                         "https://live.douyin.com/123")
        self.assertEqual(lv.url_key("https://live.douyin.com/123?a=1"), lv.url_key("https://live.douyin.com/123"))

    def test_other_urls_untouched(self):
        self.assertEqual(lv.canonical_url("https://v.douyin.com/iAbc/"), "https://v.douyin.com/iAbc/")


class QualityTests(unittest.TestCase):
    def test_order_labels_https(self):
        qs = lv.extract_qualities(_room())
        self.assertEqual([q["key"] for q in qs], ["ao", "ld", "sd", "hd", "origin"])
        self.assertEqual(qs[1]["label"], "標清")
        self.assertTrue(all(q["url"].startswith("https://") for q in qs))

    def test_quality_not_in_options_is_dropped_but_ao_kept(self):
        qs = lv.extract_qualities(_room(keys=("ld", "md", "hd", "ao"), opts=("ld", "hd")))
        self.assertEqual([q["key"] for q in qs], ["ao", "ld", "hd"])

    def test_fallback_to_hls_pull_url_map(self):
        room = _room(keys=(), opts=(), hmap={"HD1": "http://x/a_hd.m3u8", "SD1": "http://x/a_ld.m3u8",
                                             "FULL_HD1": "http://x/a_or4.m3u8", "ORIGIN": "http://x/a_or4.m3u8?codec=h264"})
        qs = lv.extract_qualities(room)
        self.assertEqual([q["key"] for q in qs], ["ld", "hd", "origin"])

    def test_same_stream_deduped(self):
        room = _room(keys=("hd", "origin"), opts=("hd", "origin"))
        sd = json.loads(room["stream_url"]["live_core_sdk_data"]["pull_data"]["stream_data"])
        sd["data"]["origin"]["main"]["hls"] = sd["data"]["hd"]["main"]["hls"]
        room["stream_url"]["live_core_sdk_data"]["pull_data"]["stream_data"] = json.dumps(sd)
        self.assertEqual([q["key"] for q in lv.extract_qualities(room)], ["hd"])

    def test_pick_default(self):
        qs = lambda *ks: [{"key": k} for k in ks]
        self.assertEqual(lv.pick_default(qs("ao", "ld", "sd", "hd")), "hd")
        self.assertEqual(lv.pick_default(qs("ao", "ld", "sd"), "hd"), "sd")
        self.assertEqual(lv.pick_default(qs("sd", "origin"), "ld"), "sd")
        self.assertEqual(lv.pick_default(qs("ao")), "ao")
        self.assertIsNone(lv.pick_default([]))


class SummarizeTests(unittest.TestCase):
    def test_live_room(self):
        s = lv.summarize_room("http://live.douyin.com/123?x=1", _room())
        self.assertTrue(s["live"])
        self.assertEqual(s["url"], "https://live.douyin.com/123")
        self.assertEqual(s["roomId"], "7704113317882940200")
        self.assertEqual(s["defaultQuality"], "hd")
        self.assertEqual(s["orientation"], "portrait")
        self.assertTrue(s["cover"].startswith("https://"))

    def test_offline_and_failed_spider(self):
        self.assertFalse(lv.summarize_room("https://live.douyin.com/1", _room(status=4))["live"])
        s = lv.summarize_room("https://live.douyin.com/1", {"anchor_name": ""})
        self.assertFalse(s["live"])
        self.assertEqual(s["qualities"], [])


class ShortLinkTests(unittest.TestCase):
    """2026-11-04：v.douyin.com 短網址找不到直播間——App 端 API 帶設定檔 cookie 回不出資料，不帶就正常。"""

    def test_is_live_page(self):
        self.assertTrue(lv.is_live_page("https://live.douyin.com/245217253187"))
        self.assertFalse(lv.is_live_page("https://live.douyin.com/"))
        self.assertFalse(lv.is_live_page("https://v.douyin.com/bGDeUMO1Y7E"))

    def test_live_url_from_room(self):
        self.assertEqual(lv.live_url_from_room({"owner": {"web_rid": "245217253187"}}), "https://live.douyin.com/245217253187")
        self.assertIsNone(lv.live_url_from_room({"owner": {}}))
        self.assertIsNone(lv.live_url_from_room({}))
        self.assertIsNone(lv.live_url_from_room({"owner": {"web_rid": "../x"}}))

    def test_cookie_attempts(self):
        self.assertEqual(lv.cookie_attempts("a=1"), ["a=1", ""])
        self.assertEqual(lv.cookie_attempts("  "), [""])

    def test_pick_room(self):
        failed, offline, live = {"anchor_name": ""}, {"status": 4}, _room()
        self.assertIs(lv.pick_room([failed, offline, live]), live)
        self.assertIs(lv.pick_room([failed, offline]), offline)
        self.assertEqual(lv.pick_room([failed, {}]), {})


class WatchRegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.reg = lv.WatchRegistry(ttl=120, max_items=2, clock=lambda: self.now)

    def test_expires_after_ttl(self):
        self.reg.touch("https://live.douyin.com/1?a=1", "A", "r1")
        self.assertEqual([w["url"] for w in self.reg.active()], ["https://live.douyin.com/1"])
        self.now += 121
        self.assertEqual(self.reg.active(), [])

    def test_touch_keeps_room_id_and_evicts_oldest(self):
        self.reg.touch("https://live.douyin.com/1", "A", "r1")
        self.now += 1
        self.reg.touch("https://live.douyin.com/1", "")
        self.assertEqual(self.reg.active()[0]["room_id"], "r1")
        self.assertEqual(self.reg.active()[0]["anchor_name"], "A")
        self.now += 1
        self.reg.touch("https://live.douyin.com/2")
        self.now += 1
        self.reg.touch("https://live.douyin.com/3")
        self.assertEqual(sorted(w["url"][-1] for w in self.reg.active()), ["2", "3"])

    def test_drop(self):
        self.reg.touch("https://live.douyin.com/1")
        self.reg.drop("https://live.douyin.com/1/")
        self.assertEqual(self.reg.active(), [])


class MergeTargetsTests(unittest.TestCase):
    def test_recording_wins_and_dedupes(self):
        rec = [{"url": "https://live.douyin.com/1?from=x", "anchor_name": "A"}]
        watch = [{"url": "https://live.douyin.com/1", "anchor_name": "A2", "room_id": "r1"},
                 {"url": "https://live.douyin.com/2", "anchor_name": "B", "room_id": "r2"},
                 {"url": "https://www.huya.com/3", "anchor_name": "C"}]
        out = lv.merge_capture_targets(rec, watch)
        self.assertEqual([o["url"] for o in out], ["https://live.douyin.com/1?from=x", "https://live.douyin.com/2"])
        self.assertEqual(out[1]["room_id"], "r2")


class ShapeDanmakuTests(unittest.TestCase):
    def test_reverse_and_filter(self):
        rows = [{"id": 3, "ts": 3, "user": "u", "content": "c", "msg_type": "chat"},
                {"id": 2, "ts": 2, "user": "u", "content": "gift", "msg_type": "gift"},
                {"id": 1, "ts": 1, "user": None, "content": "a", "msg_type": None}]
        self.assertEqual([r["id"] for r in lv.shape_danmaku(rows)], [1, 3])
        self.assertEqual(lv.shape_danmaku(rows)[0]["user"], "")


if __name__ == "__main__":
    unittest.main()
