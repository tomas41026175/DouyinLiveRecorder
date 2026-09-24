"""Regression tests for src/danmaku_store.py (SQLite storage + query).
Uses a real temp SQLite file per test (no mocking -- this module IS the IO
layer, so testing against a real DB is more meaningful than faking it).
Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
danmaku_store = load("danmaku_store")


class DanmakuStoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = danmaku_store.DanmakuStore(os.path.join(self._tmpdir.name, "d.db"))

    def tearDown(self):
        self._tmpdir.cleanup()


class RecordAndQueryTests(DanmakuStoreTestCase):
    def test_record_then_query_roundtrip(self):
        self.store.record("https://x", "hello", user="小美", anchor_name="小美",
                          platform="douyin", ts=1000)
        rows = self.store.query()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "hello")
        self.assertEqual(rows[0]["anchor_name"], "小美")

    def test_empty_content_ignored(self):
        self.store.record("https://x", "", ts=1000)
        self.assertEqual(self.store.total(), 0)

    def test_query_filters_by_anchor(self):
        self.store.record("https://x", "a", anchor_name="小美", ts=1000)
        self.store.record("https://y", "b", anchor_name="阿明", ts=1000)
        rows = self.store.query(anchor="小美")
        self.assertEqual([r["content"] for r in rows], ["a"])

    def test_query_filters_by_url(self):
        self.store.record("https://x", "a", ts=1000)
        self.store.record("https://y", "b", ts=1000)
        rows = self.store.query(url="https://x")
        self.assertEqual([r["content"] for r in rows], ["a"])

    def test_query_since_until_range(self):
        for i, ts in enumerate([100, 200, 300, 400]):
            self.store.record("https://x", f"m{i}", ts=ts)
        rows = self.store.query(since=150, until=350, order="asc")
        self.assertEqual([r["content"] for r in rows], ["m1", "m2"])

    def test_query_keyword_like_search(self):
        self.store.record("https://x", "hello world", ts=1000)
        self.store.record("https://x", "goodbye", ts=1001)
        rows = self.store.query(keyword="hello")
        self.assertEqual(len(rows), 1)

    def test_query_order_asc_desc(self):
        self.store.record("https://x", "first", ts=100)
        self.store.record("https://x", "second", ts=200)
        asc = self.store.query(order="asc")
        desc = self.store.query(order="desc")
        self.assertEqual([r["content"] for r in asc], ["first", "second"])
        self.assertEqual([r["content"] for r in desc], ["second", "first"])

    def test_count_matches_query_length(self):
        for i in range(5):
            self.store.record("https://x", f"m{i}", ts=1000 + i)
        self.assertEqual(self.store.count(), 5)
        self.assertEqual(self.store.count(since=1002), 3)

    def test_record_many_batch_insert(self):
        n = self.store.record_many([
            {"streamer_url": "https://x", "content": "a", "ts": 1000},
            {"streamer_url": "https://x", "content": "", "ts": 1001},  # skipped
            {"streamer_url": "https://x", "content": "b", "ts": 1002},
        ])
        self.assertEqual(n, 2)
        self.assertEqual(self.store.total(), 2)

    def test_record_many_empty_list_returns_zero(self):
        self.assertEqual(self.store.record_many([]), 0)


class DeleteContentMatchingTests(DanmakuStoreTestCase):
    def test_deletes_only_matching_rows(self):
        self.store.record("https://x", "internal_src:pushserver|seq:1", ts=1000)
        self.store.record("https://x", "real chat message", ts=1001)
        self.store.record("https://x", "another internal_src:pushserver row", ts=1002)
        deleted = self.store.delete_content_matching("internal_src:pushserver")
        self.assertEqual(deleted, 2)
        remaining = self.store.query()
        self.assertEqual([r["content"] for r in remaining], ["real chat message"])

    def test_empty_needle_deletes_nothing(self):
        self.store.record("https://x", "hello", ts=1000)
        self.assertEqual(self.store.delete_content_matching(""), 0)
        self.assertEqual(self.store.total(), 1)

    def test_needle_with_percent_and_underscore_treated_literally(self):
        # % and _ are SQL LIKE wildcards -- must be escaped so they only
        # match literal % / _ characters, not "any string"/"any char".
        self.store.record("https://x", "100% real content, no wildcard match here", ts=1000)
        self.store.record("https://x", "totally unrelated message", ts=1001)
        deleted = self.store.delete_content_matching("100% real")
        self.assertEqual(deleted, 1)
        remaining = self.store.query()
        self.assertEqual([r["content"] for r in remaining], ["totally unrelated message"])


class AnchorsAndPruneTests(DanmakuStoreTestCase):
    def test_anchors_grouping_and_count(self):
        self.store.record("https://x", "a", anchor_name="小美", ts=1000)
        self.store.record("https://x", "b", anchor_name="小美", ts=1001)
        self.store.record("https://y", "c", anchor_name="阿明", ts=1002)
        anchors = self.store.anchors()
        by_name = {a["anchor_name"]: a for a in anchors}
        self.assertEqual(by_name["小美"]["count"], 2)
        self.assertEqual(by_name["阿明"]["count"], 1)

    def test_prune_removes_only_older_rows(self):
        import time
        now = int(time.time())
        self.store.record("https://x", "old", ts=now - 10 * 86400)
        self.store.record("https://x", "recent", ts=now)
        removed = self.store.prune(older_than_days=5)
        self.assertEqual(removed, 1)
        remaining = self.store.query()
        self.assertEqual([r["content"] for r in remaining], ["recent"])


class GetStoreSingletonTests(unittest.TestCase):
    def test_same_path_returns_same_instance(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "d.db")
            s1 = danmaku_store.get_store(path)
            s2 = danmaku_store.get_store(path)
            self.assertIs(s1, s2)

    def test_different_path_returns_new_instance(self):
        with tempfile.TemporaryDirectory() as d:
            s1 = danmaku_store.get_store(os.path.join(d, "a.db"))
            s2 = danmaku_store.get_store(os.path.join(d, "b.db"))
            self.assertIsNot(s1, s2)


if __name__ == "__main__":
    unittest.main()
