"""Build a randomised session that fits a time budget.

The headphones play files in alphabetical order, so the plan assigns each
chosen track a zero-padded position prefix. Alphabetical order == play order.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .library import Track


@dataclass(frozen=True)
class PlannedTrack:
    position: int
    track: Track

    @property
    def staged_name(self) -> str:
        return f"{self.position:03d} - {self.track.path.name}"


def plan_session(
    tracks: list[Track],
    minutes: float = 60,
    seed: int | None = None,
    avoid_repeat_artist: bool = True,
) -> list[PlannedTrack]:
    """Shuffle, then greedily take every track that still fits the budget."""
    rng = random.Random(seed)
    pool = list(tracks)
    rng.shuffle(pool)
    budget = minutes * 60
    chosen: list[Track] = []
    total = 0.0
    for t in pool:
        if total + t.duration <= budget:
            chosen.append(t)
            total += t.duration
    if avoid_repeat_artist:
        chosen = _spread_artists(chosen)
    return [PlannedTrack(i + 1, t) for i, t in enumerate(chosen)]


def _spread_artists(tracks: list[Track]) -> list[Track]:
    """Best-effort: avoid the same artist back to back, keeping shuffle otherwise."""
    out: list[Track] = []
    rest = list(tracks)
    while rest:
        prev = out[-1].artist if out else None
        idx = next(
            (i for i, t in enumerate(rest) if not t.artist or t.artist != prev), 0
        )
        out.append(rest.pop(idx))
    return out


def total_seconds(plan: list[PlannedTrack]) -> float:
    return sum(p.track.duration for p in plan)
