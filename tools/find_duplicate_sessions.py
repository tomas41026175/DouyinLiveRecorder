"""
find_duplicate_sessions.py -- diagnostic + cleanup helper for the orphaned-
ffmpeg duplicate-recording bug (see AGENT.md/CLAUDE.md, `taskkill /f /im
...exe` without `/T`): finds pairs/clusters of recording sessions for the
same anchor whose start timestamps are only seconds/tens of seconds apart
AND whose segment files (`..._000.ts`, `..._001.ts`, ...) have near-identical
sizes -- the signature of the same live stream having been recorded twice in
parallel.

By default this only PRINTS a report -- it never touches your files. Pass
--quarantine-dir to additionally move the duplicate copy of each flagged
group into a separate folder (mirroring the original relative paths) so your
recording folders end up clean, but --apply is required to actually perform
the move; without it you get a dry-run preview of exactly what would happen.
Nothing is ever deleted -- review the quarantine folder yourself and delete
from there once you're satisfied.

Point it at any folder -- one anchor's own folder, or a parent folder holding
many anchors' subfolders (e.g. the whole `抖音直播` platform folder, or even
higher up covering several platforms). It walks the whole tree and groups
sessions by the anchor name embedded in each filename, not by which
subfolder they're in, so scanning "all anchors at once" needs no separate
flag -- just point `directory` at the common parent and the report groups
the results by anchor automatically.

Usage (from repo root or anywhere, stdlib only):

    # 1. Preview what's flagged as duplicate across every anchor under this
    #    folder (no files touched):
    python tools/find_duplicate_sessions.py "F:\\main\\record未分類\\抖音直播"

    # 2. Preview exactly what a cleanup would move (still no files touched):
    python tools/find_duplicate_sessions.py <dir> --quarantine-dir <dir>\\_duplicate_quarantine

    # 3. Actually move the duplicate copies into the quarantine folder:
    python tools/find_duplicate_sessions.py <dir> --quarantine-dir <dir>\\_duplicate_quarantine --apply

`--max-gap` seconds: how close two sessions' start times must be to be
considered (default 90, matching the 10~70s gaps seen in the real cases).
`--tolerance` relative file-size difference allowed per matched segment
index (default 0.02 = 2%).
`--keep {largest,earliest}` (default largest): within each duplicate group,
which single session to keep in place -- the rest are the ones moved to
quarantine. "largest" keeps whichever copy has the most total recorded
bytes (most complete); "earliest" keeps whichever started recording first
(matches which process was the pre-restart orphan) but can keep a
truncated recording if that earlier session was itself cut short for an
unrelated reason -- check the printed report either way before using --apply.
"""
import argparse
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dup_session_finder():
    # Load src/dup_session_finder.py by file path, bypassing src/__init__.py
    # (which pulls in the full recorder stack -- network clients, tqdm, etc.
    # -- unnecessary for this standalone, dependency-free diagnostic script;
    # same approach as tests/_loadmod.py).
    path = os.path.join(_ROOT, "src", "dup_session_finder.py")
    spec = importlib.util.spec_from_file_location("dup_session_finder", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses needs the module registered before exec
    spec.loader.exec_module(mod)
    return mod


dup = _load_dup_session_finder()
Segment = dup.Segment
build_sessions = dup.build_sessions
find_duplicate_candidates = dup.find_duplicate_candidates
parse_segment_filename = dup.parse_segment_filename
plan_quarantine = dup.plan_quarantine


def scan_directory(root: str) -> list[Segment]:
    segments = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            parsed = parse_segment_filename(name)
            if parsed is None:
                continue
            full_path = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full_path)
            except OSError:
                continue
            segments.append(Segment(
                path=full_path, prefix=parsed.prefix, start=parsed.start,
                index=parsed.index, size=size,
            ))
    return segments


def format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _quarantine_dest(scan_root: str, quarantine_dir: str, source_path: str) -> str:
    try:
        rel = os.path.relpath(source_path, scan_root)
    except ValueError:
        rel = os.path.basename(source_path)
    return os.path.join(quarantine_dir, rel)


def run_quarantine(plans, scan_root: str, quarantine_dir: str, apply: bool) -> list[dict]:
    """Build the full list of {source, dest, ...} moves for every session in
    every plan's `remove` list. When apply=True, actually performs the move
    (creating parent dirs, refusing to overwrite an existing dest) and
    returns only the moves that succeeded; when apply=False nothing is
    touched and every planned move is returned as a preview."""
    moves = []
    for plan in plans:
        for session in plan.remove:
            for seg in session.segments:
                dest = _quarantine_dest(scan_root, quarantine_dir, seg.path)
                moves.append({
                    "prefix": session.prefix,
                    "removed_session_start": session.start.isoformat(),
                    "kept_session_start": plan.keep.start.isoformat(),
                    "source": seg.path,
                    "dest": dest,
                    "size": seg.size,
                })

    if not apply:
        return moves

    applied = []
    for move in moves:
        dest = move["dest"]
        if os.path.exists(dest):
            print(f"    SKIP (destination already exists): {dest}", file=sys.stderr)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            shutil.move(move["source"], dest)
        except OSError as e:
            print(f"    SKIP (move failed: {e}): {move['source']}", file=sys.stderr)
            continue
        applied.append(move)
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", help="folder to scan recursively (e.g. one anchor's recording folder)")
    parser.add_argument("--max-gap", type=float, default=90.0, help="max seconds between session start times (default 90)")
    parser.add_argument("--tolerance", type=float, default=0.02, help="relative file-size tolerance per matched segment (default 0.02)")
    parser.add_argument("--min-match-ratio", type=float, default=0.7, help="min fraction of matched overlapping segments (default 0.7)")
    parser.add_argument("--keep", choices=["largest", "earliest"], default="largest",
                        help="which session per duplicate group to keep in place (default: largest total size)")
    parser.add_argument("--quarantine-dir", help="if set, plan moving flagged duplicate sessions here (mirrors original relative paths)")
    parser.add_argument("--apply", action="store_true", help="actually perform the quarantine move (default: dry-run preview only)")
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"Not a directory: {args.directory}", file=sys.stderr)
        return 1

    scan_root = os.path.abspath(args.directory)
    segments = scan_directory(scan_root)
    if not segments:
        print("No recording files matched the expected filename pattern under this directory.")
        return 0

    sessions = build_sessions(segments)
    candidates = find_duplicate_candidates(
        sessions, max_gap_seconds=args.max_gap, size_tolerance=args.tolerance,
        min_match_ratio=args.min_match_ratio,
    )

    anchors_scanned = len({s.prefix for s in sessions})
    if not candidates:
        print(f"Scanned {len(sessions)} sessions ({len(segments)} files, {anchors_scanned} anchors) under {scan_root}: "
              f"no likely duplicates found.")
        return 0

    # Group by anchor (dict preserves insertion order; candidates already
    # come out prefix-grouped from find_duplicate_candidates) so a scan
    # across many anchors reads as one section per anchor instead of one
    # flat numbered list.
    by_anchor: dict[str, list] = {}
    for c in candidates:
        by_anchor.setdefault(c.session_a.prefix, []).append(c)

    print(f"Scanned {len(sessions)} sessions ({len(segments)} files, {anchors_scanned} anchors) under {scan_root}.")
    print(f"Found {len(candidates)} likely duplicate session group(s) across {len(by_anchor)} anchor(s):\n")

    n = 0
    for anchor, anchor_candidates in by_anchor.items():
        print(f"===== {anchor} ({len(anchor_candidates)} 組) =====")
        for c in anchor_candidates:
            n += 1
            a, b = c.session_a, c.session_b
            print(f"[{n}] session A: start={a.start}  segments={len(a.segments)}  total={format_bytes(a.total_size())}")
            for seg in a.segments:
                print(f"        {seg.path}  ({format_bytes(seg.size)})")
            print(f"    session B: start={b.start}  segments={len(b.segments)}  total={format_bytes(b.total_size())}")
            for seg in b.segments:
                print(f"        {seg.path}  ({format_bytes(seg.size)})")
            print(f"    gap={c.gap_seconds:.0f}s  matched {c.matched_indices}/{c.compared_indices} overlapping segments "
                  f"({c.match_ratio:.0%})\n")

    plans = plan_quarantine(candidates, keep=args.keep)
    reclaimable = sum(rm.total_size() for plan in plans for rm in plan.remove)
    reclaimable_anchors = len({plan.keep.prefix for plan in plans})
    print(f"--- Summary: {len(plans)} duplicate group(s) across {reclaimable_anchors} anchor(s), "
          f"~{format_bytes(reclaimable)} reclaimable if cleaned up (keep={args.keep}) ---\n")

    if not args.quarantine_dir:
        print("(Pass --quarantine-dir <folder> to preview/perform moving the duplicate copies out of the way.)")
        return 0

    quarantine_dir = os.path.abspath(args.quarantine_dir)

    print(f"--- Quarantine plan (keep={args.keep}) ---\n")
    for plan in plans:
        print(f"[{plan.keep.prefix}] KEEP  start={plan.keep.start}  total={format_bytes(plan.keep.total_size())}")
        for rm in plan.remove:
            flag = " *** LARGER THAN THE KEPT SESSION -- review before deleting ***" if rm.total_size() > plan.keep.total_size() else ""
            print(f"           MOVE  start={rm.start}  total={format_bytes(rm.total_size())}{flag}")
        print()

    moves = run_quarantine(plans, scan_root, quarantine_dir, apply=args.apply)
    total_bytes = sum(m["size"] for m in moves)

    if not args.apply:
        print(f"DRY RUN: would move {len(moves)} file(s), {format_bytes(total_bytes)} total, into {quarantine_dir}")
        print("Re-run with --apply to actually move them (nothing is deleted; review the quarantine folder afterwards).")
        return 0

    manifest_path = os.path.join(quarantine_dir, f"manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(quarantine_dir, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(moves, f, ensure_ascii=False, indent=2)

    print(f"Moved {len(moves)} file(s), {format_bytes(total_bytes)} total, into {quarantine_dir}")
    print(f"Manifest (original source paths, for manual restore if needed): {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
