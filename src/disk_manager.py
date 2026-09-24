"""
disk_manager.py - Disk space auto-management for DouyinLiveRecorder.

Two responsibilities, kept as PURE logic (no real deletion happens here; the
caller decides whether to act):

  1. plan_pause()   - decide whether new recordings should be paused because the
                      drive is critically low (root cause of the "recorded until
                      the disk was 100% full" incident).
  2. plan_cleanup() - given a retention policy and a list of recording files,
                      decide which oldest files to delete to get back under a
                      target free-space level. DISABLED by default; the user must
                      explicitly opt in, and we never touch a file that is being
                      written right now.

Keeping this pure means it is fully unit-testable and the destructive step
(actually unlinking files) is a thin, auditable wrapper in the web layer.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

GB = 1024 ** 3

DEFAULT_DISK_POLICY = {
    "pause_below_gb": 2,        # pause NEW recordings when free < this
    "resume_above_gb": 5,      # allow recording again once free >= this (hysteresis)
    "cleanup_enabled": False,  # opt-in destructive cleanup
    "cleanup_target_gb": 10,   # after cleanup, aim for at least this much free
    "keep_days": 0,            # 0 = no age rule; >0 = always keep files newer than N days
    "protect_active": True,    # never delete a file currently being recorded
}


def _gb(n: Optional[float]) -> str:
    try:
        return f"{n / GB:.1f}GB"
    except Exception:
        return "?"


def merge_policy(policy: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(DEFAULT_DISK_POLICY)
    for k, v in (policy or {}).items():
        if k in out and v is not None:
            out[k] = v
    return out


def plan_pause(free_bytes: Optional[int],
               currently_paused: bool,
               policy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Decide the pause/resume action using hysteresis to avoid flapping.

    Returns {"action": "pause"|"resume"|"none", "should_pause": bool, "reason": str}.
    """
    p = merge_policy(policy)
    if free_bytes is None:
        return {"action": "none", "should_pause": currently_paused,
                "reason": "free space unknown"}
    pause_below = p["pause_below_gb"] * GB
    resume_above = p["resume_above_gb"] * GB
    if not currently_paused and free_bytes < pause_below:
        return {"action": "pause", "should_pause": True,
                "reason": f"剩餘 {_gb(free_bytes)} < {p['pause_below_gb']:g}GB，暫停新錄製"}
    if currently_paused and free_bytes >= resume_above:
        return {"action": "resume", "should_pause": False,
                "reason": f"剩餘 {_gb(free_bytes)} ≥ {p['resume_above_gb']:g}GB，恢復錄製"}
    return {"action": "none", "should_pause": currently_paused,
            "reason": "no change"}


def plan_cleanup(free_bytes: Optional[int],
                 files: List[Dict[str, Any]],
                 policy: Optional[Dict[str, Any]] = None,
                 now_ts: Optional[float] = None) -> Dict[str, Any]:
    """Plan which files to delete to reach the target free space.

    `files`: list of {"path", "size", "mtime", "active"(bool, optional)}.
    Deletes oldest-first, never the active file, never files newer than keep_days.
    Returns {"enabled", "delete": [paths], "freed_bytes", "free_after", "reason"}.
    Nothing is deleted here — this is a plan only.
    """
    p = merge_policy(policy)
    if not p["cleanup_enabled"]:
        return {"enabled": False, "delete": [], "freed_bytes": 0,
                "free_after": free_bytes, "reason": "cleanup disabled"}
    if free_bytes is None:
        return {"enabled": True, "delete": [], "freed_bytes": 0,
                "free_after": None, "reason": "free space unknown"}

    target = p["cleanup_target_gb"] * GB
    if free_bytes >= target:
        return {"enabled": True, "delete": [], "freed_bytes": 0,
                "free_after": free_bytes, "reason": "already above target"}

    import time as _t
    now_ts = now_ts if now_ts is not None else _t.time()
    keep_cutoff = (now_ts - p["keep_days"] * 86400) if p["keep_days"] else None

    # Candidates: not active, older than keep window. Oldest first.
    cands = []
    for f in files or []:
        if p["protect_active"] and f.get("active"):
            continue
        mtime = f.get("mtime")
        if keep_cutoff is not None and mtime is not None and mtime > keep_cutoff:
            continue
        cands.append(f)
    cands.sort(key=lambda f: f.get("mtime") or 0)  # oldest first

    to_delete, freed = [], 0
    need = target - free_bytes
    for f in cands:
        if freed >= need:
            break
        to_delete.append(f["path"])
        freed += int(f.get("size") or 0)

    return {
        "enabled": True,
        "delete": to_delete,
        "freed_bytes": freed,
        "free_after": free_bytes + freed,
        "reason": (f"刪除 {len(to_delete)} 個最舊檔案以釋出 {_gb(freed)}"
                   if to_delete else "沒有可刪除的候選檔（可能都受保護或在保留期內）"),
    }


def iter_recording_files(root: str, exts=(".ts", ".mp4", ".flv", ".mkv", ".m4a", ".mp3")):
    """Yield {"path","size","mtime"} for recording files under `root`.
    Best-effort; skips unreadable entries. (Used by the web layer, not pure.)"""
    out = []
    try:
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                if not n.lower().endswith(exts):
                    continue
                fp = Path(dirpath) / n
                try:
                    st = fp.stat()
                    out.append({"path": str(fp), "size": st.st_size,
                                "mtime": st.st_mtime})
                except Exception:
                    continue
    except Exception:
        pass
    return out
