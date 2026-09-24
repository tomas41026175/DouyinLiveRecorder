"""Regression tests for src/marks_store.py (SQLite storage for playback
bookmarks). Uses a real temp SQLite file per test, same convention as
test_danmaku_store.py. Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
marks_store = load("marks_store")


class MarksStoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = marks_store.MarksStore(os.path.join(self._tmpdir.name, "m.db"))

    def tearDown(self):
        self._tmpdir.cleanup()


class AddAndListTests(MarksStoreTestCase):
    def test_add_point_mark_then_list(self):
        row = self.store.add("session-A", 1000.0, note="精彩片段")
        self.assertIsNotNone(row["id"])
        self.assertEqual(row["session_key"], "session-A")
        self.assertEqual(row["start_epoch"], 1000.0)
        self.assertIsNone(row["end_epoch"])
        self.assertEqual(row["note"], "精彩片段")

        rows = self.store.list_for_session("session-A")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["start_epoch"], 1000.0)

    def test_add_range_mark(self):
        row = self.store.add("session-A", 1000.0, end_epoch=1050.5, note="一段")
        self.assertEqual(row["end_epoch"], 1050.5)

    def test_missing_session_key_raises(self):
        with self.assertRaises(ValueError):
            self.store.add("", 1000.0)

    def test_end_before_start_raises(self):
        with self.assertRaises(ValueError):
            self.store.add("session-A", 1000.0, end_epoch=900.0)

    def test_end_equal_start_raises(self):
        with self.assertRaises(ValueError):
            self.store.add("session-A", 1000.0, end_epoch=1000.0)

    def test_note_defaults_to_empty_string(self):
        row = self.store.add("session-A", 1000.0)
        self.assertEqual(row["note"], "")

    def test_note_is_stripped(self):
        row = self.store.add("session-A", 1000.0, note="  hi  ")
        self.assertEqual(row["note"], "hi")

    def test_list_for_session_sorted_by_start_epoch(self):
        self.store.add("session-A", 3000.0, note="c")
        self.store.add("session-A", 1000.0, note="a")
        self.store.add("session-A", 2000.0, note="b")
        rows = self.store.list_for_session("session-A")
        self.assertEqual([r["note"] for r in rows], ["a", "b", "c"])

    def test_list_for_session_filters_by_session(self):
        self.store.add("session-A", 1000.0, note="a")
        self.store.add("session-B", 1000.0, note="b")
        rows = self.store.list_for_session("session-A")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["note"], "a")

    def test_list_for_unknown_session_returns_empty(self):
        self.assertEqual(self.store.list_for_session("nope"), [])


class UpdateAndDeleteTests(MarksStoreTestCase):
    def test_update_note(self):
        row = self.store.add("session-A", 1000.0, note="old")
        ok = self.store.update_note(row["id"], "new")
        self.assertTrue(ok)
        rows = self.store.list_for_session("session-A")
        self.assertEqual(rows[0]["note"], "new")

    def test_update_note_unknown_id_returns_false(self):
        self.assertFalse(self.store.update_note(999, "x"))

    def test_delete_removes_row(self):
        row = self.store.add("session-A", 1000.0)
        self.assertTrue(self.store.delete(row["id"]))
        self.assertEqual(self.store.list_for_session("session-A"), [])

    def test_delete_unknown_id_returns_false(self):
        self.assertFalse(self.store.delete(999))

    def test_delete_for_session_removes_only_that_session(self):
        self.store.add("session-A", 1000.0)
        self.store.add("session-A", 2000.0)
        self.store.add("session-B", 1000.0)
        removed = self.store.delete_for_session("session-A")
        self.assertEqual(removed, 2)
        self.assertEqual(self.store.list_for_session("session-A"), [])
        self.assertEqual(len(self.store.list_for_session("session-B")), 1)


class GetStoreSingletonTests(unittest.TestCase):
    def test_same_path_returns_same_instance(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.db")
            s1 = marks_store.get_store(path)
            s2 = marks_store.get_store(path)
            self.assertIs(s1, s2)

    def test_different_path_returns_new_instance(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            s1 = marks_store.get_store(os.path.join(d1, "m.db"))
            s2 = marks_store.get_store(os.path.join(d2, "m.db"))
            self.assertIsNot(s1, s2)


if __name__ == "__main__":
    unittest.main()
