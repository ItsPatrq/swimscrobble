"""Build a varied, energising session from several buckets of taste.

Each bucket (recent obsession, 30-90 day history, unplayed tracks by known artists, lost gems,
recommendations) gets a share of the session. Inside a bucket, tracks are drawn at random with a
weight of taste score * energy^2 (flattened, so favourites do not always win), at most
`max_per_artist` per artist. The order is a randomised walk that avoids big energy jumps, repeated
artists and runs of the same bucket.
"""
from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from .energy import Features
from .generator import Candidate, Key
from .library import Track
from .planner import PlannedTrack, dedupe, key_of

DEFAULT_QUOTAS = {"obsession": 0.40, "mid": 0.15, "fresh": 0.15, "lost": 0.10, "rec": 0.20}
BUCKET_ALIASES = {"playlist": "obsession", "top": "obsession", "loved": "obsession", "similar": "rec"}
UNKNOWN_TASTE_WEIGHT = 0.1  # only used when there is no taste pool at all
FLATTEN = 0.6  # taste ** FLATTEN: <1 flattens differences so the same favourites do not always win


@dataclass
class Item:
    track: Track
    bucket: str
    energy: float
    weight: float  # taste score, damped by how often it was already swum

    @property
    def draw_weight(self) -> float:
        """Taste is flattened for variety; energy is not, so workouts stay energising."""
        return self.weight**FLATTEN * self.energy**2


def bucket_of(c: Candidate) -> str:
    for s in sorted(c.sources):
        b = BUCKET_ALIASES.get(s, s)
        if b in DEFAULT_QUOTAS:
            return b
    return "obsession"


def _draw(items: list[Item], budget: float, rng: random.Random, counts: Counter, cap: int | None):
    """Random weighted draw into `budget` seconds. Returns (chosen, leftover)."""
    ranked = sorted(items, key=lambda i: rng.random() ** (1 / max(i.draw_weight, 1e-9)), reverse=True)
    chosen, left, total = [], [], 0.0
    for it in ranked:
        artist = it.track.artist.casefold()
        if total + it.track.duration <= budget and (cap is None or counts[artist] < cap):
            chosen.append(it)
            total += it.track.duration
            counts[artist] += 1
        else:
            left.append(it)
    return chosen, left


def order_by_flow(items: list[Item], rng: random.Random, jump: float = 0.25) -> list[Item]:
    """Randomised walk: avoid energy jumps, the same artist twice, and long runs of one bucket."""
    pool, out = list(items), []
    while pool:
        w = []
        for it in pool:
            m = it.draw_weight
            if out:
                prev = out[-1]
                if abs(it.energy - prev.energy) > jump:
                    m *= 0.3
                if it.track.artist and it.track.artist == prev.track.artist:
                    m *= 0.15
                if it.bucket == prev.bucket:
                    m *= 0.6
                    if len(out) > 1 and out[-2].bucket == it.bucket:
                        m *= 0.35
            w.append(m)
        out.append(pool.pop(rng.choices(range(len(pool)), weights=w)[0]))
    return out


def build_mix(
    library: list[Track],
    candidates: list[Candidate],
    feature_of: Callable[[Track], Features],
    *,
    minutes: float = 120,
    seed: int | None = None,
    quotas: dict[str, float] | None = None,
    min_energy: float = 0.5,
    plays: dict[Key, int] | None = None,
    recent: set[Key] | None = None,
    jump: float = 0.25,
    max_per_artist: int = 3,
) -> list[PlannedTrack]:
    rng = random.Random(seed)
    plays, recent = plays or {}, recent or set()
    cmap = {(c.artist.casefold(), c.title.casefold()): c for c in candidates}
    quotas = dict(quotas or DEFAULT_QUOTAS)

    by_bucket: dict[str, list[Item]] = {}
    stale: list[Item] = []  # recently swum: last resort, only to fill the buffer
    for t in dedupe(library):
        k = key_of(t)
        c = cmap.get(k)
        if cmap and c is None:
            continue  # a taste pool exists: only tracks it lists are eligible
        f = feature_of(t)
        if f.energy < min_energy:
            continue
        bucket = bucket_of(c) if c else "other"
        taste = c.score if c else UNKNOWN_TASTE_WEIGHT
        item = Item(t, bucket, f.energy, taste / (1 + plays.get(k, 0)))
        (stale if k in recent else by_bucket.setdefault(bucket, [])).append(item)

    if "other" in by_bucket:
        quotas = {"other": 1.0}
    live = {b: w for b, w in quotas.items() if by_bucket.get(b)}
    total_w = sum(live.values()) or 1.0
    budget = minutes * 60
    counts: Counter = Counter()
    chosen: list[Item] = []
    leftover: list[Item] = []
    for b, w in live.items():
        got, left = _draw(by_bucket[b], budget * w / total_w, rng, counts, max_per_artist)
        chosen += got
        leftover += left
    for b in by_bucket:  # buckets without a quota (should not happen) still feed the top-up
        if b not in live:
            leftover += by_bucket[b]

    def top_up(pool: list[Item], cap: int | None) -> list[Item]:
        used = sum(i.track.duration for i in chosen)
        got, left = _draw(pool, budget - used, rng, counts, cap)
        chosen.extend(got)
        return left

    leftover = top_up(leftover, max_per_artist)  # buckets that came up short hand their time to others
    leftover = top_up(leftover, None)  # then relax the per-artist cap
    top_up(stale, None)  # finally recently swum tracks, so the buffer is always full

    ordered = order_by_flow(chosen, rng, jump)
    return [PlannedTrack(i + 1, it.track, it.bucket, round(it.energy, 2)) for i, it in enumerate(ordered)]
