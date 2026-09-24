"""
health.py - System health evaluation for DouyinLiveRecorder.

Pure, dependency-free logic that turns a snapshot of the system state into an
overall status (ok / warn / critical) plus a structured list of issues. Kept
free of Flask / filesystem / network IO so it can be unit-tested in isolation.

The web layer (web_ui.py) gathers the snapshot and feeds it in; the watchdog
thread compares results over time and pushes alerts when things degrade or
recover.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Severity ordering for "which is worse" comparisons.
_SEVERITY_RANK = {"ok": 0, "warn": 1, "critical": 2}
# (rev: P0 integrity + cookie checks included)

# Stable issue codes (used for throttle keys + recovery matching).
DISK_CRITICAL = "disk_critical"
DISK_LOW = "disk_low"
RECORDER_DOWN = "recorder_down"
DB_UNWRITABLE = "db_unwritable"
WEBUI_DEGRADED = "webui_degraded"
SUSPECT_RECORDING = "suspect_recording"
COOKIE_EXPIRED = "cookie_expired"

# A finished session shorter than this (and with a tiny/zero file) is "suspect"
# — usually means the stream dropped instantly or auth failed.
SUSPECT_MIN_SECONDS = 15
SUSPECT_MIN_BYTES = 64 * 1024  # 64 KB


def _gb(n: Optional[float]) -> str:
    try:
        return f"{n / (1024 ** 3):.1f}GB"
    except Exception:
        return "?"


def worst(severities) -> str:
    """Return the most severe status among the given severities."""
    out = "ok"
    for s in severities:
        if _SEVERITY_RANK.get(s, 0) > _SEVERITY_RANK[out]:
            out = s
    return out


def evaluate_health(snapshot: Dict[str, Any],
                    disk_warn_gb: float = 10.0,
                    disk_critical_gb: float = 2.0) -> Dict[str, Any]:
    """Evaluate system health from a snapshot dict.

    snapshot keys (all optional; missing => that check is skipped):
        disk_free_bytes   int    free bytes on the recording drive
        disk_total_bytes  int    total bytes on the recording drive
        recorder_running  bool   is DouyinLiveRecorder.exe alive
        recorder_expected bool   should it be running (i.e. there are enabled streamers)
        db_ok             bool   could the history DB be read/written
        recording_count   int    number of active recordings (context only)

    Returns:
        {
          "status": "ok" | "warn" | "critical",
          "issues": [ {code, severity, title, detail}, ... ],
          "checked_keys": [...],
        }
    """
    issues: List[Dict[str, str]] = []
    checked: List[str] = []

    # --- Disk space ----------------------------------------------------------
    free = snapshot.get("disk_free_bytes")
    if free is not None:
        checked.append("disk")
        crit = disk_critical_gb * (1024 ** 3)
        warn = disk_warn_gb * (1024 ** 3)
        if free < crit:
            issues.append({
                "code": DISK_CRITICAL,
                "severity": "critical",
                "title": "磁碟空間嚴重不足",
                "detail": f"剩餘 {_gb(free)}，低於 {disk_critical_gb:g}GB。"
                          f"錄製與資料庫可能立即失敗，請馬上清理空間。",
            })
        elif free < warn:
            issues.append({
                "code": DISK_LOW,
                "severity": "warn",
                "title": "磁碟空間偏低",
                "detail": f"剩餘 {_gb(free)}，低於 {disk_warn_gb:g}GB。建議盡快清理。",
            })

    # --- Database writability -------------------------------------------------
    if "db_ok" in snapshot:
        checked.append("db")
        if snapshot.get("db_ok") is False:
            issues.append({
                "code": DB_UNWRITABLE,
                "severity": "critical",
                "title": "錄製資料庫無法讀寫",
                "detail": "歷史紀錄資料庫出現 I/O 錯誤（通常因磁碟已滿）。"
                          "統計數據可能不準確。",
            })

    # --- Recorder process -----------------------------------------------------
    if "recorder_running" in snapshot:
        checked.append("recorder")
        running = snapshot.get("recorder_running")
        expected = snapshot.get("recorder_expected", True)
        if not running and expected:
            issues.append({
                "code": RECORDER_DOWN,
                "severity": "critical",
                "title": "錄製器未在執行",
                "detail": "DouyinLiveRecorder.exe 沒有在執行，目前不會錄到任何直播。"
                          "可在控制台點「重啟錄製器」或執行 start_widget.bat。",
            })

    # --- Web UI self-degradation (passed through from API layer) --------------
    if snapshot.get("webui_degraded"):
        checked.append("webui")
        issues.append({
            "code": WEBUI_DEGRADED,
            "severity": "warn",
            "title": "控制台以降級模式運作",
            "detail": "部分資料暫時無法取得，顯示的數字可能不完整。",
        })

    status = worst(i["severity"] for i in issues) if issues else "ok"
    return {"status": status, "issues": issues, "checked_keys": checked}


# ---------------------------------------------------------------------------
# P0-1: Recording integrity validation
# ---------------------------------------------------------------------------
def classify_recording(duration_sec: Optional[int],
                       file_size: Optional[int],
                       file_exists: Optional[bool],
                       finished_reason: Optional[str] = None,
                       min_seconds: int = SUSPECT_MIN_SECONDS,
                       min_bytes: int = SUSPECT_MIN_BYTES) -> str:
    """Classify one finished recording as 'ok' or 'suspect'.

    A recording is suspect only when the *recording itself* failed — it ended
    almost immediately with no usable output, produced a truncated on-disk file,
    or the recorder reported an error.

    A file that is simply MISSING after a long, normally-finished recording is
    NOT suspect: that means the file was archived/moved/deleted afterward (e.g.
    uploaded to a netdisk then removed locally), which is routine housekeeping —
    not a recording failure. Flagging those produced large waves of false
    positives. We distinguish the two cases by recording duration: a recording
    that ran for a meaningful length clearly captured content.
    """
    if finished_reason == "error":
        return "suspect"

    ran_meaningfully = duration_sec is not None and duration_sec >= min_seconds

    if file_exists is False:
        # Missing file: only a failure if the recording also barely ran.
        # Long successful recordings whose files were archived/deleted are fine.
        return "ok" if ran_meaningfully else "suspect"

    # File is present (or unknown). A tiny/zero on-disk file is genuinely broken,
    # regardless of how long the session claims to have run (truncated/corrupt).
    if file_size is not None and file_exists is not False and file_size < min_bytes:
        return "suspect"

    # Ended almost immediately with no real output.
    if duration_sec is not None and duration_sec < min_seconds \
            and (file_size is None or file_size < min_bytes):
        return "suspect"
    return "ok"


def evaluate_recordings(recent_sessions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Scan a list of recent finished sessions and report suspect ones.

    Each session dict may contain: anchor_name, platform, duration_sec,
    file_size, file_exists, finished_reason. Returns a dict with the suspect
    list and a ready-to-use issue (or None).
    """
    suspects = []
    for s in recent_sessions or []:
        verdict = classify_recording(
            s.get("duration_sec"), s.get("file_size"),
            s.get("file_exists"), s.get("finished_reason"))
        if verdict == "suspect":
            suspects.append(s)
    issue = None
    if suspects:
        names = ", ".join(
            str(s.get("anchor_name") or s.get("platform") or "?")
            for s in suspects[:5])
        more = f" 等 {len(suspects)} 場" if len(suspects) > 5 else ""
        issue = {
            "code": SUSPECT_RECORDING,
            "severity": "warn",
            "title": "偵測到可能損壞的錄製",
            "detail": f"最近有 {len(suspects)} 場錄製疑似失敗（檔案過小/過短/錯誤）："
                      f"{names}{more}。建議檢查該主播的 cookie 或直播狀態。",
        }
    return {"suspects": suspects, "issue": issue}


# ---------------------------------------------------------------------------
# P0-2: Cookie-expiry detection (heuristic on error text)
# ---------------------------------------------------------------------------
# Substrings that typically indicate an auth/cookie problem rather than a
# transient network blip. Lower-cased match.
_COOKIE_HINTS = (
    "login", "登录", "登入", "未登录", "请先登录",
    "cookie", "unauthor", "401", "403", "forbidden",
    "auth", "认证", "認證", "token", "鉴权", "鑒權",
    "实名", "實名", "verify", "captcha", "验证", "驗證",
)


def looks_like_cookie_issue(error_text: Optional[str]) -> bool:
    """True if an error string smells like an expired/invalid cookie or auth."""
    if not error_text:
        return False
    t = str(error_text).lower()
    return any(h in t for h in _COOKIE_HINTS)


def evaluate_cookie_health(platform_errors: Dict[str, str]) -> Dict[str, Any]:
    """Given {platform: last_error_text}, flag platforms whose errors look like
    cookie/auth failures. Returns {'platforms': [...], 'issue': issue|None}."""
    bad = [p for p, err in (platform_errors or {}).items()
           if looks_like_cookie_issue(err)]
    issue = None
    if bad:
        issue = {
            "code": COOKIE_EXPIRED,
            "severity": "warn",
            "title": "Cookie 可能已過期",
            "detail": f"以下平台出現疑似認證/登入失敗：{', '.join(bad)}。"
                      f"請到 config.ini 重新更新該平台的 cookie。",
        }
    return {"platforms": bad, "issue": issue}
