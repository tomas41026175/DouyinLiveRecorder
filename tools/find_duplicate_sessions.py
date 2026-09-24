"""
find_duplicate_sessions.py -- read-only diagnostic for the orphaned-ffmpeg
duplicate-recording bug (see AGENT.md/CLAUDE.md, `taskkill /f /im ...exe`
without `/T`): reports pairs of recording sessions for the same anchor whose
start timestamps are only seconds/tens of seconds apart AND whose segment
files (`..._000.ts`, `..._001.ts`, ...) have near-identical sizes -- the
signature of the same live stream having been recorded twice in parallel.

Does NOT move, rename, or delete anything. It only prints a list so you can
review it and clean up manually (or point a separate script at the flagged
pairs once you've confirmed them).

Usage (from repo root or anywhere, stdlib only):

    python tools/find_duplicate_sessions.py "F:\\main\\record未分類\\抖音直播\\主播資料夾"
    python tools/find_duplicate_sessions.py <dir> --max-gap 90 --tolerance 0.02

`--max-gap` seconds: how close two sessions' start times must be to be
considered (default 90, matching the 10~70s gaps seen in the real cases).
`--tolerance` relative file-size difference allowed per matched segment
index (default 0.02 = 2%).
"""
import argparse
import importlib.util
import os
import sys

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", help="folder to scan recursively (e.g. one anchor's recording folder)")
    parser.add_argument("--max-gap", type=float, default=90.0, help="max seconds between session start times (default 90)")
    parser.add_argument("--tolerance", type=float, default=0.02, help="relative file-size tolerance per matched segment (default 0.02)")
    parser.add_argument("--min-match-ratio", type=float, default=0.7, help="min fraction of matched overlapping segments (default 0.7)")
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"Not a directory: {args.directory}", file=sys.stderr)
        return 1

    segments = scan_directory(args.directory)
    if not segments:
        print("No recording files matched the expected filename pattern under this directory.")
        return 0

    sessions = build_sessions(segments)
    candidates = find_duplicate_candidates(
        sessions, max_gap_seconds=args.max_gap, size_tolerance=args.tolerance,
        min_match_ratio=args.min_match_ratio,
    )

    if not candidates:
        print(f"Scanned {len(sessions)} sessions ({len(segments)} files) under {args.directory}: no likely duplicates found.")
        return 0

    print(f"Scanned {len(sessions)} sessions ({len(segments)} files) under {args.directory}.")
    print(f"Found {len(candidates)} likely duplicate session pair(s):\n")

    for n, c in enumerate(candidates, 1):
        a, b = c.session_a, c.session_b
        print(f"[{n}] {a.prefix}")
        print(f"    session A: start={a.start}  segments={len(a.segments)}  total={format_bytes(a.total_size())}")
        for seg in a.segments:
            print(f"        {seg.path}  ({format_bytes(seg.size)})")
        print(f"    session B: start={b.start}  segments={len(b.segments)}  total={format_bytes(b.total_size())}")
        for seg in b.segments:
            print(f"        {seg.path}  ({format_bytes(seg.size)})")
        print(f"    gap={c.gap_seconds:.0f}s  matched {c.matched_indices}/{c.compared_indices} overlapping segments "
              f"({c.match_ratio:.0%})\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
