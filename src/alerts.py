"""
alerts.py - Proactive health alerts for DouyinLiveRecorder.

Reuses the project's existing push channels (msg_push.py: ntfy / Telegram /
generic webhook) to notify the user when the recorder's *health* degrades —
e.g. disk full, recorder process died, DB unwritable — and again when it
recovers. The original push code only fires on stream on-air/off-air events;
this fills the "something is actually broken" gap.

Design goals:
  * Never spam: each issue code alerts at most once per `min_interval_sec`,
    and a recovery ("✓ 已恢復") fires only after a real alert was sent.
  * Degrade gracefully: any send failure is swallowed and logged; the watchdog
    must never crash because a webhook was unreachable.
  * Zero new dependencies: Telegram/webhook use urllib (like msg_push.py).
"""
from __future__ import annotations

import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

from . import common as _common

DEFAULT_ALERTS = {
    "enabled": False,
    "disk_warn_gb": 10,
    "disk_critical_gb": 2,
    "min_interval_sec": 1800,          # don't re-alert the same issue within 30 min
    "notify_on_recovery": True,
    "channels": {
        "ntfy": {"enabled": False, "url": ""},          # e.g. https://ntfy.sh/my-topic
        "telegram": {"enabled": False, "token": "", "chat_id": ""},
        "webhook": {"enabled": False, "url": ""},        # generic JSON POST
    },
}

_lock = threading.Lock()
# code -> last time we alerted (monotonic seconds)
_last_alert_at: Dict[str, float] = {}
# codes we have an *open* (un-recovered) alert for
_open_alerts: set = set()


# ---------------------------------------------------------------------------
# Config load / save
# ---------------------------------------------------------------------------
# Kept as a thin alias (not a re-implementation) so this module has zero
# duplicate logic vs common.py, while every existing internal caller of
# `_deep_merge(...)` keeps working unchanged (Stage 5 tech-debt cleanup,
# UIUX_SPEC.md §9.1).
_deep_merge = _common.deep_merge


def load_alerts(path) -> Dict[str, Any]:
    """Load alerts config, filling defaults for any missing keys. Never raises."""
    return _common.load_json_settings(path, DEFAULT_ALERTS)


def save_alerts(path, data: Dict[str, Any]) -> None:
    """Persist alerts config (merged onto defaults). Raises on write failure."""
    _common.save_json_settings(path, data, DEFAULT_ALERTS)


# ---------------------------------------------------------------------------
# Low-level senders (best-effort, never raise)
# ---------------------------------------------------------------------------
def _post_json(url: str, payload: dict, timeout: int = 10) -> bool:
    # proxy=None -> force-bypass system/env proxy (this module's original
    # behavior via build_opener(ProxyHandler({}))); no explicit UA (original
    # request never set one either). See common.http_post_json docstring.
    ok, detail = _common.http_post_json(url, payload, proxy=None, timeout=timeout)
    if not ok:
        print(f"[alerts] webhook POST failed: {detail}")
    return ok


def _send_ntfy(url: str, title: str, body: str, critical: bool) -> bool:
    if not url:
        return False
    try:
        headers = {
            "Title": title.encode("utf-8"),
            "Priority": b"urgent" if critical else b"default",
            "Tags": b"rotating_light" if critical else b"warning",
        }
        req = urllib.request.Request(url, data=body.encode("utf-8"),
                                     headers=headers)
        no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        no_proxy.open(req, timeout=10).read()
        return True
    except Exception as e:
        print(f"[alerts] ntfy send failed: {e}")
        return False


def _send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    return _post_json(url, {"chat_id": chat_id, "text": text})


def _dispatch(cfg: Dict[str, Any], title: str, body: str, critical: bool) -> List[str]:
    """Send `title`/`body` to every enabled channel. Returns channels that succeeded."""
    sent = []
    ch = cfg.get("channels", {})
    n = ch.get("ntfy", {})
    if n.get("enabled") and _send_ntfy(n.get("url", ""), title, body, critical):
        sent.append("ntfy")
    t = ch.get("telegram", {})
    if t.get("enabled") and _send_telegram(t.get("token", ""),
                                           t.get("chat_id", ""),
                                           f"{title}\n{body}"):
        sent.append("telegram")
    w = ch.get("webhook", {})
    if w.get("enabled") and w.get("url") and _post_json(
            w["url"], {"title": title, "body": body,
                       "level": "critical" if critical else "warning"}):
        sent.append("webhook")
    return sent


# ---------------------------------------------------------------------------
# Public API used by the watchdog
# ---------------------------------------------------------------------------
def process_health(cfg: Dict[str, Any], health: Dict[str, Any],
                   now: Optional[float] = None) -> Dict[str, Any]:
    """Given the alerts config and a health result, send any due alerts.

    Returns a small summary dict describing what was sent (useful for logs/tests).
    Honours throttling (`min_interval_sec`) and recovery notifications.
    """
    if now is None:
        now = time.monotonic()
    summary = {"alerted": [], "recovered": [], "skipped_throttle": []}
    if not cfg.get("enabled"):
        return summary

    interval = float(cfg.get("min_interval_sec", 1800))
    issues = {i["code"]: i for i in health.get("issues", [])}

    with _lock:
        # 1) Fire alerts for current issues (subject to throttle).
        for code, issue in issues.items():
            last = _last_alert_at.get(code, 0.0)
            already_open = code in _open_alerts
            if already_open and (now - last) < interval:
                summary["skipped_throttle"].append(code)
                continue
            critical = issue["severity"] == "critical"
            prefix = "🔴" if critical else "🟠"
            title = f"{prefix} DLR：{issue['title']}"
            sent = _dispatch(cfg, title, issue["detail"], critical)
            _last_alert_at[code] = now
            _open_alerts.add(code)
            summary["alerted"].append({"code": code, "channels": sent})

        # 2) Fire recovery notices for issues that cleared.
        if cfg.get("notify_on_recovery", True):
            cleared = [c for c in list(_open_alerts) if c not in issues]
            for code in cleared:
                _dispatch(cfg, "✅ DLR：問題已恢復",
                          f"先前的「{code}」狀況已解除，系統恢復正常。",
                          critical=False)
                _open_alerts.discard(code)
                summary["recovered"].append(code)
        else:
            for code in [c for c in list(_open_alerts) if c not in issues]:
                _open_alerts.discard(code)

    return summary


def send_test_alert(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Send a one-off test message to all enabled channels (ignores throttle)."""
    sent = _dispatch(cfg, "🔔 DLR 測試通知",
                     "這是一則測試訊息，代表你的健康警報通知設定正常運作。",
                     critical=False)
    return {"sent": sent}


def reset_state() -> None:
    """Clear throttle/open-alert state (used by tests)."""
    with _lock:
        _last_alert_at.clear()
        _open_alerts.clear()
