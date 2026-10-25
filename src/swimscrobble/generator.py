"""Score candidate tracks from playlist membership, Last.fm taste and similarity."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

W_PLAYLIST, W_LOVED, W_TOP, W_SIMILAR = 1.0, 0.8, 0.6, 0.5

Key = tuple[str, str]


def key(artist: str, title: str) -> Key:
    return (artist.casefold().strip(), title.casefold().strip())


@dataclass
class Candidate:
    artist: str
    title: str
    score: float
    sources: set[str]


def score_candidates(
    playlist: list[Key],
    loved: list[Key],
    top: list[tuple[str, str, int]],
    similar: list[tuple[str, str, float]],
    display: dict[Key, tuple[str, str]] | None = None,
) -> list[Candidate]:
    """Weighted sum per track, ranked high to low. `display` maps keys back to original casing."""
    scores: dict[Key, float] = defaultdict(float)
    sources: dict[Key, set[str]] = defaultdict(set)

    def add(k: Key, w: float, src: str):
        scores[k] += w
        sources[k].add(src)

    for k in playlist:
        add(k, W_PLAYLIST, "playlist")
    for a, t in loved:
        add(key(a, t), W_LOVED, "loved")
    peak = max((n for _, _, n in top), default=0)
    for a, t, n in top:
        # log scaling so one heavily played song does not drown everything
        add(key(a, t), W_TOP * math.log1p(n) / math.log1p(peak or 1), "top")
    for a, t, m in similar:
        add(key(a, t), W_SIMILAR * m, "similar")

    display = display or {}
    out = [
        Candidate(*display.get(k, k), score=s, sources=sources[k]) for k, s in scores.items()
    ]
    return sorted(out, key=lambda c: -c.score)


def pool_weights(
    cands: list[Candidate], swim_plays: dict[Key, int], floor: float = 0.05
) -> dict[Key, float]:
    """Sampling weights for the planner: taste score, damped by how often it was swum already."""
    return {
        key(c.artist, c.title): max(floor, c.score) / (1 + swim_plays.get(key(c.artist, c.title), 0))
        for c in cands
    }
