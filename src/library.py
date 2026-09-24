"""
library.py - Recording library: group finished recordings by date and resolve
the actual on-disk video file for playback.

Pure-ish logic (filesystem access is injected as `exists`/`resolve` callables so
the core is unit-testable). The web layer wraps this to serve a date-based
"continuous playback" player.

Why a resolver is needed: the duration DB stores the file_path as it was at
record time, but the file may since have been:
  * converted .ts -> .mp4 (recorder post-processing), or
  * replaced by the auto-compressor with <stem>_hevc.mp4 (original deleted).
So the stored path can be stale; resolve_video() finds the real current file.
"""
from __future__ import annotations

import glob as _glob_module
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Extensions we can play back / look for, in preference order.
_PLAYABLE_EXTS = [".mp4", ".mkv", ".flv", ".ts"]
_COMPRESS_SUFFIX = "_hevc"

# ffmpeg's -f segment muxer output pattern, e.g. "..._%03d.ts" (main.py's 分段錄製).
_SEGMENT_PATTERN_RE = re.compile(r"%0?\d*d")

# main.py names each configured URL entry "序号{N} {anchor_name}" (record_name =
# f'序号{count_variable} {anchor_name}', N = that entry's line index in
# URL_config.ini) purely so the console can tell threads apart. That raw
# record_name is what gets stored as recording_sessions.anchor_name -- if we
# group/display by it verbatim, the same real streamer fragments into a
# different "anchor" every time N shifts (someone else added/removed a line
# above them in URL_config.ini). Strip it for grouping/display.
_SEQ_PREFIX_RE = re.compile(r"^序[号號]\d+\s+")


def canonical_anchor_name(name: Optional[str]) -> str:
    """Real display name with main.py's '序号N ' disambiguation prefix removed."""
    return _SEQ_PREFIX_RE.sub("", name or "").strip()


def _default_exists(p: str) -> bool:
    try:
        return Path(p).exists()
    except Exception:
        return False


def _default_glob(pattern: str) -> List[str]:
    try:
        return _glob_module.glob(pattern)
    except Exception:
        return []


def resolve_video(stored_path: str,
                  exists: Callable[[str], bool] = _default_exists,
                  glob_fn: Callable[[str], List[str]] = _default_glob,
                  cache: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return the real current path for a recording, or None if nothing is found.

    Tries, in order:
      0. if stored_path is an ffmpeg segment pattern (contains "%03d" etc.) --
         that literal string is never a real file (ffmpeg substitutes the
         number per segment) -- glob for the earliest real segment instead.
         Backward-compat path for rows written before main.py started
         resolving this itself at record time (see _resolve_recorded_file_path
         in main.py); handles old DB rows without a migration script.
      1. the compressed twin  <stem>_hevc.mp4   (compressor replaces originals)
      2. the stored path itself
      3. <stem>.mp4 / .mkv / .flv / .ts        (format conversion)
      4. <stem>_hevc.<ext> for other exts

    `cache`: optional dict shared across calls (caller-owned; None here means
    "no caching", so existing unit tests that don't pass one keep the exact
    same pure, deterministic behaviour). When provided, this does *verify-
    then-trust* caching, not a TTL: a cache hit is only returned after
    confirming (via the injected `exists()` -- one cheap stat, not a glob)
    that the previously-resolved path still exists. If the recorder or the
    compressor has since replaced the file, `exists()` on the stale path
    returns False, so we fall through and re-glob for the current real path
    -- there is never a window where a renamed/deleted file's stale path
    keeps getting served. This is what makes repeatedly resolving the same
    (mostly-finished, rarely-changing) recordings cheap without needing any
    cross-process invalidation signal from main.py/compressor.py.
    """
    if not stored_path:
        return None
    if cache is not None:
        cached = cache.get(stored_path)
        if cached is not None and exists(cached):
            return cached
    result = _resolve_video_uncached(stored_path, exists, glob_fn)
    if cache is not None and result is not None:
        cache[stored_path] = result
    return result


def _resolve_video_uncached(stored_path: str,
                            exists: Callable[[str], bool],
                            glob_fn: Callable[[str], List[str]]) -> Optional[str]:
    if _SEGMENT_PATTERN_RE.search(stored_path):
        # Segment number wildcarded, but the *extension* in the stored path can
        # no longer be trusted either: converts_to_mp4 (main.py) converts each
        # real segment to .mp4 and deletes the .ts original once done, so the
        # literal ".ts" from the stored pattern often matches nothing anymore.
        # Strip the stored extension too and retry every playable extension +
        # the compressed twin, same priority order as the non-pattern branch
        # below (compressed .mp4 first, since compression deletes the source).
        stem_with_token = str(Path(stored_path).with_suffix(""))
        stem_glob = _SEGMENT_PATTERN_RE.sub("*", _glob_module.escape(stem_with_token))
        pattern_candidates = [stem_glob + _COMPRESS_SUFFIX + ".mp4"]
        for ext in _PLAYABLE_EXTS:
            pattern_candidates.append(stem_glob + ext)
            pattern_candidates.append(stem_glob + _COMPRESS_SUFFIX + ext)
        seen_patterns = set()
        for pattern in pattern_candidates:
            if pattern in seen_patterns:
                continue
            seen_patterns.add(pattern)
            matches = sorted(c for c in glob_fn(pattern) if exists(c))
            if matches:
                return matches[0]
        return None
    p = Path(stored_path)
    stem = p.with_suffix("")           # path without extension
    candidates = [
        str(stem) + _COMPRESS_SUFFIX + ".mp4",
        stored_path,
    ]
    for ext in _PLAYABLE_EXTS:
        candidates.append(str(stem) + ext)
        candidates.append(str(stem) + _COMPRESS_SUFFIX + ext)
    seen = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        if exists(c):
            return c
    return None


def resolve_all_segments(stored_path: str,
                         exists: Callable[[str], bool] = _default_exists,
                         glob_fn: Callable[[str], List[str]] = _default_glob,
                         cache: Optional[Dict[str, List[Tuple[int, str]]]] = None
                         ) -> List[Tuple[int, str]]:
    """Like resolve_video()'s segment-pattern branch, but returns *every* real
    segment file found (not just the first) -- one recording_sessions row with
    a 分段錄製 (ffmpeg -f segment) path can correspond to many real files on
    disk (a 5h40m stream split into 30-min segments is one DB row + ~11
    files). build_playlist() uses this to show each real segment as its own
    playable clip instead of silently only ever offering the first ~30 minutes
    of a session that's actually hours long.

    Returns [(segment_index, real_path), ...] sorted by index ascending. Same
    per-index priority as resolve_video() (compressed .mp4 twin first, since
    compression deletes the source) -- if both a compressed and raw file exist
    for the same index, only the higher-priority one is kept, not both.

    `cache`: same verify-then-trust contract as resolve_video()'s -- None
    (default) means no caching, so existing callers/tests are unaffected. A
    cache hit is only trusted once every cached segment path is re-checked
    with `exists()` (cheap stats, no glob); any one of them missing (a
    segment got compressed/renamed since) invalidates the whole cached list
    and triggers a fresh glob, so this can't ever serve a stale segment list
    for a session whose files have since changed. This is the expensive path
    in practice (one `glob_fn()` call per candidate extension/compression
    combination, per session) -- group_by_date()/build_playlist() call this
    once per session in the requested set, so for a prolific anchor with many
    分段錄製 sessions this cache is what turns "reglob everything on every
    click" into "one glob per session, ever, until a file actually changes".
    """
    if not stored_path or not _SEGMENT_PATTERN_RE.search(stored_path):
        return []
    if cache is not None:
        cached = cache.get(stored_path)
        if cached is not None and all(exists(p) for _, p in cached):
            return cached
    result = _resolve_all_segments_uncached(stored_path, exists, glob_fn)
    if cache is not None and result:
        cache[stored_path] = result
    return result


def _resolve_all_segments_uncached(stored_path: str,
                                   exists: Callable[[str], bool],
                                   glob_fn: Callable[[str], List[str]]
                                   ) -> List[Tuple[int, str]]:
    stem_with_token = str(Path(stored_path).with_suffix(""))
    stem_glob = _SEGMENT_PATTERN_RE.sub("*", _glob_module.escape(stem_with_token))
    index_re = re.compile(
        # function replacement, not a raw string -- re.sub() treats a string
        # replacement's backslashes as its own escape sequences ("\d" isn't a
        # valid one, so r"(\d+)" as a plain replacement raises "bad escape");
        # a callable's return value is inserted verbatim, no re-interpretation.
        "^" + _SEGMENT_PATTERN_RE.sub(lambda _m: r"(\d+)", re.escape(stem_with_token)) + r"\.[^.]+$")

    pattern_candidates = [stem_glob + _COMPRESS_SUFFIX + ".mp4"]
    for ext in _PLAYABLE_EXTS:
        pattern_candidates.append(stem_glob + ext)
        pattern_candidates.append(stem_glob + _COMPRESS_SUFFIX + ext)

    best_for_index: Dict[int, str] = {}
    seen_patterns = set()
    for pattern in pattern_candidates:
        if pattern in seen_patterns:
            continue
        seen_patterns.add(pattern)
        for path in glob_fn(pattern):
            if not exists(path):
                continue
            m = index_re.match(path)
            if not m:
                continue
            idx = int(m.group(1))
            if idx not in best_for_index:  # first hit = highest-priority pattern for this index
                best_for_index[idx] = path
    return sorted(best_for_index.items())


def find_sidecar_subtitles(video_path: str,
                           exists: Callable[[str], bool] = _default_exists
                           ) -> Dict[str, Optional[str]]:
    """Look for danmaku sidecar subtitles next to the video.
    Returns {"srt": path|None, "ass": path|None}."""
    out = {"srt": None, "ass": None}
    if not video_path:
        return out
    stem = str(Path(video_path).with_suffix(""))
    for kind in ("srt", "ass"):
        for cand in (f"{stem}.danmaku.{kind}", f"{stem}.{kind}"):
            if exists(cand):
                out[kind] = cand
                break
    return out


def _parse_start(start_str: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(start_str)
    except Exception:
        return None


def estimate_segment_window(start_iso: str, total_duration_sec: int, index: int,
                            segment_seconds: int) -> Tuple[str, int]:
    """(start_time_iso, duration_sec) for the `index`-th real segment of a
    分段錄製 session whose *whole* live session started at `start_iso` and
    lasted `total_duration_sec` in total.

    Single source of truth for this estimate -- used by build_playlist()
    (labelling/ordering each segment row in the day's clip list) AND by
    web_ui.py's clip-id -> session resolver (so a clip's synced-danmaku time
    window lines up with *that specific segment*, not the whole multi-hour
    session -- otherwise offset_sec would be computed against the wrong
    baseline and the sync panel would show messages from the wrong part of
    the stream, or just fail to line up with anything in the ~30min clip
    that's actually playing). Keeping both call sites on this one function
    means they can't silently drift apart from each other.
    """
    dt = _parse_start(start_iso)
    if dt is None:
        return start_iso, segment_seconds
    seg_start = dt + timedelta(seconds=index * segment_seconds)
    if total_duration_sec:
        seg_duration = max(0, min(segment_seconds, total_duration_sec - index * segment_seconds))
    else:
        seg_duration = segment_seconds
    return seg_start.strftime("%Y-%m-%dT%H:%M:%S"), seg_duration


def group_by_date(sessions: List[Dict[str, Any]],
                  exists: Callable[[str], bool] = _default_exists,
                  glob_fn: Callable[[str], List[str]] = _default_glob,
                  segments_cache: Optional[Dict[str, List[Tuple[int, str]]]] = None
                  ) -> List[Dict[str, Any]]:
    """Group finished sessions by local calendar date (YYYY-MM-DD).

    sessions: dicts with at least start_time (ISO). Returns
    [{"date": "YYYY-MM-DD", "count": n, "total_sec": s}, ...] newest first.

    `count` is how many playable clips that date will actually show in
    build_playlist(), not "1 per DB row": a 分段錄製 session expands into one
    row per real segment file there, so counting DB rows here would show e.g.
    "1 段" for a date whose 當天清單 then lists 11 rows -- exactly the
    mismatch a user hit (date list said "1 段, 5h40m" for a session whose
    playable clip turned out to be one 30-min segment). `total_sec` stays the
    session's *total* live duration (unaffected by segmenting) -- that number
    is correct as-is, it just needs `count` to stop contradicting it.

    `segments_cache`: passed straight through to resolve_all_segments() (see
    its docstring) -- this is the expensive call in this loop (one real glob
    per candidate pattern per 分段錄製 session), and this function runs on
    every /api/library/dates request (every page-load and every anchor
    click), so a caller-owned cache here is what avoids re-globbing a
    prolific anchor's entire history on every click.
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    for s in sessions or []:
        dt = _parse_start(s.get("start_time", ""))
        if dt is None:
            continue
        key = dt.strftime("%Y-%m-%d")
        b = buckets.setdefault(key, {"date": key, "count": 0, "total_sec": 0})
        segments = resolve_all_segments(s.get("file_path", ""), exists=exists,
                                        glob_fn=glob_fn, cache=segments_cache)
        b["count"] += len(segments) if segments else 1
        b["total_sec"] += int(s.get("duration_sec") or 0)
    return sorted(buckets.values(), key=lambda x: x["date"], reverse=True)


def list_anchors(sessions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Distinct anchors present in `sessions`, for the playback page's
    anchor-first picker (UIUX follow-up: 回放應該選擇主播後才是選擇日期).

    Returns [{"anchor_name", "count", "total_sec", "latest_date"}, ...],
    sorted by latest_date desc (most recently active anchor first).
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    for s in sessions or []:
        name = canonical_anchor_name(s.get("anchor_name")) or "(未知主播)"
        b = buckets.setdefault(name, {"anchor_name": name, "count": 0,
                                       "total_sec": 0, "latest_date": None})
        b["count"] += 1
        b["total_sec"] += int(s.get("duration_sec") or 0)
        dt = _parse_start(s.get("start_time", ""))
        if dt is not None:
            key = dt.strftime("%Y-%m-%d")
            if b["latest_date"] is None or key > b["latest_date"]:
                b["latest_date"] = key
    return sorted(buckets.values(),
                  key=lambda x: (x["latest_date"] or ""), reverse=True)


def build_playlist(sessions: List[Dict[str, Any]], date: str,
                   exists: Callable[[str], bool] = _default_exists,
                   id_for: Optional[Callable[[str], str]] = None,
                   glob_fn: Callable[[str], List[str]] = _default_glob,
                   segment_seconds: int = 1800,
                   segments_cache: Optional[Dict[str, List[Tuple[int, str]]]] = None,
                   video_cache: Optional[Dict[str, str]] = None
                   ) -> List[Dict[str, Any]]:
    """Ordered, playable clips for one date (chronological — natural viewing).

    Each item: {id, anchor_name, platform, live_url, start_time, duration_sec,
                path, filename, has_srt, has_ass}. Clips whose file can't be
                resolved are skipped (can't play a missing file). anchor_name
                is the canonical display name (see canonical_anchor_name) --
                main.py's "序号N " disambiguation prefix is stripped so the
                same real streamer doesn't fragment across different N.

    分段錄製 (segment_seconds = main.py's 视频分段时间(秒), default 1800/30min):
    one recording_sessions row can correspond to *many* real files on disk
    (a 5h40m stream split into 30-min segments = 1 DB row + ~11 files). Such
    a session expands into one playlist item per real segment file found
    (via resolve_all_segments), each with its own estimated start_time
    (session start + index*segment_seconds) and duration_sec (segment_seconds,
    or whatever's left of the session for a shorter trailing segment) --
    otherwise 回放 could only ever offer the first ~30 minutes of an hours-long
    stream while showing the session's *total* duration in the date list,
    which is confusing (looks like "only 1 clip, but the date's a total-hours
    count that doesn't match what's playable"). Estimated, not exact: real
    segment boundaries can drift slightly from a clean multiple of
    segment_seconds (keyframe alignment), but this ordering-only estimate is
    what the day list's own display/sort needs.
    """
    items = []
    for s in sessions or []:
        dt = _parse_start(s.get("start_time", ""))
        if dt is None or dt.strftime("%Y-%m-%d") != date:
            continue
        anchor = canonical_anchor_name(s.get("anchor_name"))
        platform = s.get("platform", "")
        live_url = s.get("live_url", "")
        stored_path = s.get("file_path", "")
        total_duration = int(s.get("duration_sec") or 0)

        segments = resolve_all_segments(stored_path, exists=exists, glob_fn=glob_fn,
                                        cache=segments_cache)
        if segments:
            for idx, real in segments:
                seg_start, seg_duration = estimate_segment_window(
                    s.get("start_time", ""), total_duration, idx, segment_seconds)
                subs = find_sidecar_subtitles(real, exists=exists)
                items.append({
                    "id": id_for(real) if id_for else real,
                    "anchor_name": anchor,
                    "platform": platform,
                    "live_url": live_url,
                    "start_time": seg_start,
                    "duration_sec": seg_duration,
                    "path": real,
                    "filename": os.path.basename(real),
                    "has_srt": bool(subs["srt"]),
                    "has_ass": bool(subs["ass"]),
                })
            continue

        real = resolve_video(stored_path, exists=exists, glob_fn=glob_fn, cache=video_cache)
        if not real:
            continue
        subs = find_sidecar_subtitles(real, exists=exists)
        items.append({
            "id": id_for(real) if id_for else real,
            "anchor_name": anchor,
            "platform": platform,
            "live_url": live_url,
            "start_time": s.get("start_time", ""),
            "duration_sec": total_duration,
            "path": real,
            "filename": os.path.basename(real),
            "has_srt": bool(subs["srt"]),
            "has_ass": bool(subs["ass"]),
        })
    items.sort(key=lambda x: x["start_time"])   # chronological within the day
    return items


def locate_epoch_in_windows(windows: List[Tuple[float, float, str]],
                            epoch: float) -> Optional[Tuple[str, float]]:
    """Given a recording session's per-clip time windows, find which clip a
    wall-clock `epoch` falls into and the offset (seconds) into that clip.

    windows: [(start_epoch, end_epoch, clip_id), ...] — one entry per playable
    clip belonging to one recording session (a single-file session has one
    window; a 分段錄製 session has one window per real segment file, in the
    same order/identity build_playlist()'s clip ids already use). Windows are
    assumed non-overlapping; order doesn't matter (sorted internally).

    Used by the playback-marker feature (回放標記): a mark is stored once per
    *session* as an absolute epoch (or epoch range), independent of which
    segment file happens to contain it — this function is what maps that
    epoch back to "which clip to load + where to seek" at render time, so a
    marker set near a 分段錄製 boundary still resolves correctly even though
    segment files can be renamed/recompressed later (the epoch itself never
    changes, only which window currently claims it).

    Returns (clip_id, offset_sec) for the containing window, or the nearest
    window's edge if `epoch` falls just outside all of them (rounding at a
    segment boundary, or a session whose live duration slightly overruns the
    last estimated segment window). Returns None only if `windows` is empty.
    """
    if not windows:
        return None
    ordered = sorted(windows, key=lambda w: w[0])
    for start_e, end_e, clip_id in ordered:
        if start_e <= epoch < end_e:
            return clip_id, max(0.0, epoch - start_e)
    first_start, _, first_clip = ordered[0]
    if epoch < first_start:
        return first_clip, 0.0
    last_start, last_end, last_clip = ordered[-1]
    if epoch >= last_end:
        return last_clip, max(0.0, epoch - last_start)
    # Falls in a gap between two windows (shouldn't normally happen for
    # contiguous segments, but don't crash on it) -- attach to the nearest
    # preceding window.
    best = ordered[0]
    for w in ordered:
        if w[0] <= epoch:
            best = w
        else:
            break
    return best[2], max(0.0, epoch - best[0])


def is_within(base_dir: str, target: str) -> bool:
    """Path-traversal guard: True only if `target` is inside `base_dir`."""
    try:
        base = Path(base_dir).resolve()
        tgt = Path(target).resolve()
        return base == tgt or base in tgt.parents
    except Exception:
        return False
