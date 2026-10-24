"""Tests for src/update_checker.py settings (auto_apply off by default, 2026-10-24).
Run from repo root: python -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
# update_checker imports `requests` at module top; CI runs stdlib-only, and
# nothing tested here touches the network, so a bare stub is enough.
sys.modules.setdefault("requests", types.ModuleType("requests"))
from _loadmod import load  # noqa: E402
uc = load("update_checker")


def _write(obj_or_text):
    path = os.path.join(tempfile.mkdtemp(), "update_settings.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))
    return path


class _StopLoop(Exception):
    pass


class SettingsTests(unittest.TestCase):
    def test_missing_file_is_off(self):
        cfg = uc.load_settings(os.path.join(tempfile.mkdtemp(), "nope.json"))
        self.assertIs(cfg["auto_apply"], False)

    def test_true_turns_on(self):
        self.assertIs(uc.load_settings(_write({"auto_apply": True}))["auto_apply"], True)

    def test_false_stays_off(self):
        self.assertIs(uc.load_settings(_write({"auto_apply": False}))["auto_apply"], False)

    def test_non_bool_values_stay_off(self):
        for v in ("true", 1, "yes", None, [True]):
            with self.subTest(v=v):
                self.assertIs(uc.load_settings(_write({"auto_apply": v}))["auto_apply"], False)

    def test_broken_json_is_off(self):
        self.assertIs(uc.load_settings(_write("{not json"))["auto_apply"], False)

    def test_settings_file_sits_next_to_state_file(self):
        self.assertEqual(uc.SETTINGS_FILE.parent, uc.STATE_FILE.parent)
        self.assertEqual(uc.SETTINGS_FILE.name, "update_settings.json")

    def test_default_is_off(self):
        self.assertIs(uc.DEFAULT_SETTINGS["auto_apply"], False)


class ShouldAutoApplyTests(unittest.TestCase):
    def test_matrix(self):
        cases = [
            ({"has_update": True}, {"auto_apply": True}, True),
            ({"has_update": True}, {"auto_apply": False}, False),
            ({"has_update": False}, {"auto_apply": True}, False),
            ({}, {"auto_apply": True}, False),
            ({"has_update": True}, {}, False),
            ({"has_update": True}, {"auto_apply": "true"}, False),
        ]
        for state, settings, want in cases:
            with self.subTest(state=state, settings=settings):
                self.assertIs(uc.should_auto_apply(state, settings), want)


class BackgroundLoopTests(unittest.TestCase):
    """One iteration of _bg_loop with network/file IO patched out."""

    def _run_once(self, auto_apply, settings_on):
        with mock.patch.object(uc, "check_for_update"), \
             mock.patch.object(uc, "load_state", return_value={"has_update": True}), \
             mock.patch.object(uc, "load_settings",
                               return_value={"auto_apply": settings_on}) as ls, \
             mock.patch.object(uc, "apply_update", return_value={"ok": True}) as ap, \
             mock.patch.object(uc.time, "sleep", side_effect=_StopLoop), \
             mock.patch("builtins.print"):
            with self.assertRaises(_StopLoop):
                uc._bg_loop(6.0, auto_apply)
        return ap, ls

    def test_default_follows_settings_off(self):
        ap, ls = self._run_once(None, settings_on=False)
        ls.assert_called()
        ap.assert_not_called()

    def test_default_follows_settings_on(self):
        ap, _ = self._run_once(None, settings_on=True)
        ap.assert_called_once()

    def test_explicit_false_overrides_settings(self):
        ap, _ = self._run_once(False, settings_on=True)
        ap.assert_not_called()

    def test_explicit_true_forces_apply(self):
        ap, _ = self._run_once(True, settings_on=False)
        ap.assert_called_once()

    def test_start_background_checker_defaults_to_settings(self):
        with mock.patch.object(uc.threading, "Thread") as th:
            uc.start_background_checker()
        self.assertEqual(th.call_args.kwargs["args"], (6.0, None))


if __name__ == "__main__":
    unittest.main()
