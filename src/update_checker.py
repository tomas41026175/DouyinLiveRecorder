"""
src/update_checker.py — Check upstream GitHub repo for updates and auto-apply.

* Only auto-updates files in AUTO_UPDATE_FILES (default: ['src/spider.py']).
  These are the files that change frequently for platform-API fixes.
* Files in NOTIFY_ONLY_FILES (main.py, etc.) are NOT touched because we have
  custom patches in them — they're only reported as "remote changed" so the
  user can rebuild manually.
* Backs up the old version to <file>.upstream-bak before overwriting.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

REPO_OWNER = "ihmily"
REPO_NAME = "DouyinLiveRecorder"
DEFAULT_BRANCH = "main"

# These get overwritten on auto-update
AUTO_UPDATE_FILES = [
    "src/spider.py",
]

# These are watched for changes but NOT auto-replaced
# (main.py has our duration_tracker hooks; src/stream.py etc. may have edits)
NOTIFY_ONLY_FILES = [
    "main.py",
    "src/stream.py",
    "src/utils.py",
    "src/room.py",
    "src/ab_sign.py",
    "src/__init__.py",
]


# ---------------------------------------------------------------------------
def _project_root() -> Path:
    """Same logic as duration_tracker.py — works whether run from .py or frozen exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


STATE_FILE = _project_root() / "config" / "update_state.json"


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    with _state_lock:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------
def fetch_latest_commit(timeout: float = 10.0):
    """Return dict {sha, message, html_url, committed_at} or None on error."""
    url = (f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/"
           f"commits/{DEFAULT_BRANCH}")
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        d = r.json()
        return {
            "sha": d["sha"],
            "short_sha": d["sha"][:7],
            "message": d["commit"]["message"].split("\n", 1)[0][:200],
            "html_url": d["html_url"],
            "committed_at": d["commit"]["committer"]["date"],
        }
    except Exception:
        return None


def fetch_file_at_sha(path_in_repo: str, sha: str, timeout: float = 30.0):
    """Download a single file from the repo at given sha. Returns bytes or None."""
    url = (f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/"
           f"{sha}/{path_in_repo}")
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None


def file_hash_at_sha(path_in_repo: str, sha: str) -> str | None:
    """Get the SHA of a file at a given commit (via Git Trees API)."""
    # The simplest "did file content change?" check: compare downloaded bytes.
    # For efficiency, just compare commit SHAs of NOTIFY_ONLY_FILES with last
    # applied SHA — if commit moved AND the file actually changed, alert.
    return None  # not used currently


# ---------------------------------------------------------------------------
# Check + apply
# ---------------------------------------------------------------------------
def check_for_update() -> dict:
    """Run a single check. Returns status dict."""
    commit = fetch_latest_commit()
    now = datetime.now().isoformat(timespec="seconds")
    state = load_state()
    state["last_check_at"] = now
    if commit is None:
        state["last_check_status"] = "network_error"
        save_state(state)
        return state

    state["latest_sha"] = commit["sha"]
    state["latest_short_sha"] = commit["short_sha"]
    state["latest_message"] = commit["message"]
    state["latest_committed_at"] = commit["committed_at"]
    state["latest_url"] = commit["html_url"]
    state["last_check_status"] = "ok"

    applied = state.get("applied_sha")
    state["has_update"] = (applied != commit["sha"])
    save_state(state)
    return state


def apply_update(target_sha: str = None) -> dict:
    """Download AUTO_UPDATE_FILES at target_sha (or latest) and replace local.
    Returns dict with results."""
    state = load_state()
    if target_sha is None:
        target_sha = state.get("latest_sha")
    if not target_sha:
        commit = fetch_latest_commit()
        if commit is None:
            return {"ok": False, "error": "Cannot reach GitHub API"}
        target_sha = commit["sha"]

    root = _project_root()
    updated, unchanged, failed = [], [], []
    for rel_path in AUTO_UPDATE_FILES:
        content = fetch_file_at_sha(rel_path, target_sha)
        if content is None:
            failed.append(rel_path)
            continue
        local = root / rel_path
        try:
            if local.exists() and local.read_bytes() == content:
                unchanged.append(rel_path)
                continue
            if local.exists():
                bak = local.with_suffix(local.suffix + ".upstream-bak")
                shutil.copy2(local, bak)
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(content)
            updated.append(rel_path)
        except Exception as e:
            failed.append(f"{rel_path}: {e}")

    state["applied_sha"] = target_sha
    state["applied_short_sha"] = target_sha[:7]
    state["applied_at"] = datetime.now().isoformat(timespec="seconds")
    state["last_apply_result"] = {
        "updated": updated, "unchanged": unchanged, "failed": failed,
    }
    state["has_update"] = False
    save_state(state)
    return {"ok": not failed, "updated": updated, "unchanged": unchanged,
            "failed": failed, "applied_sha": target_sha[:7]}


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------
def _bg_loop(interval_hours: float, auto_apply: bool):
    interval_sec = max(60, int(interval_hours * 3600))
    while True:
        try:
            check_for_update()
            state = load_state()
            if auto_apply and state.get("has_update"):
                print(f"[update] new commit {state.get('latest_short_sha')} "
                      f"detected: {state.get('latest_message')}")
                result = apply_update()
                if result.get("ok"):
                    print(f"[update] auto-applied: {result.get('updated')}")
                else:
                    print(f"[update] auto-apply failed: {result.get('failed')}")
        except Exception as e:
            print(f"[update] error: {e}")
        time.sleep(interval_sec)


def start_background_checker(interval_hours: float = 6.0, auto_apply: bool = True):
    """Spawn daemon thread that periodically checks GitHub for new commits."""
    t = threading.Thread(
        target=_bg_loop, args=(interval_hours, auto_apply),
        daemon=True, name="update-checker")
    t.start()
    return t
