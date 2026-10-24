"""Per-streamer aggregates for /api/streamers (pure logic, no IO).

web_ui._per_url_totals() runs one GROUP BY live_url query over
recording_sessions and hands the raw rows here:

    (live_url, finished_total_sec, finished_count, max_start_time)

- finished_total_sec / finished_count only count sessions with end_time set
  (same as before: an in-progress session has no duration yet).
- max_start_time is MAX(start_time) over *all* sessions, including the one
  still recording (end_time IS NULL) -- that one is the most recent recording.

start_time is written by duration_tracker as
datetime.now().isoformat(timespec="seconds") (local time, no offset), so the
SQL MAX() over the TEXT column sorts correctly.
"""
from datetime import datetime

__all__ = ["normalize_iso", "build_url_totals", "PER_URL_TOTALS_SQL"]

PER_URL_TOTALS_SQL = (
    "SELECT live_url, "
    "COALESCE(SUM(CASE WHEN end_time IS NOT NULL THEN duration_sec END), 0), "
    "COALESCE(SUM(CASE WHEN end_time IS NOT NULL THEN 1 ELSE 0 END), 0), "
    "MAX(start_time) "
    "FROM recording_sessions GROUP BY live_url"
)


def normalize_iso(value):
    """Return an ISO-8601 string (seconds precision) or None.

    Accepts the stored TEXT ("2026-10-24T14:30:05", also tolerates a space
    separator or fractional seconds from older rows). Anything unparsable or
    empty becomes None so the API contract stays "ISO string or null".
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).isoformat(timespec="seconds")
    except ValueError:
        return None


def build_url_totals(rows):
    """rows -> {live_url: (total_sec, sessions, last_recorded_at_or_None)}."""
    out = {}
    for row in rows:
        url, total, count, last = (tuple(row) + (None,) * 4)[:4]
        if url is None:
            continue
        out[url] = (int(total or 0), int(count or 0), normalize_iso(last))
    return out
