"""Turn a played session into Last.fm-style scrobble records."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta

from .planner import PlannedTrack


@dataclass(frozen=True)
class Scrobble:
    timestamp: datetime
    artist: str
    title: str
    album: str
    duration: int


def build_scrobbles(plan: list[PlannedTrack], start: datetime) -> list[Scrobble]:
    """Back-to-back playback from `start`; a scrobble's timestamp is when the track began."""
    out = []
    t = start
    for p in plan:
        out.append(
            Scrobble(t, p.track.artist, p.track.title, p.track.album, round(p.track.duration))
        )
        t += timedelta(seconds=p.track.duration)
    return out


def to_csv(scrobbles: list[Scrobble]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "artist", "track", "album", "duration"])
    for s in scrobbles:
        w.writerow([s.timestamp.isoformat(timespec="seconds"), s.artist, s.title, s.album, s.duration])
    return buf.getvalue()
