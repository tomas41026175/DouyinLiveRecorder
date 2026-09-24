"""Pure logic for detecting duplicate recording sessions caused by orphaned
ffmpeg processes (see AGENT.md/CLAUDE.md: `taskkill /f /im ...exe` without
`/T` on Windows only kills the recorder process, not the ffmpeg children it
spawned -- the orphan keeps recording while a freshly restarted recorder
starts a brand new session for the same live room a few seconds later).

No filesystem access here on purpose -- `tools/find_duplicate_sessions.py`
does the `os.walk`/`os.path.getsize` IO and feeds plain data structures in,
so this module stays trivially unit-testable.

Recorder filenames look like ``<anchor>[_title]_<YYYY-MM-DD_HH-MM-SS>[_NNN].<ext>``
(see `main.py`'s `start_record`): the `<anchor>[_title]` prefix, then a fixed
timestamp taken once when the session starts, then an optional zero-padded
segment index when `split_video_by_time` is on, since ffmpeg's `-segment_time`
writes a new numbered file per split rather than reusing the same path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")
_INDEXED_TAIL_RE = re.compile(r"^_(?P<index>\d{3})\.(?P<ext>[^.]+)$")
_PLAIN_TAIL_RE = re.compile(r"^\.(?P<ext>[^.]+)$")


@dataclass(frozen=True)
class ParsedName:
    prefix: str
    start: datetime
    index: int | None
    ext: str


def parse_segment_filename(filename: str) -> ParsedName | None:
    """Split a recording filename into (anchor/title prefix, session start
    time, segment index or None, extension). Returns None for anything that
    doesn't look like a recorder output file."""
    m = _DATE_RE.search(filename)
    if not m:
        return None
    prefix = filename[: m.start()].rstrip("_")
    if not prefix:
        return None
    try:
        start = datetime.strptime(m.group(), "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return None

    rest = filename[m.end():]
    tail = _INDEXED_TAIL_RE.match(rest)
    if tail:
        return ParsedName(prefix=prefix, start=start, index=int(tail.group("index")), ext=tail.group("ext"))

    plain = _PLAIN_TAIL_RE.match(rest)
    if plain:
        return ParsedName(prefix=prefix, start=start, index=None, ext=plain.group("ext"))

    return None


@dataclass(frozen=True)
class Segment:
    path: str
    prefix: str
    start: datetime
    index: int | None
    size: int


@dataclass(frozen=True)
class Session:
    """One recording attempt: same anchor/title prefix, same exact start
    timestamp (ffmpeg only stamps it once), one or more segment files."""
    prefix: str
    start: datetime
    segments: tuple[Segment, ...] = field(default_factory=tuple)

    def size_by_index(self) -> dict[int | None, int]:
        return {s.index: s.size for s in self.segments}

    def total_size(self) -> int:
        return sum(s.size for s in self.segments)


def build_sessions(segments: list[Segment]) -> list[Session]:
    """Group raw segment files into sessions keyed by (prefix, start)."""
    grouped: dict[tuple[str, datetime], list[Segment]] = {}
    for seg in segments:
        grouped.setdefault((seg.prefix, seg.start), []).append(seg)

    sessions = [
        Session(prefix=prefix, start=start, segments=tuple(sorted(segs, key=lambda s: (s.index is None, s.index))))
        for (prefix, start), segs in grouped.items()
    ]
    sessions.sort(key=lambda s: (s.prefix, s.start))
    return sessions


@dataclass(frozen=True)
class DuplicateCandidate:
    session_a: Session
    session_b: Session
    gap_seconds: float
    compared_indices: int
    matched_indices: int

    @property
    def match_ratio(self) -> float:
        if self.compared_indices == 0:
            return 0.0
        return self.matched_indices / self.compared_indices


def find_duplicate_candidates(
    sessions: list[Session],
    max_gap_seconds: float = 90.0,
    size_tolerance: float = 0.02,
    min_match_ratio: float = 0.7,
    min_compared_indices: int = 3,
) -> list[DuplicateCandidate]:
    """Flag pairs of sessions of the same anchor/title that started within
    `max_gap_seconds` of each other AND whose overlapping segment indices
    have near-identical file sizes (within `size_tolerance` relative
    difference) -- the signature of the same live stream being recorded
    twice in parallel by an orphaned ffmpeg + a freshly restarted recorder.

    Same-anchor sessions that merely started close together but whose
    segment sizes diverge (e.g. a stream that genuinely dropped and was
    re-picked-up) are not flagged.
    """
    by_prefix: dict[str, list[Session]] = {}
    for s in sessions:
        by_prefix.setdefault(s.prefix, []).append(s)

    candidates: list[DuplicateCandidate] = []
    for prefix, group in by_prefix.items():
        group = sorted(group, key=lambda s: s.start)
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                gap = (b.start - a.start).total_seconds()
                if gap > max_gap_seconds:
                    break  # sorted by start; nothing further in range either
                if gap < 0:
                    continue

                sizes_a = a.size_by_index()
                sizes_b = b.size_by_index()
                shared_indices = [idx for idx in sizes_a if idx is not None and idx in sizes_b]
                compared = len(shared_indices)
                if compared < min_compared_indices:
                    continue

                matched = 0
                for idx in shared_indices:
                    size_a, size_b = sizes_a[idx], sizes_b[idx]
                    largest = max(size_a, size_b, 1)
                    if abs(size_a - size_b) / largest <= size_tolerance:
                        matched += 1

                candidate = DuplicateCandidate(
                    session_a=a, session_b=b, gap_seconds=gap,
                    compared_indices=compared, matched_indices=matched,
                )
                if candidate.match_ratio >= min_match_ratio:
                    candidates.append(candidate)

    return candidates
