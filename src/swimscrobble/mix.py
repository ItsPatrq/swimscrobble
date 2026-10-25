"""Build an energising, semi-random session: mostly favourites, a few recommendations.

Favourites = tracks in the playlist, your Last.fm top tracks of the recent window, or loved.
Recommendations = tracks that only showed up as "similar" to those favourites.
Slow tracks (low audio energy) are dropped; the rest are sampled with weight
taste_score * energy^2 / (1 + times already swum), ordered by a randomised walk that avoids big
energy jumps and back-to-back artists, with recommendations spread evenly through the session.
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from .energy import Features
from .generator import Candidate, Key
from .library import Track
from .planner import PlannedTrack, dedupe, key_of

UNKNOWN_TASTE_WEIGHT = 0.1  # only used when there is no taste pool at all


@dataclass
class Item:
    track: Track
    kind: str  # "fav" | "rec"
    energy: float
    weight: float


def _sample(items: list[Item], budget: float, rng: random.Random) -> tuple[list[Item], list[Item]]:
    """Weighted draw (Efraimidis-Spirakis) until the budget is full. Returns (chosen, leftover)."""
    ranked = sorted(items, key=lambda i: rng.random() ** (1 / max(i.weight, 1e-9)), reverse=True)
    chosen, left, total = [], [], 0.0
    for it in ranked:
        if total + it.track.duration <= budget:
            chosen.append(it)
            total += it.track.duration
        else:
            left.append(it)
    return chosen, left


def order_by_flow(items: list[Item], rng: random.Random, jump: float = 0.25) -> list[Item]:
    """Randomised walk: next pick prefers a similar energy to the previous one, never the same artist."""
    pool, out = list(items), []
    while pool:
        prev = out[-1] if out else None
        w = []
        for it in pool:
            m = it.weight
            if prev:
                if abs(it.energy - prev.energy) > jump:
                    m *= 0.3
                if it.track.artist and it.track.artist == prev.track.artist:
                    m *= 0.15
            w.append(m)
        out.append(pool.pop(rng.choices(range(len(pool)), weights=w)[0]))
    return out


def interleave(favs: list[Item], recs: list[Item], rng: random.Random) -> list[Item]:
    """Spread recommendations roughly evenly (with jitter) so they never clump at one end."""
    n = len(favs) + len(recs)
    slots: list[int] = []
    for j in range(len(recs)):
        pos = round((j + 1) * n / (len(recs) + 1)) - 1 + rng.choice([-1, 0, 1])
        pos = min(max(pos, 1 if n > 2 else 0), n - 1)
        while pos in slots:
            pos = pos + 1 if pos + 1 < n else pos - 1
        slots.append(pos)
    out: list[Item | None] = [None] * n
    for pos, rec in zip(sorted(slots), recs, strict=True):
        out[pos] = rec
    it = iter(favs)
    return [x if x is not None else next(it) for x in out]


def build_mix(
    library: list[Track],
    candidates: list[Candidate],
    feature_of: Callable[[Track], Features],
    *,
    minutes: float = 60,
    seed: int | None = None,
    rec_ratio: float = 0.2,
    min_energy: float = 0.5,
    plays: dict[Key, int] | None = None,
    recent: set[Key] | None = None,
    jump: float = 0.25,
) -> list[PlannedTrack]:
    rng = random.Random(seed)
    plays, recent = plays or {}, recent or set()
    cmap = {(c.artist.casefold(), c.title.casefold()): c for c in candidates}

    favs: list[Item] = []
    recs: list[Item] = []
    stale: list[Item] = []  # recently swum: only used if the fresh pool can't fill the buffer
    for t in dedupe(library):
        k = key_of(t)
        c = cmap.get(k)
        if cmap and c is None:
            continue  # a taste pool exists: only tracks from the playlist / Last.fm are eligible
        f = feature_of(t)
        if f.energy < min_energy:
            continue
        is_rec = c is not None and c.sources == {"similar"}
        taste = c.score if c else UNKNOWN_TASTE_WEIGHT
        item = Item(t, "rec" if is_rec else "fav", f.energy, taste * f.energy**2 / (1 + plays.get(k, 0)))
        (stale if k in recent else recs if is_rec else favs).append(item)

    budget = minutes * 60
    rec_budget = budget * rec_ratio if recs else 0.0
    chosen_recs, spare_recs = _sample(recs, rec_budget, rng)
    chosen_favs, spare_favs = _sample(favs, budget - sum(i.track.duration for i in chosen_recs), rng)

    # top up any slack (e.g. too few recommendations) from what was left over, favourites first
    used = sum(i.track.duration for i in chosen_recs + chosen_favs)
    stale.sort(key=lambda i: rng.random())
    for it in spare_favs + spare_recs + stale:
        if used + it.track.duration <= budget:
            (chosen_recs if it.kind == "rec" else chosen_favs).append(it)
            used += it.track.duration

    ordered = interleave(order_by_flow(chosen_favs, rng, jump), order_by_flow(chosen_recs, rng, jump), rng)
    return [PlannedTrack(i + 1, it.track, it.kind, round(it.energy, 2)) for i, it in enumerate(ordered)]
