"""
src/duration_tracker.py — Per-streamer recording duration tracker.

Storage: SQLite at <ROOT>/config/recording_history.db
  - Running from source     : ROOT = parent dir of this file's parent (project root)
  - Running from packaged exe: ROOT = folder containing DouyinLiveRecorder.exe
"""
from __future__ import annotations

import sqlite3
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from . import common


# Resolve project root in a way that works for source AND PyInstaller-frozen exe.
def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


_DB_DEFAULT_PATH = _base_dir() / "config" / "recording_history.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS recording_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    anchor_name     TEXT    NOT NULL,
    platform        TEXT    NOT NULL,
    live_url        TEXT    NOT NULL,
    quality         TEXT,
    start_time      TEXT    NOT NULL,
    end_time        TEXT,
    duration_sec    INTEGER,
    file_path       TEXT,
    finished_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_anchor    ON recording_sessions(anchor_name);
CREATE INDEX IF NOT EXISTS idx_start     ON recording_sessions(start_time);
CREATE INDEX IF NOT EXISTS idx_anchor_start ON recording_sessions(anchor_name, start_time);
"""


@dataclass
class SessionRow:
    id: int
    anchor: str
    platform: str
    url: str
    quality: Optional[str]
    start: datetime
    end: Optional[datetime]
    duration_sec: Optional[int]
    file_path: Optional[str]
    finished_reason: Optional[str]


class DurationTracker:
    def __init__(self, db_path: Path | str = _DB_DEFAULT_PATH, min_keep_seconds: int = 30):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._min_keep = max(0, int(min_keep_seconds))
        self._active: dict[tuple[str, str], int] = {}
        self._init_db()
        self._recover_dirty_rows()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
            con.execute("PRAGMA journal_mode=WAL;")
            try:
                yield con
            finally:
                con.close()

    def _init_db(self) -> None:
        with self._conn() as con:
            con.executescript(_SCHEMA)

    def _recover_dirty_rows(self) -> None:
        with self._conn() as con:
            con.execute(
                "UPDATE recording_sessions "
                "SET end_time = COALESCE(end_time, datetime('now','localtime')), "
                "    duration_sec = COALESCE(duration_sec, "
                "        CAST((julianday(datetime('now','localtime')) - julianday(start_time)) * 86400 AS INTEGER)), "
                "    finished_reason = 'crash' "
                "WHERE end_time IS NULL"
            )

    def session_start(self, anchor: str, platform: str, url: str,
                      quality: str | None = None) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as con:
            cur = con.execute(
                "INSERT INTO recording_sessions "
                "(anchor_name, platform, live_url, quality, start_time) "
                "VALUES (?, ?, ?, ?, ?)",
                (anchor, platform, url, quality, now),
            )
            sid = cur.lastrowid
        self._active[(anchor, url)] = sid
        return sid

    def session_end(self, anchor: str, url: str, file_path: str | None = None,
                    finished_reason: str = "normal") -> None:
        sid = self._active.pop((anchor, url), None)
        end = datetime.now()
        with self._conn() as con:
            if sid is None:
                row = con.execute(
                    "SELECT id, start_time FROM recording_sessions "
                    "WHERE anchor_name=? AND live_url=? AND end_time IS NULL "
                    "ORDER BY id DESC LIMIT 1",
                    (anchor, url),
                ).fetchone()
                if row is None:
                    return
                sid, start_str = row
            else:
                start_str = con.execute(
                    "SELECT start_time FROM recording_sessions WHERE id=?", (sid,)
                ).fetchone()[0]
            start = datetime.fromisoformat(start_str)
            dur = int((end - start).total_seconds())
            if dur < self._min_keep:
                con.execute("DELETE FROM recording_sessions WHERE id=?", (sid,))
                return
            con.execute(
                "UPDATE recording_sessions "
                "SET end_time=?, duration_sec=?, file_path=?, finished_reason=? "
                "WHERE id=?",
                (end.isoformat(timespec="seconds"), dur, file_path, finished_reason, sid),
            )

    def total_by_anchor(self, since: datetime | None = None):
        sql = (
            "SELECT anchor_name, SUM(COALESCE(duration_sec,0)) AS total, COUNT(*) "
            "FROM recording_sessions WHERE end_time IS NOT NULL "
        )
        params: list = []
        if since is not None:
            sql += "AND start_time >= ? "
            params.append(since.isoformat(timespec="seconds"))
        sql += "GROUP BY anchor_name ORDER BY total DESC"
        with self._conn() as con:
            return list(con.execute(sql, params))

    def sessions_for(self, anchor: str, limit: int = 50):
        with self._conn() as con:
            rows = con.execute(
                "SELECT id, anchor_name, platform, live_url, quality, start_time, end_time, "
                "       duration_sec, file_path, finished_reason "
                "FROM recording_sessions WHERE anchor_name=? "
                "ORDER BY start_time DESC LIMIT ?",
                (anchor, limit),
            ).fetchall()
        return [
            SessionRow(
                id=r[0], anchor=r[1], platform=r[2], url=r[3], quality=r[4],
                start=datetime.fromisoformat(r[5]),
                end=datetime.fromisoformat(r[6]) if r[6] else None,
                duration_sec=r[7], file_path=r[8], finished_reason=r[9],
            ) for r in rows
        ]

    def total_seconds(self, anchor: str | None = None,
                      since: datetime | None = None) -> int:
        sql = "SELECT COALESCE(SUM(duration_sec),0) FROM recording_sessions WHERE 1=1"
        params: list = []
        if anchor is not None:
            sql += " AND anchor_name=?"; params.append(anchor)
        if since is not None:
            sql += " AND start_time>=?"; params.append(since.isoformat(timespec="seconds"))
        with self._conn() as con:
            return int(con.execute(sql, params).fetchone()[0])

    def export_csv(self, csv_path: Path | str, since: datetime | None = None) -> Path:
        import csv
        csv_path = Path(csv_path)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        sql = (
            "SELECT anchor_name, platform, live_url, quality, start_time, end_time, "
            "duration_sec, file_path, finished_reason FROM recording_sessions"
        )
        params: list = []
        if since:
            sql += " WHERE start_time>=?"; params.append(since.isoformat(timespec="seconds"))
        sql += " ORDER BY start_time DESC"
        with self._conn() as con, open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["主播", "平台", "直播間 URL", "畫質", "開始", "結束",
                        "時長(秒)", "時長(h:m:s)", "檔案", "結束原因"])
            for r in con.execute(sql, params):
                dur = r[6] or 0
                w.writerow([*r[:6], dur, common.fmt_duration(dur), r[7], r[8]])
        return csv_path


_TRACKER: Optional[DurationTracker] = None


def get_tracker() -> DurationTracker:
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = DurationTracker()
    return _TRACKER
