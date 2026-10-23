"""Pure logic for detecting an anchor rename: the same live-room URL recorded
under two (or more) different display names over time, because the streamer
changed their nickname on the platform, or someone edited the pinned
`主播: xxx` annotation in URL_config.ini via the web UI's streamer editor.

Symptom this catches: `folder_by_author` names each recording's folder after
the anchor name *at record time* (see main.py's `start_record()`), so a
rename silently splits one real streamer's history across two folders with
no link between them -- the URL is the only thing that stays constant.

No filesystem/DB access here on purpose -- `tools/find_anchor_renames.py`
reads `config/recording_history.db` and does the `shutil.move`/`UPDATE`, and
feeds plain data structures in, so this module stays trivially unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .library import canonical_anchor_name


@dataclass(frozen=True)
class SessionRecord:
    """One row from `recording_sessions` (see src/duration_tracker.py):
    `anchor` is the RAW `anchor_name` column value (main.py's record_name,
    e.g. "序号3 京圈太子" -- still carrying the "序号N " disambiguation
    prefix), not yet run through `canonical_anchor_name()`."""
    url: str
    anchor: str
    start_time: datetime
    platform: str = ""


@dataclass(frozen=True)
class RenameEvent:
    """One previously-used display name for `url` that isn't the name its
    most recent recording used -- a candidate to merge into `new_name`."""
    url: str
    old_name: str
    new_name: str
    old_last_seen: datetime
    new_first_seen: datetime
    platform: str = ""


def find_rename_events(records: list[SessionRecord]) -> list[RenameEvent]:
    """For each URL, treat the display name of its chronologically LAST
    recording as "current" and flag every other distinct display name that
    URL has ever used as a rename candidate to merge into it.

    Deliberately not "walk the timeline and flag each consecutive change":
    if a streamer flips between two names repeatedly, consolidating
    everything into whichever name is currently active is still the useful
    answer (their whole history under one folder), and it sidesteps having
    to decide whether an old name flipping back counts as a second event.
    """
    by_url: dict[str, list[SessionRecord]] = {}
    for r in records:
        if not r.url:
            continue
        by_url.setdefault(r.url, []).append(r)

    events: list[RenameEvent] = []
    for url, recs in by_url.items():
        recs = sorted(recs, key=lambda r: r.start_time)
        current = recs[-1]
        current_name = canonical_anchor_name(current.anchor)
        if not current_name:
            continue

        by_name: dict[str, list[SessionRecord]] = {}
        for r in recs:
            name = canonical_anchor_name(r.anchor)
            if name:
                by_name.setdefault(name, []).append(r)

        new_first_seen = min(r.start_time for r in by_name[current_name])
        for name, name_recs in by_name.items():
            if name == current_name:
                continue
            events.append(RenameEvent(
                url=url, old_name=name, new_name=current_name,
                old_last_seen=max(r.start_time for r in name_recs),
                new_first_seen=new_first_seen,
                platform=current.platform,
            ))

    return events
