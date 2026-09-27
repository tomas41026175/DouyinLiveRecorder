"""
find_anchor_renames.py -- diagnostic + cleanup helper for anchor renames: the
same live-room URL recorded under two (or more) different display names over
time (streamer changed their nickname on the platform, or you edited the
pinned `主播: xxx` annotation in URL_config.ini from the web UI's streamer
editor). `folder_by_author` names each recording's folder after the anchor
name AT RECORD TIME, so a rename silently splits one real streamer's history
across separate folders with nothing linking them back together -- the live
room URL is the only thing that stays constant across the rename.

Source of truth for "did this URL's name ever change": the duration tracker
DB (`config/recording_history.db`, see src/duration_tracker.py), which has
recorded every session's (live_url, anchor_name, start_time) since it was
added. Whichever name that URL's most recent recording used is treated as
"current"; every other name it has ever used is a merge candidate.

By default this only PRINTS a report -- it never touches your files or your
DB. Pass --apply to move the OLD name's folder contents into the CURRENT
name's folder (never overwriting an existing destination) and repoint the
matching `recording_sessions.file_path` rows at their new location (needed
so the playback library can still find them -- see AGENT.md's note on
`resolve_video()` only searching the file's *stored* directory).

Usage (from repo root or anywhere, stdlib only):

    # 1. Preview what renames are detected and whether both folders exist
    #    (no files touched, no DB writes):
    python tools/find_anchor_renames.py "F:\\main\\record未分類" --db "F:\\main\\DouyinLiveRecorder_v4.0.7\\config\\recording_history.db"

    # 2. Once you've checked the preview, actually merge:
    python tools/find_anchor_renames.py <record_root> --db <path to recording_history.db> --apply

CAVEAT: any playback marks (`config/marks.db`) saved against a moved
session will no longer resolve -- marks are keyed by the session's exact
original `file_path` string (see AGENT.md), and merging changes it. This
tool does not touch marks.db; re-add those marks manually afterward if you
had any on the affected sessions.
"""
import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_ROOT, "src")


def _ensure_stub_src_package():
    # anchor_rename_finder.py does `from .library import canonical_anchor_name`
    # (a relative import); register a lightweight stand-in for the `src`
    # package (just its __path__, no code executed) so that resolves to the
    # real src/library.py loaded fresh, WITHOUT ever executing the real,
    # heavy src/__init__.py (same trick as tests/_loadmod.py).
    if "src" in sys.modules:
        return
    import types
    stub = types.ModuleType("src")
    stub.__path__ = [_SRC_DIR]
    sys.modules["src"] = stub


def _load_module(name):
    _ensure_stub_src_package()
    path = os.path.join(_SRC_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location("src." + name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


arf = _load_module("anchor_rename_finder")
SessionRecord = arf.SessionRecord
find_rename_events = arf.find_rename_events


def load_session_records(db_path: str) -> list:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT anchor_name, platform, live_url, start_time "
            "FROM recording_sessions WHERE live_url IS NOT NULL AND live_url != ''"
        ).fetchall()
    finally:
        con.close()

    records = []
    for anchor, platform, url, start_str in rows:
        try:
            start = datetime.fromisoformat(start_str)
        except (TypeError, ValueError):
            continue
        records.append(SessionRecord(url=url, anchor=anchor or "", start_time=start,
                                      platform=platform or ""))
    return records


def format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def folder_stats(path: str):
    if not os.path.isdir(path):
        return None
    total = 0
    count = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
            count += 1
    return {"files": count, "bytes": total}


def merge_folder(src_dir: str, dest_dir: str) -> list:
    """Move every file under src_dir into dest_dir, mirroring the relative
    path. Never overwrites an existing destination file. Returns the list of
    {source, dest} moves actually performed."""
    moves = []
    for dirpath, _dirnames, filenames in os.walk(src_dir):
        for name in filenames:
            source = os.path.join(dirpath, name)
            rel = os.path.relpath(source, src_dir)
            dest = os.path.join(dest_dir, rel)
            if os.path.exists(dest):
                print(f"    SKIP (destination already exists): {dest}", file=sys.stderr)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                shutil.move(source, dest)
            except OSError as e:
                print(f"    SKIP (move failed: {e}): {source}", file=sys.stderr)
                continue
            moves.append({"source": source, "dest": dest})

    # Clean up now-empty subdirectories left behind, best-effort.
    for dirpath, dirnames, filenames in os.walk(src_dir, topdown=False):
        try:
            os.rmdir(dirpath)
        except OSError:
            pass
    return moves


def repoint_file_paths(db_path: str, url: str, old_name: str, old_dir: str, new_dir: str) -> list:
    """Update recording_sessions.file_path for rows belonging to `url` whose
    canonical anchor name is old_name and whose stored path is under
    old_dir, rewriting the old_dir prefix to new_dir. Returns the list of
    {id, old_path, new_path} rows actually updated."""
    from src.library import canonical_anchor_name  # noqa: E402 (src stub already registered)

    con = sqlite3.connect(db_path)
    updated = []
    try:
        rows = con.execute(
            "SELECT id, anchor_name, file_path FROM recording_sessions WHERE live_url = ?",
            (url,),
        ).fetchall()
        old_dir_norm = os.path.normpath(old_dir)
        for row_id, anchor, file_path in rows:
            if canonical_anchor_name(anchor or "") != old_name:
                continue
            if not file_path:
                continue
            file_path_norm = os.path.normpath(file_path)
            if not file_path_norm.startswith(old_dir_norm + os.sep) and file_path_norm != old_dir_norm:
                continue
            rel = os.path.relpath(file_path_norm, old_dir_norm)
            new_path = os.path.normpath(os.path.join(new_dir, rel))
            con.execute("UPDATE recording_sessions SET file_path = ? WHERE id = ?",
                        (new_path, row_id))
            updated.append({"id": row_id, "old_path": file_path, "new_path": new_path})
        con.commit()
    finally:
        con.close()
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("record_root", help="folder holding <platform>/<anchor_name>/ subfolders (e.g. F:\\main\\record未分類)")
    parser.add_argument("--db", required=True, help="path to recording_history.db (in the recorder's own config\\ folder, not the source checkout)")
    parser.add_argument("--apply", action="store_true", help="actually merge the old folder into the new one and repoint the DB (default: dry-run preview only)")
    args = parser.parse_args()

    if not os.path.isdir(args.record_root):
        print(f"Not a directory: {args.record_root}", file=sys.stderr)
        return 1
    if not os.path.isfile(args.db):
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1

    records = load_session_records(args.db)
    events = find_rename_events(records)

    if not events:
        print(f"Scanned {len(records)} recording_sessions row(s) in {args.db}: no anchor renames detected.")
        return 0

    print(f"Scanned {len(records)} recording_sessions row(s) in {args.db}.")
    print(f"Found {len(events)} rename candidate(s):\n")

    plans = []
    for e in events:
        old_dir = os.path.join(args.record_root, e.platform, e.old_name)
        new_dir = os.path.join(args.record_root, e.platform, e.new_name)
        old_stats = folder_stats(old_dir)
        new_stats = folder_stats(new_dir)

        print(f"[{e.platform}] {e.old_name}  ->  {e.new_name}   (url: {e.url})")
        print(f"    last recorded as \"{e.old_name}\": {e.old_last_seen}")
        print(f"    first recorded as \"{e.new_name}\": {e.new_first_seen}")
        if old_stats is None:
            print(f"    old folder not found on disk: {old_dir}  (nothing to merge)")
            print()
            continue
        print(f"    old folder: {old_dir}  ({old_stats['files']} files, {format_bytes(old_stats['bytes'])})")
        if new_stats is None:
            print(f"    new folder does not exist yet: {new_dir}  (will be created on --apply)")
        else:
            print(f"    new folder: {new_dir}  ({new_stats['files']} files, {format_bytes(new_stats['bytes'])})")
        print()
        plans.append({"event": e, "old_dir": old_dir, "new_dir": new_dir})

    if not plans:
        print("No old folders found on disk for any detected rename -- nothing to merge.")
        return 0

    if not args.apply:
        print("(Pass --apply to actually merge the folders above and repoint the DB.)")
        return 0

    manifest = {"moves": [], "db_updates": []}
    for plan in plans:
        e = plan["event"]
        print(f"Merging {plan['old_dir']} -> {plan['new_dir']} ...")
        moves = merge_folder(plan["old_dir"], plan["new_dir"])
        manifest["moves"].extend(moves)
        updated = repoint_file_paths(args.db, e.url, e.old_name, plan["old_dir"], plan["new_dir"])
        manifest["db_updates"].extend(updated)
        print(f"    moved {len(moves)} file(s), repointed {len(updated)} DB row(s)")

    manifest_path = os.path.join(args.record_root, f"anchor_rename_manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, default=str)

    total_moved = len(manifest["moves"])
    total_updated = len(manifest["db_updates"])
    print(f"\nDone: moved {total_moved} file(s), repointed {total_updated} DB row(s).")
    print(f"Manifest: {manifest_path}")
    print("\nNOTE: any playback marks (config/marks.db) on the moved sessions are now "
          "orphaned (marks are keyed by the session's exact original file_path) -- "
          "re-add those manually if you had any.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
