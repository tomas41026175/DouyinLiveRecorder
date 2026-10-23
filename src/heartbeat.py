"""Healthchecks.io heartbeat for the Web UI watchdog.

Why this exists: alerts.py only fires when *this machine* notices a problem.
If the whole PC is off / asleep / the web_ui process died, nothing local is
left to complain. A heartbeat flips that around: web_ui pings an external
service every few minutes, and the *external* service alerts when the pings
stop. That is the only way "the Windows box is down" ever gets noticed.

Design (AGENT.md §5):
- Default OFF (`enabled: False`, empty url). Nothing is sent until the user
  fills config/healthchecks.json -> merging this changes no behavior.
- Pure decision logic (`is_due`, `build_ping`, `ping_url`) is separate from
  the single IO function (`send`) so it is unit-testable without network.
- stdlib only (urllib). Sends a User-Agent (Cloudflare-fronted endpoints
  reject requests without one). Bypasses system proxy like alerts.py.
- Never raises: a broken monitor must not break the watchdog loop.

Mapping from health status to ping:
- "critical" (recorder down, DB broken, disk almost full) -> /fail
- "ok" / "warn"                                           -> success
  (warn = still recording, just worth a look; the issue list is attached
   as the ping body so it shows up in the Healthchecks event log)
"""
from __future__ import annotations

import urllib.request
from typing import Any, Dict, Optional, Tuple

from . import common as _common

DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": False,
    "url": "",              # e.g. https://hc-ping.com/<uuid>
    "interval_sec": 300,    # ping every 5 min; set Healthchecks period=5m, grace=10m
}

USER_AGENT = "DouyinLiveRecorder-heartbeat/1.0"
MAX_BODY_BYTES = 10_000
_MIN_INTERVAL_SEC = 60      # watchdog ticks every 60s; faster is meaningless


def load_settings(path) -> Dict[str, Any]:
    return _common.load_json_settings(path, DEFAULT_SETTINGS)


def is_enabled(cfg: Dict[str, Any]) -> bool:
    return bool(cfg.get("enabled")) and bool(str(cfg.get("url", "")).strip())


def is_due(cfg: Dict[str, Any], last_sent: Optional[float], now: float) -> bool:
    """True when enabled and at least `interval_sec` passed since the last ping.
    `last_sent=None` means never pinged in this process -> due immediately."""
    if not is_enabled(cfg):
        return False
    if last_sent is None:
        return True
    try:
        interval = max(float(cfg.get("interval_sec", 300)), _MIN_INTERVAL_SEC)
    except (TypeError, ValueError):
        interval = float(DEFAULT_SETTINGS["interval_sec"])
    return (now - last_sent) >= interval


def build_ping(health: Dict[str, Any]) -> Tuple[str, str]:
    """Map a health result (health.evaluate_health shape) to (kind, body).
    kind is "success" or "fail"; body lists issues (empty when all ok)."""
    issues = health.get("issues") or []
    lines = [f"[{i.get('severity', '?')}] {i.get('title', i.get('code', '?'))}: {i.get('detail', '')}"
             for i in issues]
    body = "\n".join(lines)
    kind = "fail" if health.get("status") == "critical" else "success"
    return kind, body


def ping_url(base: str, kind: str) -> str:
    if kind not in ("success", "fail", "start"):
        raise ValueError(f"unknown ping kind: {kind}")
    base = base.strip().rstrip("/")
    return base if kind == "success" else f"{base}/{kind}"


def send(base: str, kind: str, body: str = "", timeout: int = 10) -> Tuple[bool, str]:
    """POST the ping. Returns (ok, detail) where detail is the concrete
    failure reason (e.g. 'HTTP 404', 'timed out') per AGENT.md §5 rule 3."""
    try:
        url = ping_url(base, kind)
    except ValueError as e:
        return False, str(e)
    data = body.encode("utf-8")[-MAX_BODY_BYTES:] if body else b""
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "text/plain; charset=utf-8",
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return True, f"HTTP {resp.status}"
            return False, f"HTTP {resp.status}"
    except Exception as e:  # noqa: BLE001 -- monitor must never break the watchdog
        return False, f"{type(e).__name__}: {e}"
