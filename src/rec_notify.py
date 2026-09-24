"""
rec_notify.py - Recording start/stop notifications to Discord (and generic
webhook), driven by web_ui's session poll — so it works WITHOUT rebuilding the
recorder exe.

How it works: web_ui already knows the set of currently-recording streamers via
active_sessions(). We diff that set against the previous poll:
  * a URL that appeared  -> "開始錄製" notification
  * a URL that vanished  -> "結束錄製" notification (with duration if known)

Discord is the primary target (incoming webhook URL), configurable from the
settings page. A generic webhook is also supported. Pure-ish: the sender is
best-effort and never raises into the caller.

Config: config/rec_notify.json
{
  "enabled": false,
  "notify_start": true,
  "notify_stop": true,
  "discord": {"enabled": false, "webhook_url": ""},
  "webhook":  {"enabled": false, "url": ""}
}
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from . import common as _common

DEFAULT_REC_NOTIFY = {
    "enabled": False,
    "notify_start": True,
    "notify_stop": True,
    "proxy_url": "",    # e.g. http://127.0.0.1:7890 ; empty = system/env proxy
    "discord": {"enabled": False, "webhook_url": ""},
    "webhook": {"enabled": False, "url": ""},
}

_lock = threading.Lock()
_prev_active: Dict[str, Dict[str, Any]] = {}   # url -> session info from last poll
_started = False
_last_error: Optional[str] = None
_sent_count = 0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Thin alias, not a re-implementation (Stage 5 tech-debt cleanup, UIUX_SPEC.md
# §9.1) -- kept so any internal caller of `_deep_merge(...)` keeps working.
_deep_merge = _common.deep_merge


def load_settings(path) -> Dict[str, Any]:
    return _common.load_json_settings(path, DEFAULT_REC_NOTIFY)


def save_settings(path, data: Dict[str, Any]) -> None:
    _common.save_json_settings(path, data, DEFAULT_REC_NOTIFY)


# ---------------------------------------------------------------------------
# Senders (best-effort, never raise)
# ---------------------------------------------------------------------------
# Optional explicit proxy for outbound notifications (e.g. "http://127.0.0.1:7890").
# Empty string -> honor the system/env proxy (Windows proxy, HTTP(S)_PROXY).
# Discord is blocked in some regions, so notifications may need a proxy even
# though local recording does not.
_proxy_url = ""


def set_proxy(url: str) -> None:
    global _proxy_url
    _proxy_url = (url or "").strip()


# A User-Agent is REQUIRED: Discord sits behind Cloudflare, which returns
# HTTP 403 "error code: 1010" for requests without a proper UA (urllib's
# default has none / a Python one). Send a normal browser-ish UA.
_DISCORD_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "DouyinLiveRecorder-Notifier/1.0")


def _post_json(url: str, payload: dict, timeout: int = 10):
    """POST JSON. Returns (ok: bool, detail: str). detail carries the real error
    (or HTTP status) so the UI can show why a send failed."""
    global _last_error
    # proxy="" -> honor system/env proxy when no explicit proxy_url is set
    # (Discord is only reachable via VPN/proxy in some regions); a non-empty
    # _proxy_url is used explicitly for both http/https. See
    # common.http_post_json docstring for the full 3-way proxy contract.
    ok, detail = _common.http_post_json(
        url, payload, proxy=(_proxy_url or ""), ua=_DISCORD_UA, timeout=timeout)
    if not ok:
        _last_error = detail
    return ok, detail


def send_discord(webhook_url: str, content: str):
    """Discord incoming webhook: {"content": "..."}. Truncates to 2000 chars.
    Returns (ok, detail)."""
    if not webhook_url:
        return False, "webhook URL is empty"
    return _post_json(webhook_url, {"content": content[:2000]})


def send_discord_embed(webhook_url: str, embed: Dict[str, Any]):
    """Send a rich embed card. Returns (ok, detail)."""
    if not webhook_url:
        return False, "webhook URL is empty"
    return _post_json(webhook_url, {"embeds": [embed]})


def _dispatch(cfg: Dict[str, Any], content: str,
              embed: Optional[Dict[str, Any]] = None):
    """Send to every enabled channel. Discord uses the embed card when given
    (falls back to plain text); the generic webhook always gets plain text +
    the raw embed dict. Returns (sent_channels, detail_map)."""
    # Honor an explicit proxy from settings for this dispatch, if provided.
    set_proxy(cfg.get("proxy_url", ""))
    sent, details = [], {}
    d = cfg.get("discord", {})
    if d.get("enabled"):
        if embed is not None:
            ok, detail = send_discord_embed(d.get("webhook_url", ""), embed)
        else:
            ok, detail = send_discord(d.get("webhook_url", ""), content)
        details["discord"] = detail
        if ok:
            sent.append("discord")
    w = cfg.get("webhook", {})
    if w.get("enabled") and w.get("url"):
        payload = {"content": content, "event": "recording"}
        if embed is not None:
            payload["embed"] = embed
        ok, detail = _post_json(w["url"], payload)
        details["webhook"] = detail
        if ok:
            sent.append("webhook")
    return sent, details


def send_test(cfg: Dict[str, Any]) -> Dict[str, Any]:
    test_embed = {
        "title": "🔔 DLR 測試通知",
        "description": "錄製通知設定正常運作。",
        "color": 0x3498DB,
        "timestamp": _now_iso(),
        "footer": {"text": "DouyinLiveRecorder"},
    }
    sent, details = _dispatch(cfg, "🔔 DLR 測試通知：錄製通知設定正常運作。",
                              embed=test_embed)
    return {"sent": sent, "details": details}


def send_current_status(cfg: Dict[str, Any],
                        active: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Send a one-off card describing what is being recorded RIGHT NOW.
    Uses the real active_sessions list, so it doubles as a live test."""
    active = active or []
    if active:
        # reuse the batch start-card layout: each line = time + name + link
        lines = [_line_start(s) for s in active]
        desc = "\n".join(lines)
        if len(desc) > _EMBED_DESC_LIMIT:
            desc = desc[:_EMBED_DESC_LIMIT] + "\n…"
        embed = {
            "title": f"📋 目前錄製狀態（{len(active)} 個直播中）",
            "description": desc,
            "color": 0x2ECC71,   # green
            "timestamp": _now_iso(),
            "footer": {"text": "DouyinLiveRecorder · 手動狀態回報"},
        }
        text = "📋 目前錄製狀態\n" + "\n".join(
            _line_start(s).replace("**", "") for s in active)
    else:
        embed = {
            "title": "📋 目前錄製狀態",
            "description": "目前沒有正在錄製的直播。",
            "color": 0x95A5A6,   # grey
            "timestamp": _now_iso(),
            "footer": {"text": "DouyinLiveRecorder · 手動狀態回報"},
        }
        text = "📋 目前錄製狀態：目前沒有正在錄製的直播。"
    sent, details = _dispatch(cfg, text, embed=embed)
    return {"sent": sent, "details": details, "count": len(active)}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------
def _fmt_dur(sec: Optional[int]) -> str:
    try:
        sec = int(sec or 0)
    except Exception:
        return ""
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat()


def _fmt_clock(start_iso: str) -> str:
    """ISO start_time -> 'HH:MM' (or 'MM-DD HH:MM' if not today). '' on failure."""
    if not start_iso:
        return ""
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(start_iso)
    except Exception:
        return ""
    today = datetime.now().date()
    return dt.strftime("%H:%M") if dt.date() == today else dt.strftime("%m-%d %H:%M")


# --- Discord embed cards ----------------------------------------------------
_COLOR_START = 0xED4245   # red (live)
_COLOR_STOP = 0x95A5A6    # grey (ended)


# --- batched embeds: one card listing all streamers of the same event -------
# Avoids notification spam when several streamers start/stop in the same poll.
_EMBED_DESC_LIMIT = 3800   # Discord embed description hard cap is 4096


def _line_start(s: Dict[str, Any]) -> str:
    name = s.get("anchor_name") or s.get("live_url") or "?"
    url = s.get("live_url") or ""
    clock = _fmt_clock(s.get("start_time", ""))
    prefix = f"`{clock}` " if clock else ""
    return (f"• {prefix}**{name}** — [直播間]({url})" if url
            else f"• {prefix}**{name}**")


def _line_stop(s: Dict[str, Any]) -> str:
    name = s.get("anchor_name") or s.get("live_url") or "?"
    clock = _fmt_clock(s.get("start_time", ""))
    dur = _fmt_dur(s.get("elapsed_sec"))
    bits = []
    if clock:
        bits.append(f"{clock} 開始")
    if dur and dur != "0s":
        bits.append(dur)
    tail = f"（{' · '.join(bits)}）" if bits else ""
    return f"• **{name}**{tail}"


def build_batch_embed(sessions: List[Dict[str, Any]], kind: str) -> Dict[str, Any]:
    """One embed card listing every streamer in `sessions` for a single event.
    kind = 'start' or 'stop'. Single streamer still renders fine (one line)."""
    if kind == "start":
        title, color, liner = "🔴 開始錄製", _COLOR_START, _line_start
    else:
        title, color, liner = "⏹ 結束錄製", _COLOR_STOP, _line_stop
    lines = [liner(s) for s in sessions]
    desc = "\n".join(lines)
    if len(desc) > _EMBED_DESC_LIMIT:      # very long batch -> trim with a note
        kept, total = [], 0
        for ln in lines:
            if total + len(ln) + 1 > _EMBED_DESC_LIMIT:
                kept.append(f"…等共 {len(lines)} 位")
                break
            kept.append(ln); total += len(ln) + 1
        desc = "\n".join(kept)
    n = len(sessions)
    return {
        "title": f"{title}（{n}）" if n > 1 else title,
        "description": desc,
        "color": color,
        "timestamp": _now_iso(),
        "footer": {"text": "DouyinLiveRecorder"},
    }


def format_batch_text(sessions: List[Dict[str, Any]], kind: str) -> str:
    """Plain-text version for the generic webhook fallback."""
    head = "🔴 開始錄製" if kind == "start" else "⏹ 結束錄製"
    liner = _line_start if kind == "start" else _line_stop
    body = "\n".join(liner(s).replace("**", "") for s in sessions)
    return f"{head}\n{body}"


# ---------------------------------------------------------------------------
# Transition detection (called each poll by web_ui)
# ---------------------------------------------------------------------------
def process_active(cfg: Dict[str, Any],
                   active: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Diff current active recordings vs the previous poll and fire start/stop
    notifications. Returns a summary. Safe to call frequently.

    active: list of session dicts (need at least live_url; anchor_name/
            elapsed_sec used for nicer messages).
    """
    global _prev_active, _sent_count
    summary = {"started": [], "stopped": [], "sent": []}
    cur = {s.get("live_url"): s for s in (active or []) if s.get("live_url")}
    with _lock:
        prev = dict(_prev_active)
        _prev_active = cur

    if not cfg.get("enabled"):
        return summary  # still updated prev so we don't flood on first enable

    # Collect this poll's transitions, then send ONE combined card per event
    # type (a single card listing everyone) to avoid notification spam when
    # several streamers start/stop in the same poll.
    started = [s for url, s in cur.items() if url not in prev]
    stopped = [s for url, s in prev.items() if url not in cur]

    if started and cfg.get("notify_start", True):
        sent, _ = _dispatch(cfg, format_batch_text(started, "start"),
                            embed=build_batch_embed(started, "start"))
        summary["started"] = [s.get("live_url") for s in started]
        if sent:
            _sent_count += 1
            summary["sent"].extend(sent)

    if stopped and cfg.get("notify_stop", True):
        sent, _ = _dispatch(cfg, format_batch_text(stopped, "stop"),
                            embed=build_batch_embed(stopped, "stop"))
        summary["stopped"] = [s.get("live_url") for s in stopped]
        if sent:
            _sent_count += 1
            summary["sent"].extend(sent)
    return summary


def prime(active: List[Dict[str, Any]]) -> None:
    """Seed the previous-active set WITHOUT sending anything (call once at
    startup so already-in-progress recordings don't all fire as 'started')."""
    global _prev_active
    with _lock:
        _prev_active = {s.get("live_url"): s for s in (active or [])
                        if s.get("live_url")}


def get_status(path) -> Dict[str, Any]:
    cfg = load_settings(path)
    with _lock:
        active_n = len(_prev_active)
    return {"settings": cfg, "active_tracked": active_n,
            "sent_count": _sent_count, "last_error": _last_error}


def reset_state() -> None:
    global _prev_active, _sent_count, _last_error
    with _lock:
        _prev_active = {}
    _sent_count = 0
    _last_error = None
