"""Daily disk-space history + a linear-trend ETA for "when will free space
drop below the critical threshold". Pure logic (no filesystem access beyond
the two explicit load/save helpers at the bottom, which the web layer calls
from a single background thread so writes never race).

Storage shape: config/disk_history.json is a plain list of
    {"date": "YYYY-MM-DD", "free_bytes": int, "total_bytes": int|None}
one entry per calendar day (recording a new sample for "today" replaces
that day's entry rather than appending a duplicate), capped to the most
recent `max_days` entries.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_MAX_DAYS = 180
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_MIN_POINTS = 3


def record_sample(history: List[Dict[str, Any]], date_str: str,
                  free_bytes: Optional[int], total_bytes: Optional[int],
                  max_days: int = DEFAULT_MAX_DAYS) -> List[Dict[str, Any]]:
    """Return a NEW list with `date_str`'s entry set to this sample (replacing
    any existing entry for that same day, so repeated calls within one day
    don't pile up duplicates), sorted by date and capped to `max_days`."""
    if free_bytes is None:
        return list(history)
    out = [s for s in history if s.get("date") != date_str]
    out.append({"date": date_str, "free_bytes": int(free_bytes),
                "total_bytes": int(total_bytes) if total_bytes is not None else None})
    out.sort(key=lambda s: s["date"])
    if len(out) > max_days:
        out = out[-max_days:]
    return out


def estimate_low_space_eta(history: List[Dict[str, Any]],
                           critical_bytes: Optional[int],
                           lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                           min_points: int = DEFAULT_MIN_POINTS) -> Dict[str, Any]:
    """Fit a straight line through the last `lookback_days` daily samples'
    free_bytes and project forward to when it crosses `critical_bytes`.

    Returns {"available": bool, "days_left": float|None, "eta_date": str|None,
    "daily_change_bytes": float|None, "reason": str}. `daily_change_bytes` is
    negative while space is shrinking (bytes lost per day); a non-negative
    value (usage stable or a cleanup/new drive made it jump up) means there is
    no meaningful ETA to give, which is a normal, expected outcome, not an
    error.
    """
    if critical_bytes is None:
        return {"available": False, "days_left": None, "eta_date": None,
                "daily_change_bytes": None, "reason": "未設定告急門檻"}

    points = sorted(
        (s for s in history if s.get("free_bytes") is not None and s.get("date")),
        key=lambda s: s["date"],
    )
    if lookback_days:
        points = points[-lookback_days:]
    if len(points) < min_points:
        return {"available": False, "days_left": None, "eta_date": None,
                "daily_change_bytes": None,
                "reason": f"資料不足（需要至少 {min_points} 天的每日紀錄）"}

    base_date = datetime.strptime(points[0]["date"], "%Y-%m-%d")
    xs = [(datetime.strptime(p["date"], "%Y-%m-%d") - base_date).days for p in points]
    ys = [p["free_bytes"] for p in points]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return {"available": False, "days_left": None, "eta_date": None,
                "daily_change_bytes": None, "reason": "資料點時間間隔太短，無法估算趨勢"}

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom  # bytes/day

    latest = points[-1]
    latest_free = latest["free_bytes"]

    if latest_free <= critical_bytes:
        return {"available": True, "days_left": 0.0, "eta_date": latest["date"],
                "daily_change_bytes": slope, "reason": "已低於告急門檻"}

    if slope >= 0:
        return {"available": False, "days_left": None, "eta_date": None,
                "daily_change_bytes": slope, "reason": "近期空間使用沒有下降趨勢，暫無法推估"}

    days_left = (latest_free - critical_bytes) / (-slope)
    eta_date = datetime.strptime(latest["date"], "%Y-%m-%d") + timedelta(days=days_left)
    return {
        "available": True,
        "days_left": round(days_left, 1),
        "eta_date": eta_date.strftime("%Y-%m-%d"),
        "daily_change_bytes": slope,
        "reason": f"依過去 {n} 天趨勢推估",
    }


# ---------------------------------------------------------------------------
# IO (best-effort, never raises) -- called from web_ui.py's watchdog thread
# ---------------------------------------------------------------------------
def load_history(path) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        return []


def save_history(path, history: List[Dict[str, Any]]) -> None:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
