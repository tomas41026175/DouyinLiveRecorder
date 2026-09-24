"""
src/common.py - Shared pure-stdlib helpers (Stage 5 tech-debt cleanup,
UIUX_SPEC.md §9.1). Zero third-party dependencies.

These four helpers were previously duplicated (byte-for-byte, or near enough)
across alerts.py / compressor.py / rec_notify.py / duration_tracker.py /
query_duration.py. This module is the single source of truth now; the
originals delegate here so behavior is unchanged:

  * deep_merge(base, override)
        <- alerts._deep_merge / compressor._deep_merge / rec_notify._deep_merge
  * load_json_settings(path, defaults) / save_json_settings(path, data, defaults)
        <- alerts.load_alerts/save_alerts, compressor.load_settings/save_settings,
           rec_notify.load_settings/save_settings (all the same deep-merge-onto-
           defaults JSON file pattern)
  * http_post_json(url, payload, proxy=None, ua=None) -> (ok, detail)
        <- alerts._post_json (no UA, always bypasses proxy) and
           rec_notify._post_json (Discord UA, optional explicit proxy, else
           honors system/env proxy). See the `proxy` parameter docs below --
           the two callers need genuinely different proxy behavior, so this
           is a 3-way switch (None / "" / url), not a plain on/off flag.
  * fmt_duration(seconds) -> "H:MM:SS"
        <- duration_tracker.DurationTracker.export_csv()'s inline
           str(timedelta(seconds=dur)) and query_duration.py's _fmt(). These
           two were byte-identical. rec_notify.py's own _fmt_dur() (compact
           "1h02m"/"5m23s" style, used in Discord embeds) and
           danmaku_subtitle.py's _fmt_ass_time()/_fmt_srt_time() (subtitle cue
           clock stamps, "HH:MM:SS.cs"/"HH:MM:SS,ms") are semantically
           different formats for different purposes -- intentionally NOT
           merged into this one, to avoid changing their output.

Upstream files (spider/stream/room/utils/ab_sign/proxy/logger/initializer)
are untouched; nothing here is imported by them.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` onto `base`. Nested dicts are merged
    key-by-key; any other value (including lists) is overwritten wholesale.
    `base` is not mutated; returns a new dict."""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_json_settings(path, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Load a JSON settings file, deep-merged onto `defaults`. Never raises:
    a missing file, unreadable file, invalid JSON, or non-dict payload all
    fall back to a fresh (deep) copy of `defaults`."""
    p = Path(path)
    if not p.exists():
        return json.loads(json.dumps(defaults))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return json.loads(json.dumps(defaults))
        return deep_merge(defaults, data)
    except Exception:
        return json.loads(json.dumps(defaults))


def save_json_settings(path, data: Dict[str, Any], defaults: Dict[str, Any]) -> None:
    """Persist `data` deep-merged onto `defaults` as pretty JSON (UTF-8, no
    ASCII escaping). Raises on write failure -- caller decides how to surface
    it (matches the prior per-module save_settings/save_alerts behavior)."""
    merged = deep_merge(defaults, data or {})
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def http_post_json(url: str, payload: dict, proxy: Optional[str] = "",
                    ua: Optional[str] = None, timeout: int = 10) -> Tuple[bool, str]:
    """POST a JSON payload with urllib. Returns (ok, detail): `detail` carries
    the HTTP status on success, or the real error / HTTP response body on
    failure, so callers can show the user why a send failed.

    proxy:
      * a non-empty string -> explicit proxy URL, used for BOTH http/https
        (rec_notify.py's "proxy_url" setting).
      * ""  (default)      -> honor the system/environment proxy (default
        urllib opener). This is rec_notify.py's behavior when no explicit
        proxy is configured (Discord is only reachable via VPN/proxy in some
        regions, so this must NOT be forced off).
      * None                -> force NO proxy at all (bypass system/env proxy
        entirely). This matches alerts.py's original behavior, which always
        used `build_opener(ProxyHandler({}))`.
    ua: optional User-Agent header. None (default) sends no explicit UA
        (urllib's own default applies) -- matches alerts.py's original
        request, which never set one. Callers that need a specific UA (e.g.
        rec_notify.py's Discord/Cloudflare requirement) pass it explicitly.
    """
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if ua:
            headers["User-Agent"] = ua
        req = urllib.request.Request(url, data=data, headers=headers)
        if proxy is None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        elif proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            opener = urllib.request.build_opener()
        resp = opener.open(req, timeout=timeout)
        code = getattr(resp, "status", None) or resp.getcode()
        resp.read()
        if 200 <= int(code) < 300:
            return True, f"HTTP {code}"
        return False, f"HTTP {code}"
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            body = ""
        return False, f"HTTP {e.code} {body}".strip()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def fmt_duration(seconds) -> str:
    """seconds -> 'H:MM:SS' (i.e. str(timedelta(seconds=...))), e.g.
    3725 -> '1:02:05'. Matches duration_tracker.py's CSV export and
    query_duration.py's CLI output exactly (the two were identical
    one-liners; unified here). Not used by rec_notify.py or
    danmaku_subtitle.py -- see module docstring."""
    return str(timedelta(seconds=int(seconds or 0)))
