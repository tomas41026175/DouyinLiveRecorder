"""
danmaku_subtitle.py - Turn stored danmaku into sidecar subtitles (ASS + SRT).

Pure logic, no IO. Given a list of danmaku rows (each with a unix ``ts`` and
``content``) plus the video's start epoch, every message's position in the video
is ``ts - start_epoch`` seconds. Two renderers:

  * build_ass() — scrolling danmaku (弹幕): each message flies right-to-left
    across the top area, assigned to a lane that is free, so they don't overlap.
    Looks like native live danmaku. Needs a player that supports ASS
    (PotPlayer / mpv / VLC).
  * build_srt() — plain bottom subtitles: concurrent messages within the same
    short window are grouped into one cue (a few lines), fixed duration each.

Both drop messages that fall outside [0, video_duration] so stray rows from
before/after the recording don't appear.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# --- defaults (overridable) -------------------------------------------------
ASS_PLAY_RES_X = 1280
ASS_PLAY_RES_Y = 720
ASS_FONT = "Microsoft YaHei"
ASS_FONTSIZE = 36
ASS_SCROLL_SECONDS = 8.0     # time for one danmaku to cross the screen
ASS_LANES = 12               # max simultaneous scrolling rows
ASS_LANE_HEIGHT = 40

SRT_CUE_SECONDS = 4.0        # how long each SRT cue stays on screen
SRT_GROUP_WINDOW = 2.0       # messages within this window share one cue
SRT_MAX_LINES = 4            # cap lines per cue


def _fmt_ass_time(t: float) -> str:
    """H:MM:SS.cc (centiseconds) — ASS format."""
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _fmt_srt_time(t: float) -> str:
    """HH:MM:SS,mmm — SRT format."""
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _escape_ass(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _offsets(rows: List[Dict[str, Any]], start_epoch: int,
             duration: Optional[float]) -> List[Dict[str, Any]]:
    """Attach 't' (seconds into video) and keep only in-range, sorted rows."""
    out = []
    for r in rows or []:
        try:
            t = int(r["ts"]) - int(start_epoch)
        except Exception:
            continue
        if t < 0:
            continue
        if duration is not None and t > float(duration):
            continue
        content = (r.get("content") or "").strip()
        if not content:
            continue
        out.append({"t": float(t), "user": r.get("user") or "",
                    "content": content})
    out.sort(key=lambda x: x["t"])
    return out


# ---------------------------------------------------------------------------
# ASS scrolling danmaku
# ---------------------------------------------------------------------------
def build_ass(rows: List[Dict[str, Any]], start_epoch: int,
              duration: Optional[float] = None,
              play_res_x: int = ASS_PLAY_RES_X, play_res_y: int = ASS_PLAY_RES_Y,
              font: str = ASS_FONT, fontsize: int = ASS_FONTSIZE,
              scroll_seconds: float = ASS_SCROLL_SECONDS,
              lanes: int = ASS_LANES, lane_height: int = ASS_LANE_HEIGHT,
              title: str = "Danmaku") -> str:
    """Return a complete .ass document string."""
    items = _offsets(rows, start_epoch, duration)
    header = f"""[Script Info]
Title: {title}
ScriptType: v4.00+
Collisions: Normal
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: DM,{font},{fontsize},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text
"""
    # lane_free_at[i] = video-time when lane i is next free to launch a new one
    lane_free_at = [0.0] * max(1, lanes)
    est_char_px = fontsize  # rough width per CJK char
    lines = []
    for it in items:
        t = it["t"]
        # pick the first lane whose previous danmaku has scrolled far enough
        lane = None
        for i in range(len(lane_free_at)):
            if lane_free_at[i] <= t:
                lane = i
                break
        if lane is None:
            # all busy -> reuse the earliest-freeing lane (slight overlap tolerated)
            lane = min(range(len(lane_free_at)), key=lambda i: lane_free_at[i])
        width = max(1, len(it["content"])) * est_char_px
        y = 10 + lane * lane_height
        start = t
        end = t + scroll_seconds
        # move from just off the right edge to fully off the left edge
        x1 = play_res_x + width // 2
        x2 = -(width // 2)
        text = _escape_ass(it["content"])
        # this lane is free once the tail of this danmaku enters the screen edge;
        # approximate as when it has travelled its own width past launch
        lane_free_at[lane] = t + scroll_seconds * (width / (play_res_x + width)) + 0.3
        lines.append(
            f"Dialogue: 0,{_fmt_ass_time(start)},{_fmt_ass_time(end)},DM,,0,0,0,"
            f",{{\\move({x1},{y},{x2},{y})}}{text}")
    return header + "\n".join(lines) + ("\n" if lines else "")


# ---------------------------------------------------------------------------
# SRT bottom subtitles (grouped)
# ---------------------------------------------------------------------------
def build_srt(rows: List[Dict[str, Any]], start_epoch: int,
              duration: Optional[float] = None,
              cue_seconds: float = SRT_CUE_SECONDS,
              group_window: float = SRT_GROUP_WINDOW,
              max_lines: int = SRT_MAX_LINES,
              with_user: bool = False) -> str:
    """Return a complete .srt document string. Concurrent messages within
    `group_window` seconds are merged into one multi-line cue."""
    items = _offsets(rows, start_epoch, duration)
    # group
    groups: List[Dict[str, Any]] = []
    for it in items:
        line = (f"{it['user']}: {it['content']}" if with_user and it["user"]
                else it["content"])
        if groups and (it["t"] - groups[-1]["start"]) <= group_window \
                and len(groups[-1]["lines"]) < max_lines:
            groups[-1]["lines"].append(line)
        else:
            groups.append({"start": it["t"], "lines": [line]})

    out = []
    for idx, g in enumerate(groups, 1):
        start = g["start"]
        # end at next group's start (so cues don't overlap) capped by cue_seconds
        nxt = groups[idx]["start"] if idx < len(groups) else None
        end = start + cue_seconds
        if nxt is not None:
            end = min(end, max(start + 0.5, nxt))
        out.append(f"{idx}\n{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n"
                   + "\n".join(g["lines"]) + "\n")
    return "\n".join(out)


def sidecar_paths(video_path: str, suffix: str = ".danmaku"):
    """Given /a/b/video.mp4 -> (video.danmaku.ass, video.danmaku.srt) paths as
    strings (does not touch disk)."""
    from pathlib import Path
    p = Path(video_path)
    stem = p.with_suffix("")  # drop extension
    return (str(stem) + suffix + ".ass", str(stem) + suffix + ".srt")
