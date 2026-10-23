"""Regression tests for src/heartbeat.py (Healthchecks heartbeat decision logic).
Run from repo root: python -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
heartbeat = load("heartbeat")

BASE = "https://hc-ping.com/00000000-0000-0000-0000-000000000000"
ON = {"enabled": True, "url": BASE, "interval_sec": 300}


class SettingsTests(unittest.TestCase):
    def test_missing_file_is_disabled(self):
        cfg = heartbeat.load_settings(os.path.join(tempfile.mkdtemp(), "nope.json"))
        self.assertFalse(heartbeat.is_enabled(cfg))

    def test_partial_file_gets_defaults(self):
        path = os.path.join(tempfile.mkdtemp(), "hc.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"enabled": True, "url": BASE}, f)
        cfg = heartbeat.load_settings(path)
        self.assertTrue(heartbeat.is_enabled(cfg))
        self.assertEqual(cfg["interval_sec"], 300)

    def test_enabled_without_url_is_disabled(self):
        self.assertFalse(heartbeat.is_enabled({"enabled": True, "url": "  "}))


class IsDueTests(unittest.TestCase):
    def test_disabled_never_due(self):
        self.assertFalse(heartbeat.is_due({"enabled": False, "url": BASE}, None, 1000.0))

    def test_first_ping_due_immediately(self):
        self.assertTrue(heartbeat.is_due(ON, None, 1000.0))

    def test_respects_interval(self):
        self.assertFalse(heartbeat.is_due(ON, 1000.0, 1299.0))
        self.assertTrue(heartbeat.is_due(ON, 1000.0, 1300.0))

    def test_interval_floor_is_60s(self):
        cfg = dict(ON, interval_sec=5)
        self.assertFalse(heartbeat.is_due(cfg, 1000.0, 1030.0))
        self.assertTrue(heartbeat.is_due(cfg, 1000.0, 1060.0))

    def test_garbage_interval_falls_back_to_default(self):
        cfg = dict(ON, interval_sec="abc")
        self.assertFalse(heartbeat.is_due(cfg, 1000.0, 1100.0))
        self.assertTrue(heartbeat.is_due(cfg, 1000.0, 1300.0))


class BuildPingTests(unittest.TestCase):
    def test_ok_is_success_with_empty_body(self):
        self.assertEqual(heartbeat.build_ping({"status": "ok", "issues": []}), ("success", ""))

    def test_warn_is_still_success_but_lists_issue(self):
        kind, body = heartbeat.build_ping({"status": "warn", "issues": [
            {"code": "disk_low", "severity": "warn", "title": "磁碟空間偏低", "detail": "剩 8 GB"}]})
        self.assertEqual(kind, "success")
        self.assertIn("磁碟空間偏低", body)

    def test_critical_is_fail(self):
        kind, body = heartbeat.build_ping({"status": "critical", "issues": [
            {"code": "recorder_down", "severity": "critical", "title": "錄製器未執行", "detail": "x"}]})
        self.assertEqual(kind, "fail")
        self.assertIn("錄製器未執行", body)

    def test_tolerates_missing_fields(self):
        self.assertEqual(heartbeat.build_ping({}), ("success", ""))


class PingUrlTests(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(heartbeat.ping_url(BASE + "/", "success"), BASE)
        self.assertEqual(heartbeat.ping_url(BASE, "fail"), BASE + "/fail")

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            heartbeat.ping_url(BASE, "log")


class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SendTests(unittest.TestCase):
    def _send_with(self, open_side_effect, kind="success", body=""):
        opener = mock.Mock()
        opener.open.side_effect = open_side_effect
        with mock.patch.object(heartbeat.urllib.request, "build_opener", return_value=opener):
            result = heartbeat.send(BASE, kind, body)
        return result, opener

    def test_success_sends_user_agent(self):
        (ok, detail), opener = self._send_with([_Resp(200)])
        self.assertTrue(ok)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.get_header("User-agent"), heartbeat.USER_AGENT)
        self.assertEqual(req.full_url, BASE)

    def test_non_2xx_reports_status(self):
        (ok, detail), _ = self._send_with([_Resp(404)])
        self.assertFalse(ok)
        self.assertEqual(detail, "HTTP 404")

    def test_network_error_never_raises(self):
        (ok, detail), _ = self._send_with(OSError("timed out"), kind="fail", body="x")
        self.assertFalse(ok)
        self.assertIn("timed out", detail)

    def test_body_keeps_tail_within_limit(self):
        body = "head" + "x" * 20_000 + "TAIL"
        (_, _), opener = self._send_with([_Resp(200)], kind="fail", body=body)
        req = opener.open.call_args.args[0]
        self.assertEqual(len(req.data), heartbeat.MAX_BODY_BYTES)
        self.assertTrue(req.data.endswith(b"TAIL"))


if __name__ == "__main__":
    unittest.main()
