"""Session manifests: the contract between planning and scrobbling."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .library import Track
from .planner import PlannedTrack


def to_manifest(plan: list[PlannedTrack], seed: int | None, minutes: float) -> dict:
    return {
        "created": datetime.now().isoformat(timespec="seconds"),
        "seed": seed,
        "minutes": minutes,
        "tracks": [
            {"position": p.position, "file": str(p.track.path), "staged": p.staged_name,
             "artist": p.track.artist, "title": p.track.title, "album": p.track.album,
             "duration": round(p.track.duration)}
            for p in plan
        ],
    }


def from_manifest(manifest: dict) -> list[PlannedTrack]:
    return [
        PlannedTrack(t["position"], Track(Path(t["file"]), t["artist"], t["title"], t["album"], t["duration"]))
        for t in manifest["tracks"]
    ]
