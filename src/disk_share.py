"""Per-streamer disk consumption: share of total, recent daily rate, and a
"what if I change this streamer's per-session limit" estimate.

Pure logic (no filesystem / DB access) so it is unit-testable; the web layer
gathers the file lists and session rows and passes them in.

Attribution of a file to a day uses the timestamp embedded in the recorder's
file name (``..._YYYY-MM-DD_HH-MM-SS_NNN[.ext]``) when present, because file
mtime is unreliable here (the H.265 compressor rewrites files long after they
were recorded, which would make old footage look "recent"). mtime is only the
fallback for names without that stamp.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_WINDOW_DAYS = 7

_STAMP_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})")


def file_day(name_or_path: str, mtime: Optional[float]) -> Optional[datetime]:
    """Recording start time encoded in the file name, else mtime, else None."""
    base = os.path.basename(name_or_path or "")
    m = _STAMP_RE.search(base)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H-%M-%S")
        except ValueError:
            pass
    if mtime is not None:
        try:
            return datetime.fromtimestamp(mtime)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def recent_bytes(files: Iterable[Dict[str, Any]], now: datetime,
                 window_days: int = DEFAULT_WINDOW_DAYS) -> int:
    """Sum of sizes of files recorded within the last `window_days`."""
    cutoff = now - timedelta(days=window_days)
    total = 0
    for f in files:
        t = file_day(f.get("path", ""), f.get("mtime"))
        if t is not None and t >= cutoff:
            total += int(f.get("size") or 0)
    return total


def estimate_daily_bytes_with_limit(durations_sec: Iterable[float],
                                    bytes_per_min: Optional[float],
                                    window_days: int,
                                    limit_min: Optional[int]) -> Optional[float]:
    """Estimated bytes/day if every session in the window had been capped at
    `limit_min` minutes (None/0 = unlimited). Returns None when there is no
    usable bytes-per-minute figure."""
    if bytes_per_min is None or window_days <= 0:
        return None
    cap = limit_min * 60 if limit_min and limit_min > 0 else None
    secs = 0.0
    for d in durations_sec:
        d = float(d or 0)
        secs += min(d, cap) if cap is not None else d
    return (secs / 60.0) * bytes_per_min / window_days


def compute_usage(streamers: List[Dict[str, Any]],
                  sessions: List[Dict[str, Any]],
                  now: datetime,
                  window_days: int = DEFAULT_WINDOW_DAYS) -> Dict[str, Any]:
    """
    streamers: [{"url", "files": [{"path","size","mtime"}]}]
    sessions : [{"url", "duration_sec"}]   (finished sessions inside the window)

    Returns {"window_days", "total_bytes", "recent_total_bytes",
             "per_url": {url: {...}}}.
    per_url fields: total_bytes, share_pct (of all-time total), recent_bytes,
    bytes_per_day, recent_share_pct (of recent total), recorded_min_per_day,
    sessions_per_day, bytes_per_min (None if no recorded time), durations_sec.
    """
    durations: Dict[str, List[float]] = {}
    for s in sessions:
        durations.setdefault(s.get("url"), []).append(float(s.get("duration_sec") or 0))

    rows: Dict[str, Dict[str, Any]] = {}
    total = 0
    recent_total = 0
    for st in streamers:
        url = st.get("url")
        files = st.get("files") or []
        tb = sum(int(f.get("size") or 0) for f in files)
        rb = recent_bytes(files, now, window_days)
        ds = durations.get(url, [])
        rec_min = sum(ds) / 60.0
        rows[url] = {
            "total_bytes": tb,
            "recent_bytes": rb,
            "bytes_per_day": rb / window_days if window_days else 0.0,
            "recorded_min_per_day": rec_min / window_days if window_days else 0.0,
            "sessions_per_day": len(ds) / window_days if window_days else 0.0,
            "bytes_per_min": (rb / rec_min) if rec_min > 0 and rb > 0 else None,
            "durations_sec": ds,
        }
        total += tb
        recent_total += rb

    for r in rows.values():
        r["share_pct"] = round(r["total_bytes"] * 100.0 / total, 1) if total else 0.0
        r["recent_share_pct"] = (round(r["recent_bytes"] * 100.0 / recent_total, 1)
                                 if recent_total else 0.0)

    return {"window_days": window_days, "total_bytes": total,
            "recent_total_bytes": recent_total,
            "total_bytes_per_day": recent_total / window_days if window_days else 0.0,
            "per_url": rows}
