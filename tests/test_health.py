"""Regression tests for src/health.py (pure system-health evaluation logic).
Run from repo root: python -m unittest discover -s tests -v"""
import sys, os, unittest
sys.path.insert(0, os.path.dirname(__file__))
from _loadmod import load
health = load("health")

GB = 1024 ** 3


class WorstTests(unittest.TestCase):
    def test_empty_is_ok(self):
        self.assertEqual(health.worst([]), "ok")

    def test_picks_most_severe(self):
        self.assertEqual(health.worst(["ok", "warn"]), "warn")
        self.assertEqual(health.worst(["warn", "critical", "ok"]), "critical")
        self.assertEqual(health.worst(["ok", "ok"]), "ok")

    def test_order_independent(self):
        self.assertEqual(health.worst(["critical", "warn"]), "critical")
        self.assertEqual(health.worst(["warn", "critical"]), "critical")

    def test_unknown_severity_treated_as_ok_rank(self):
        # unknown strings default to rank 0 via .get(s, 0), so they never win
        # over a real "warn"/"critical" but also never crash the comparison.
        self.assertEqual(health.worst(["bogus", "warn"]), "warn")


class EvaluateHealthDiskTests(unittest.TestCase):
    def test_no_disk_key_skips_check(self):
        out = health.evaluate_health({})
        self.assertEqual(out["status"], "ok")
        self.assertNotIn("disk", out["checked_keys"])
        self.assertEqual(out["issues"], [])

    def test_disk_plenty_is_ok(self):
        out = health.evaluate_health({"disk_free_bytes": 50 * GB})
        self.assertEqual(out["status"], "ok")
        self.assertIn("disk", out["checked_keys"])
        self.assertEqual(out["issues"], [])

    def test_disk_low_warns(self):
        out = health.evaluate_health({"disk_free_bytes": 5 * GB},
                                     disk_warn_gb=10.0, disk_critical_gb=2.0)
        self.assertEqual(out["status"], "warn")
        self.assertEqual(len(out["issues"]), 1)
        self.assertEqual(out["issues"][0]["code"], health.DISK_LOW)
        self.assertEqual(out["issues"][0]["severity"], "warn")

    def test_disk_critical(self):
        out = health.evaluate_health({"disk_free_bytes": 1 * GB},
                                     disk_warn_gb=10.0, disk_critical_gb=2.0)
        self.assertEqual(out["status"], "critical")
        self.assertEqual(out["issues"][0]["code"], health.DISK_CRITICAL)

    def test_disk_exact_boundary_not_yet_critical(self):
        # free == critical threshold should NOT trip critical (strict <)
        out = health.evaluate_health({"disk_free_bytes": 2 * GB},
                                     disk_warn_gb=10.0, disk_critical_gb=2.0)
        self.assertEqual(out["issues"][0]["code"], health.DISK_LOW)

    def test_disk_exact_warn_boundary_is_ok(self):
        # free == warn threshold should NOT trip warn (strict <)
        out = health.evaluate_health({"disk_free_bytes": 10 * GB},
                                     disk_warn_gb=10.0, disk_critical_gb=2.0)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["issues"], [])


class EvaluateHealthDbTests(unittest.TestCase):
    def test_db_ok_true_no_issue(self):
        out = health.evaluate_health({"db_ok": True})
        self.assertIn("db", out["checked_keys"])
        self.assertEqual(out["issues"], [])

    def test_db_ok_false_is_critical(self):
        out = health.evaluate_health({"db_ok": False})
        self.assertEqual(out["status"], "critical")
        self.assertEqual(out["issues"][0]["code"], health.DB_UNWRITABLE)

    def test_db_key_absent_skips_check(self):
        out = health.evaluate_health({"disk_free_bytes": 50 * GB})
        self.assertNotIn("db", out["checked_keys"])


class EvaluateHealthRecorderTests(unittest.TestCase):
    def test_running_no_issue(self):
        out = health.evaluate_health({"recorder_running": True})
        self.assertIn("recorder", out["checked_keys"])
        self.assertEqual(out["issues"], [])

    def test_not_running_but_expected_is_critical(self):
        out = health.evaluate_health({"recorder_running": False, "recorder_expected": True})
        self.assertEqual(out["status"], "critical")
        self.assertEqual(out["issues"][0]["code"], health.RECORDER_DOWN)

    def test_not_running_and_not_expected_is_ok(self):
        out = health.evaluate_health({"recorder_running": False, "recorder_expected": False})
        self.assertEqual(out["issues"], [])

    def test_expected_defaults_true_when_omitted(self):
        # recorder_expected missing -> defaults to True per docstring
        out = health.evaluate_health({"recorder_running": False})
        self.assertEqual(out["issues"][0]["code"], health.RECORDER_DOWN)


class EvaluateHealthWebuiTests(unittest.TestCase):
    def test_degraded_flag_warns(self):
        out = health.evaluate_health({"webui_degraded": True})
        self.assertIn("webui", out["checked_keys"])
        self.assertEqual(out["issues"][0]["code"], health.WEBUI_DEGRADED)
        self.assertEqual(out["status"], "warn")

    def test_degraded_false_no_issue(self):
        out = health.evaluate_health({"webui_degraded": False})
        self.assertEqual(out["issues"], [])


class EvaluateHealthCombinedTests(unittest.TestCase):
    def test_status_is_worst_across_multiple_issues(self):
        out = health.evaluate_health({
            "disk_free_bytes": 5 * GB,       # warn
            "recorder_running": False,        # critical (expected defaults True)
        }, disk_warn_gb=10.0, disk_critical_gb=2.0)
        self.assertEqual(out["status"], "critical")
        codes = {i["code"] for i in out["issues"]}
        self.assertEqual(codes, {health.DISK_LOW, health.RECORDER_DOWN})

    def test_checked_keys_reflects_all_present_checks(self):
        out = health.evaluate_health({
            "disk_free_bytes": 50 * GB,
            "db_ok": True,
            "recorder_running": True,
            "webui_degraded": False,
        })
        self.assertEqual(set(out["checked_keys"]), {"disk", "db", "recorder"})


class ClassifyRecordingTests(unittest.TestCase):
    def test_finished_reason_error_always_suspect(self):
        # even a long, well-sized recording is suspect if recorder reported error
        self.assertEqual(
            health.classify_recording(3600, 10 * 1024 * 1024, True, finished_reason="error"),
            "suspect")

    def test_long_recording_healthy_file_is_ok(self):
        self.assertEqual(
            health.classify_recording(120, 1024 * 1024, True),
            "ok")

    def test_missing_file_after_long_recording_is_ok(self):
        # archived/moved afterward -- not a failure
        self.assertEqual(
            health.classify_recording(120, None, False),
            "ok")

    def test_missing_file_after_short_recording_is_suspect(self):
        self.assertEqual(
            health.classify_recording(5, None, False),
            "suspect")

    def test_tiny_file_present_is_suspect_regardless_of_duration(self):
        self.assertEqual(
            health.classify_recording(3600, 100, True),
            "suspect")

    def test_short_duration_with_no_size_info_is_suspect(self):
        self.assertEqual(
            health.classify_recording(5, None, None),
            "suspect")

    def test_short_duration_with_adequate_file_is_ok(self):
        # ran < min_seconds but file_size already large enough -> not suspect
        self.assertEqual(
            health.classify_recording(5, 1024 * 1024, True),
            "ok")

    def test_all_none_is_ok_by_default(self):
        # no signal at all: duration None (not "ran_meaningfully"), file_exists
        # not False, file_size None -> none of the suspect branches trigger.
        self.assertEqual(
            health.classify_recording(None, None, None),
            "ok")

    def test_custom_thresholds_respected(self):
        self.assertEqual(
            health.classify_recording(20, 500, True, min_seconds=30, min_bytes=1000),
            "suspect")
        self.assertEqual(
            health.classify_recording(20, 500, True, min_seconds=10, min_bytes=100),
            "ok")


class EvaluateRecordingsTests(unittest.TestCase):
    def test_no_sessions_no_issue(self):
        out = health.evaluate_recordings([])
        self.assertEqual(out["suspects"], [])
        self.assertIsNone(out["issue"])

    def test_none_sessions_treated_as_empty(self):
        out = health.evaluate_recordings(None)
        self.assertEqual(out["suspects"], [])
        self.assertIsNone(out["issue"])

    def test_mixed_sessions_flags_only_suspects(self):
        sessions = [
            {"anchor_name": "A", "duration_sec": 200, "file_size": 5_000_000, "file_exists": True},
            {"anchor_name": "B", "duration_sec": 3, "file_size": 0, "file_exists": True},
        ]
        out = health.evaluate_recordings(sessions)
        self.assertEqual(len(out["suspects"]), 1)
        self.assertEqual(out["suspects"][0]["anchor_name"], "B")
        self.assertIsNotNone(out["issue"])
        self.assertEqual(out["issue"]["code"], health.SUSPECT_RECORDING)
        self.assertIn("B", out["issue"]["detail"])

    def test_issue_names_capped_at_five_with_more_suffix(self):
        sessions = [
            {"anchor_name": f"S{i}", "duration_sec": 1, "file_size": 0, "file_exists": True}
            for i in range(7)
        ]
        out = health.evaluate_recordings(sessions)
        self.assertEqual(len(out["suspects"]), 7)
        detail = out["issue"]["detail"]
        self.assertIn("S0", detail)
        self.assertIn("等 7 場", detail)

    def test_falls_back_to_platform_when_no_anchor_name(self):
        sessions = [{"platform": "douyin", "duration_sec": 1, "file_size": 0, "file_exists": True}]
        out = health.evaluate_recordings(sessions)
        self.assertIn("douyin", out["issue"]["detail"])


class LooksLikeCookieIssueTests(unittest.TestCase):
    def test_empty_or_none_is_false(self):
        self.assertFalse(health.looks_like_cookie_issue(None))
        self.assertFalse(health.looks_like_cookie_issue(""))

    def test_matches_known_hints_case_insensitive(self):
        self.assertTrue(health.looks_like_cookie_issue("HTTP 401 Unauthorized"))
        self.assertTrue(health.looks_like_cookie_issue("请先登录后再试"))
        self.assertTrue(health.looks_like_cookie_issue("Cookie expired"))
        self.assertTrue(health.looks_like_cookie_issue("需要實名驗證"))

    def test_unrelated_error_is_false(self):
        self.assertFalse(health.looks_like_cookie_issue("connection timed out"))
        self.assertFalse(health.looks_like_cookie_issue("ffmpeg exited with code 1"))


class EvaluateCookieHealthTests(unittest.TestCase):
    def test_no_errors_no_issue(self):
        out = health.evaluate_cookie_health({})
        self.assertEqual(out["platforms"], [])
        self.assertIsNone(out["issue"])

    def test_none_input_treated_as_empty(self):
        out = health.evaluate_cookie_health(None)
        self.assertEqual(out["platforms"], [])
        self.assertIsNone(out["issue"])

    def test_flags_only_cookie_like_platforms(self):
        out = health.evaluate_cookie_health({
            "douyin": "401 Unauthorized",
            "kuaishou": "connection reset",
        })
        self.assertEqual(out["platforms"], ["douyin"])
        self.assertIsNotNone(out["issue"])
        self.assertEqual(out["issue"]["code"], health.COOKIE_EXPIRED)
        self.assertIn("douyin", out["issue"]["detail"])
        self.assertNotIn("kuaishou", out["issue"]["detail"])


if __name__ == "__main__":
    unittest.main()
