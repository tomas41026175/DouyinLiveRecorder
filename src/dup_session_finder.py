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


def cluster_duplicate_sessions(candidates: list[DuplicateCandidate]) -> list[list[Session]]:
    """Group sessions connected (directly or transitively) by a duplicate
    edge into clusters, e.g. A~B and B~C (but not A~C directly, if the gap
    between A and C exceeds max_gap_seconds) still form one 3-way cluster
    {A, B, C}, since they're all the same repeated recording."""
    parent: dict[Session, Session] = {}

    def find(x: Session) -> Session:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: Session, b: Session) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for c in candidates:
        union(c.session_a, c.session_b)

    groups: dict[Session, list[Session]] = {}
    seen: set[Session] = set()
    for c in candidates:
        for s in (c.session_a, c.session_b):
            if s in seen:
                continue
            seen.add(s)
            groups.setdefault(find(s), []).append(s)
    return list(groups.values())


@dataclass(frozen=True)
class QuarantinePlan:
    """One duplicate cluster resolved into a session to keep and the
    session(s) to move aside. Nothing here touches the filesystem --
    `tools/find_duplicate_sessions.py` does the actual `shutil.move`."""
    keep: Session
    remove: tuple[Session, ...]


def plan_quarantine(candidates: list[DuplicateCandidate], keep: str = "largest") -> list[QuarantinePlan]:
    """Decide, per duplicate cluster, which single session to keep and which
    to move to quarantine.

    keep="largest" (default): keep the session with the most total recorded
    bytes -- the most complete copy regardless of which process (the
    orphaned ffmpeg or the freshly restarted one) happened to start it.
    keep="earliest": keep whichever session started recording first --
    matches the orphaned-ffmpeg mechanism directly (the pre-restart process
    was already recording before the post-restart one started a redundant
    copy), but can pick a truncated recording over a more complete one if
    the earlier session was itself cut short for an unrelated reason.

    Either way this only decides a *plan*; nothing is deleted, and the
    "remove" side is meant to be moved to a quarantine folder for the user
    to confirm before any manual deletion.
    """
    if keep not in ("largest", "earliest"):
        raise ValueError(f"keep must be 'largest' or 'earliest', got {keep!r}")

    plans = []
    for cluster in cluster_duplicate_sessions(candidates):
        if keep == "largest":
            ordered = sorted(cluster, key=lambda s: (-s.total_size(), s.start))
        else:
            ordered = sorted(cluster, key=lambda s: (s.start, -s.total_size()))
        plans.append(QuarantinePlan(keep=ordered[0], remove=tuple(ordered[1:])))
    return plans
