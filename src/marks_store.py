"""
marks_store.py - SQLite storage for playback bookmarks ("標記").

使用者需求：回放時可以在目前播放位置加一個標記，可以是單一時間點、也可以是一
個時間段（開始+結束），並附上備註；之後點一下就能直接跳到那個時間點。也要能
跨影片——但限同一場分段錄製 session 內的相鄰片段（同一場直播被切成多個檔案，
本來就是連續時間軸）。

純儲存層，跟 danmaku_store.py 同一套設計（tempfile 可測、標準庫 sqlite3、不
引入第三方依賴）。

Keyed by **session_key**（DB 裡 recording_sessions.file_path 原始值，即
library.resolve_video()/resolve_all_segments() 的 stored_path 參數），而不是
單一片段的 clip_id：同一場分段錄製會展開成好幾個 clip_id（每個真實檔案一
個），如果標記綁在 clip_id 上就沒辦法表達「橫跨兩個片段」的時間段，且壓縮/
改檔名後 clip_id 會變、標記會跟丟。session_key（原始 file_path 樣板字串）在
整場錄製生命週期內不變，時間則存絕對 epoch（start_epoch / 可選 end_epoch），
跟 danmaku 的比對方式一致——由 web_ui.py 呼叫
library.locate_epoch_in_windows() 在讀取時才換算成「屬於哪個 clip_id、片段
內第幾秒」，因此壓縮改檔名不會讓已存的標記失效（跟 danmaku 的 offset 同步邏
輯一樣穩固）。

Schema (config/marks.db):
    marks(
      id           INTEGER PRIMARY KEY AUTOINCREMENT
      session_key  TEXT     NOT NULL  -- recording_sessions.file_path（未 resolve 前的原始值）
      start_epoch  REAL     NOT NULL  -- 標記開始的絕對 unix 時間（秒，可有小數）
      end_epoch    REAL              -- 時間段標記的結束（NULL = 單一時間點）
      note         TEXT
      created_at   INTEGER  NOT NULL
    )
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS marks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT    NOT NULL,
    start_epoch REAL    NOT NULL,
    end_epoch   REAL,
    note        TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_marks_session ON marks(session_key);
"""


class MarksStore:
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

    # -- write -----------------------------------------------------------------
    def add(self, session_key: str, start_epoch: float,
            end_epoch: Optional[float] = None, note: str = "") -> Dict[str, Any]:
        """Insert one mark. Raises ValueError on obviously-bad input (missing
        session_key, or end_epoch <= start_epoch) rather than silently storing
        junk -- the web layer is expected to validate first, but the store
        itself stays safe to call directly (e.g. from tests/tools)."""
        if not session_key:
            raise ValueError("session_key is required")
        start_epoch = float(start_epoch)
        if end_epoch is not None:
            end_epoch = float(end_epoch)
            if end_epoch <= start_epoch:
                raise ValueError("end_epoch must be greater than start_epoch")
        note = (note or "").strip()
        created_at = int(time.time())
        with self._lock, self._conn() as con:
            cur = con.execute(
                "INSERT INTO marks (session_key, start_epoch, end_epoch, note, "
                "created_at) VALUES (?,?,?,?,?)",
                (session_key, start_epoch, end_epoch, note, created_at))
            new_id = cur.lastrowid
        return {"id": new_id, "session_key": session_key, "start_epoch": start_epoch,
                "end_epoch": end_epoch, "note": note, "created_at": created_at}

    # -- read --------------------------------------------------------------------
    def list_for_session(self, session_key: str) -> List[Dict[str, Any]]:
        """All marks for one recording session, ordered by start_epoch ascending."""
        with self._lock, self._conn() as con:
            rows = con.execute(
                "SELECT id, session_key, start_epoch, end_epoch, note, created_at "
                "FROM marks WHERE session_key = ? ORDER BY start_epoch ASC, id ASC",
                (session_key,)).fetchall()
        cols = ["id", "session_key", "start_epoch", "end_epoch", "note", "created_at"]
        return [dict(zip(cols, r)) for r in rows]

    # -- update / delete -----------------------------------------------------------
    def update_note(self, mark_id: int, note: str) -> bool:
        """Edit a mark's note text. Returns True if a row was changed."""
        with self._lock, self._conn() as con:
            cur = con.execute("UPDATE marks SET note = ? WHERE id = ?",
                              ((note or "").strip(), int(mark_id)))
            return cur.rowcount > 0

    def delete(self, mark_id: int) -> bool:
        """Remove one mark by id. Returns True if a row was actually removed."""
        with self._lock, self._conn() as con:
            cur = con.execute("DELETE FROM marks WHERE id = ?", (int(mark_id),))
            return cur.rowcount > 0

    def delete_for_session(self, session_key: str) -> int:
        """Remove all marks for one session (housekeeping/tests; not currently
        wired to any endpoint). Returns rows removed."""
        with self._lock, self._conn() as con:
            cur = con.execute("DELETE FROM marks WHERE session_key = ?", (session_key,))
            return cur.rowcount


_STORE: Optional[MarksStore] = None
_store_lock = threading.Lock()


def get_store(db_path) -> MarksStore:
    global _STORE
    with _store_lock:
        if _STORE is None or str(_STORE.db_path) != str(db_path):
            _STORE = MarksStore(db_path)
    return _STORE
