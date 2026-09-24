"""
danmaku_store.py - SQLite storage + query for live-chat danmaku (弹幕).

Pure storage layer, no network. The capture layer (danmaku_capture.py) writes
rows in via record(); the web UI and CLI read them back through the query
helpers. Kept dependency-free and unit-testable.

Schema (config/danmaku.db):
    danmaku(
      id, streamer_url, anchor_name, platform,
      ts        INTEGER  -- unix seconds when the message was received
      user      TEXT     -- sender nickname (may be empty)
      content   TEXT     -- message text
      msg_type  TEXT     -- 'chat' | 'gift' | 'member' | 'like' | ...
    )
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS danmaku (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    streamer_url TEXT    NOT NULL,
    anchor_name  TEXT,
    platform     TEXT,
    ts           INTEGER NOT NULL,
    user         TEXT,
    content      TEXT,
    msg_type     TEXT DEFAULT 'chat'
);
CREATE INDEX IF NOT EXISTS idx_dm_url     ON danmaku(streamer_url);
CREATE INDEX IF NOT EXISTS idx_dm_anchor  ON danmaku(anchor_name);
CREATE INDEX IF NOT EXISTS idx_dm_ts      ON danmaku(ts);
CREATE INDEX IF NOT EXISTS idx_dm_anchor_ts ON danmaku(anchor_name, ts);
"""


class DanmakuStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self):
        con = sqlite3.connect(self.db_path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL;")
        return con

    def _init_db(self):
        with self._lock, self._conn() as con:
            con.executescript(_SCHEMA)

    # -- write -------------------------------------------------------------
    def record(self, streamer_url: str, content: str, user: str = "",
               anchor_name: str = "", platform: str = "", msg_type: str = "chat",
               ts: Optional[int] = None) -> None:
        """Insert one message. Empty content is ignored."""
        if not content:
            return
        ts = int(ts if ts is not None else time.time())
        with self._lock, self._conn() as con:
            con.execute(
                "INSERT INTO danmaku (streamer_url, anchor_name, platform, ts, "
                "user, content, msg_type) VALUES (?,?,?,?,?,?,?)",
                (streamer_url, anchor_name, platform, ts, user, content, msg_type))

    def record_many(self, rows: List[Dict[str, Any]]) -> int:
        """Batch insert. Each row: {streamer_url, content, user?, anchor_name?,
        platform?, msg_type?, ts?}. Returns count inserted."""
        vals = []
        now = int(time.time())
        for r in rows or []:
            if not r.get("content"):
                continue
            vals.append((r.get("streamer_url", ""), r.get("anchor_name", ""),
                         r.get("platform", ""), int(r.get("ts", now)),
                         r.get("user", ""), r.get("content", ""),
                         r.get("msg_type", "chat")))
        if not vals:
            return 0
        with self._lock, self._conn() as con:
            con.executemany(
                "INSERT INTO danmaku (streamer_url, anchor_name, platform, ts, "
                "user, content, msg_type) VALUES (?,?,?,?,?,?,?)", vals)
        return len(vals)

    # -- read --------------------------------------------------------------
    def query(self, anchor: Optional[str] = None, url: Optional[str] = None,
              keyword: Optional[str] = None, since: Optional[int] = None,
              until: Optional[int] = None, user: Optional[str] = None,
              limit: int = 200, offset: int = 0,
              order: str = "desc") -> List[Dict[str, Any]]:
        sql = ("SELECT id, streamer_url, anchor_name, platform, ts, user, "
               "content, msg_type FROM danmaku WHERE 1=1")
        params: list = []
        if anchor:
            sql += " AND anchor_name = ?"; params.append(anchor)
        if url:
            sql += " AND streamer_url = ?"; params.append(url)
        if keyword:
            sql += " AND content LIKE ?"; params.append(f"%{keyword}%")
        if user:
            sql += " AND user LIKE ?"; params.append(f"%{user}%")
        if since is not None:
            sql += " AND ts >= ?"; params.append(int(since))
        if until is not None:
            sql += " AND ts <= ?"; params.append(int(until))
        sql += f" ORDER BY ts {'ASC' if order == 'asc' else 'DESC'}, id {'ASC' if order == 'asc' else 'DESC'}"
        sql += " LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 5000)), max(0, int(offset))])
        with self._lock, self._conn() as con:
            rows = con.execute(sql, params).fetchall()
        cols = ["id", "streamer_url", "anchor_name", "platform", "ts", "user",
                "content", "msg_type"]
        return [dict(zip(cols, r)) for r in rows]

    def count(self, anchor: Optional[str] = None, url: Optional[str] = None,
              keyword: Optional[str] = None, since: Optional[int] = None,
              until: Optional[int] = None, user: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) FROM danmaku WHERE 1=1"
        params: list = []
        if anchor:
            sql += " AND anchor_name = ?"; params.append(anchor)
        if url:
            sql += " AND streamer_url = ?"; params.append(url)
        if keyword:
            sql += " AND content LIKE ?"; params.append(f"%{keyword}%")
        if user:
            sql += " AND user LIKE ?"; params.append(f"%{user}%")
        if since is not None:
            sql += " AND ts >= ?"; params.append(int(since))
        if until is not None:
            sql += " AND ts <= ?"; params.append(int(until))
        with self._lock, self._conn() as con:
            return int(con.execute(sql, params).fetchone()[0])

    def anchors(self) -> List[Dict[str, Any]]:
        """Distinct anchors with message counts and last-seen ts, most recent first."""
        with self._lock, self._conn() as con:
            rows = con.execute(
                "SELECT anchor_name, streamer_url, COUNT(*) AS n, MAX(ts) AS last "
                "FROM danmaku GROUP BY anchor_name, streamer_url "
                "ORDER BY last DESC").fetchall()
        return [{"anchor_name": r[0], "streamer_url": r[1],
                 "count": int(r[2]), "last_ts": int(r[3] or 0)} for r in rows]

    def total(self) -> int:
        with self._lock, self._conn() as con:
            return int(con.execute("SELECT COUNT(*) FROM danmaku").fetchone()[0])

    def export_csv(self, csv_path, **filters) -> Path:
        import csv
        from datetime import datetime
        csv_path = Path(csv_path)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        # pull all matching rows (cap high) oldest-first for readability
        rows = self.query(limit=5000, order="asc",
                          **{k: v for k, v in filters.items()
                             if k in ("anchor", "url", "keyword", "since",
                                      "until", "user")})
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["時間", "主播", "平台", "使用者", "內容", "類型"])
            for r in rows:
                t = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
                w.writerow([t, r["anchor_name"], r["platform"], r["user"],
                            r["content"], r["msg_type"]])
        return csv_path

    def prune(self, older_than_days: int) -> int:
        """Delete messages older than N days. Returns rows removed."""
        cutoff = int(time.time()) - int(older_than_days) * 86400
        with self._lock, self._conn() as con:
            cur = con.execute("DELETE FROM danmaku WHERE ts < ?", (cutoff,))
            return cur.rowcount

    def delete_content_matching(self, needle: str) -> int:
        """Delete rows whose content contains `needle` (plain substring, not a
        LIKE pattern -- % and _ in `needle` are treated literally). One-off
        cleanup helper: a protobuf field-numbering bug in danmaku_capture.py
        used to store Douyin's internal 'internal_src:pushserver|...' protocol
        metadata as if it were chat content (see AGENT.md). Returns rows
        removed."""
        if not needle:
            return 0
        with self._lock, self._conn() as con:
            cur = con.execute(
                "DELETE FROM danmaku WHERE content LIKE '%' || ? || '%' ESCAPE '\\'",
                (needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"),))
            return cur.rowcount


_STORE: Optional[DanmakuStore] = None
_store_lock = threading.Lock()


def get_store(db_path) -> DanmakuStore:
    global _STORE
    with _store_lock:
        if _STORE is None or str(_STORE.db_path) != str(db_path):
            _STORE = DanmakuStore(db_path)
    return _STORE
