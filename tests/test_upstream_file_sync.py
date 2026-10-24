"""Tests for tools/upstream_file_sync.py (upstream-spider-sync.yml helper).
Run from repo root: python -m unittest discover -s tests -v"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "upstream_file_sync", REPO / "tools" / "upstream_file_sync.py")
ufs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ufs)


class ReadAutoUpdateFilesTests(unittest.TestCase):
    def test_real_update_checker(self):
        src = (REPO / "src" / "update_checker.py").read_text(encoding="utf-8")
        self.assertEqual(ufs.read_auto_update_files(src), ["src/spider.py"])

    def test_missing(self):
        with self.assertRaises(ValueError):
            ufs.read_auto_update_files("X = 1\n")

    def test_bad_type(self):
        with self.assertRaises(ValueError):
            ufs.read_auto_update_files("AUTO_UPDATE_FILES = 'src/spider.py'\n")
        with self.assertRaises(ValueError):
            ufs.read_auto_update_files("AUTO_UPDATE_FILES = ['']\n")

    def test_state_file_covers_every_listed_file(self):
        src = (REPO / "src" / "update_checker.py").read_text(encoding="utf-8")
        state = json.loads((REPO / ufs.STATE_PATH).read_text(encoding="utf-8"))
        for rel in ufs.read_auto_update_files(src):
            with self.subTest(rel=rel):
                entry = state["files"][rel]
                self.assertRegex(entry["base_commit"], r"^[0-9a-f]{40}$")
                self.assertRegex(entry["base_blob"], r"^[0-9a-f]{40}$")


class RelativeImportTests(unittest.TestCase):
    def setUp(self):
        self.pkg = Path(tempfile.mkdtemp())
        (self.pkg / "__init__.py").write_text("JS = 1\n", encoding="utf-8")
        (self.pkg / "utils.py").write_text(
            "import os\nfrom x import y as z\nA, B = 1, 2\n"
            "def helper(): pass\nclass K: pass\nasync def ah(): pass\n",
            encoding="utf-8")
        (self.pkg / "clients").mkdir()
        (self.pkg / "clients" / "__init__.py").write_text("", encoding="utf-8")
        (self.pkg / "clients" / "http.py").write_text("def req(): pass\n", encoding="utf-8")
        (self.pkg / "star.py").write_text("from os.path import *\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.pkg, ignore_errors=True)

    def check(self, src):
        return ufs.missing_relative_imports(src, self.pkg)

    def test_all_resolve(self):
        self.assertEqual(self.check(
            "from . import JS, utils\nfrom .utils import helper, K, A, B, z, os, ah\n"
            "from .clients.http import req\nfrom .clients import http\n"), [])

    def test_missing_name(self):
        probs = self.check("from .utils import helper, new_helper\n")
        self.assertEqual(len(probs), 1)
        self.assertIn("new_helper", probs[0])

    def test_missing_module(self):
        probs = self.check("from .nope import x\n")
        self.assertEqual(len(probs), 1)
        self.assertIn("module not found", probs[0])

    def test_missing_package_attr(self):
        self.assertEqual(len(self.check("from . import NOT_THERE\n")), 1)

    def test_star_import_cannot_verify(self):
        self.assertEqual(self.check("from .star import anything\n"), [])

    def test_absolute_imports_ignored(self):
        self.assertEqual(self.check("import os\nfrom os import path\n"), [])

    def test_current_fork_spider_resolves(self):
        src = (REPO / "src" / "spider.py").read_text(encoding="utf-8")
        self.assertEqual(ufs.missing_relative_imports(src, REPO / "src"), [])


class PureHelperTests(unittest.TestCase):
    def test_compiles(self):
        self.assertIsNone(ufs.compiles("x = 1\n", "a.py"))
        self.assertIn("SyntaxError", ufs.compiles("def (:\n", "a.py"))

    def test_insert_status_line(self):
        text = "# T\n\n## 目前進度\n\n- old\n\n## 下一步\n"
        out = ufs.insert_status_line(text, "- new")
        self.assertEqual(out, "# T\n\n## 目前進度\n\n- new\n- old\n\n## 下一步\n")

    def test_insert_status_line_on_real_status_md(self):
        text = (REPO / "STATUS.md").read_text(encoding="utf-8")
        out = ufs.insert_status_line(text, "- X")
        self.assertIn("## 目前進度\n\n- X\n- ", out)

    def test_insert_status_line_missing_heading(self):
        with self.assertRaises(ValueError):
            ufs.insert_status_line("# no heading\n", "- x")

    def test_summarize(self):
        merged = {"path": "a", "status": ufs.MERGED, "checks": []}
        bad = {"path": "a", "status": ufs.MERGED, "checks": ["name not found"]}
        same = {"path": "b", "status": ufs.UNCHANGED, "checks": []}
        conflict = {"path": "b", "status": ufs.CONFLICT, "checks": []}
        sha = "a" * 40
        cases = [
            ([merged, same], True, True),
            ([bad, same], True, False),
            ([merged, conflict], True, False),
            ([same], False, False),
            ([conflict], False, False),
        ]
        for results, changed, ok in cases:
            with self.subTest(results=results):
                plan = ufs.summarize(results, sha, "u")
                self.assertIs(plan["changed"], changed)
                self.assertIs(plan["auto_merge_ok"], ok)
        self.assertTrue(ufs.summarize([conflict], sha, "u")["needs_attention"])
        self.assertIn("aaaaaaa", ufs.render_markdown(ufs.summarize([merged], sha, "u")))


BASE = "import os\n\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n"
FORK = BASE.replace("return 2", "return 2  # fork patch")
UP_OTHER = BASE.replace("return 1", "return 11")       # upstream edits a()
UP_SAME_LINE = BASE.replace("return 2", "return 22")   # upstream edits b() -> conflict


@unittest.skipUnless(shutil.which("git"), "git not installed")
class GitApplyTests(unittest.TestCase):
    """End-to-end in a throwaway repo: branch `upstream` plays ihmily/main."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "update_checker.py").write_text(
            'AUTO_UPDATE_FILES = ["src/spider.py"]\n', encoding="utf-8")
        self.write_spider(BASE)
        self.git("add", "-A")
        self.git("commit", "-qm", "upstream base")
        self.base_commit = self.out("rev-parse", "HEAD")
        self.base_blob = self.out("rev-parse", "HEAD:src/spider.py")
        self.git("branch", "upstream")
        self.write_spider(FORK)
        (self.repo / ".github").mkdir()
        (self.repo / ufs.STATE_PATH).write_text(json.dumps({"files": {"src/spider.py": {
            "base_commit": self.base_commit, "base_blob": self.base_blob}}}),
            encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "fork patch")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    def out(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def write_spider(self, text):
        (self.repo / "src" / "spider.py").write_text(text, encoding="utf-8")

    def upstream_commit(self, text):
        self.git("checkout", "-q", "upstream")
        self.write_spider(text)
        self.git("commit", "-qam", "upstream change")
        sha = self.out("rev-parse", "HEAD")
        self.git("checkout", "-q", "main")
        return sha

    def spider(self):
        return (self.repo / "src" / "spider.py").read_text(encoding="utf-8")

    def state(self):
        return json.loads((self.repo / ufs.STATE_PATH).read_text(encoding="utf-8"))

    def test_nothing_new_upstream_keeps_fork_patch(self):
        plan = ufs.apply(self.repo, "upstream", "u")
        self.assertFalse(plan["changed"])
        self.assertFalse(plan["needs_attention"])
        self.assertEqual(plan["files"][0]["status"], ufs.UNCHANGED)
        self.assertEqual(self.spider(), FORK)

    def test_clean_merge_keeps_fork_patch_and_moves_baseline(self):
        new = self.upstream_commit(UP_OTHER)
        plan = ufs.apply(self.repo, "upstream", "u")
        self.assertTrue(plan["changed"])
        self.assertTrue(plan["auto_merge_ok"])
        self.assertIn("return 11", self.spider())
        self.assertIn("# fork patch", self.spider())
        entry = self.state()["files"]["src/spider.py"]
        self.assertEqual(entry["base_commit"], new)
        self.assertEqual(entry["base_blob"], self.out("rev-parse", "upstream:src/spider.py"))
        # second run against the same upstream is a no-op
        self.assertFalse(ufs.apply(self.repo, "upstream", "u")["changed"])

    def test_conflict_leaves_file_and_state_alone(self):
        self.upstream_commit(UP_SAME_LINE)
        plan = ufs.apply(self.repo, "upstream", "u")
        self.assertFalse(plan["changed"])
        self.assertTrue(plan["needs_attention"])
        self.assertEqual(plan["files"][0]["status"], ufs.CONFLICT)
        self.assertEqual(self.spider(), FORK)
        self.assertEqual(self.state()["files"]["src/spider.py"]["base_commit"],
                         self.base_commit)

    def test_merge_that_breaks_relative_import_is_not_auto_mergeable(self):
        (self.repo / "src" / "utils.py").write_text("def old(): pass\n", encoding="utf-8")
        self.upstream_commit("from .utils import brand_new\n" + BASE)
        plan = ufs.apply(self.repo, "upstream", "u")
        self.assertTrue(plan["changed"])
        self.assertFalse(plan["auto_merge_ok"])
        self.assertIn("brand_new", " ".join(plan["files"][0]["checks"]))

    def test_no_baseline(self):
        self.upstream_commit(UP_OTHER)
        (self.repo / ufs.STATE_PATH).write_text('{"files": {}}', encoding="utf-8")
        plan = ufs.apply(self.repo, "upstream", "u")
        self.assertEqual(plan["files"][0]["status"], ufs.NO_BASELINE)
        self.assertTrue(plan["needs_attention"])
        self.assertEqual(self.spider(), FORK)

    def test_missing_upstream_file(self):
        self.git("checkout", "-q", "upstream")
        self.git("rm", "-q", "src/spider.py")
        self.git("commit", "-qm", "rm")
        self.git("checkout", "-q", "main")
        plan = ufs.apply(self.repo, "upstream", "u")
        self.assertEqual(plan["files"][0]["status"], ufs.MISSING_UPSTREAM)
        self.assertEqual(self.spider(), FORK)


if __name__ == "__main__":
    unittest.main()
