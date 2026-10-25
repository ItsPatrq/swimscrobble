"""Sort owned tracks into variety buckets from Last.fm history, and list what is wanted but missing.

obsession  played in the last 30 days (plus anything in your playlist)
mid        played 30-90 days ago, not lately
fresh      known artist, track you have (apparently) never played
lost       loved / heavily played all-time, silent for 90+ days
rec        similar tracks and top tracks of your genres, from artists you may not know
"""
from __future__ import annotations

import math

from .generator import Candidate
from .library import Track
from .link import LocalIndex
from .matcher import norm
from .taste import Taste
from .ytm import Entry

BUCKETS = ("obsession", "mid", "fresh", "lost", "rec")
KNOWN_ARTIST_PLAYS = 3  # all-time plays before an artist counts as "known"


def _scale(n: int, peak: int) -> float:
    return math.log1p(n) / math.log1p(peak or 1)


def assign(
    taste: Taste | None,
    playlist: list[Entry],
    library: list[Track],
    idx: LocalIndex | None = None,
) -> tuple[list[Candidate], list[Candidate]]:
    """Return (owned candidates keyed by local tags, wanted-but-missing candidates)."""
    idx = idx or LocalIndex(library)
    taste = taste or Taste()
    taken: dict[tuple[str, str], Candidate] = {}
    wanted: dict[tuple[str, str], Candidate] = {}

    def local(artist: str, title: str, duration: float | None = None) -> Track | None:
        return idx.find(artist, title, duration)

    def lkey(t: Track) -> tuple[str, str]:
        return (t.artist.casefold(), t.title.casefold())

    def put(t: Track, bucket: str, score: float) -> None:
        k = lkey(t)
        if k not in taken:  # first (highest priority) bucket wins
            taken[k] = Candidate(t.artist, t.title, score, {bucket})

    def want(artist: str, title: str, bucket: str, score: float) -> None:
        k = (norm(artist), norm(title))
        if k not in wanted:
            wanted[k] = Candidate(artist, title, score, {bucket})

    def chart_keys(chart, bucket: str) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for a, t, n in chart:
            tr = local(a, t)
            if tr:
                out[lkey(tr)] = out.get(lkey(tr), 0) + n
            else:
                want(a, t, bucket, 0.5)
        return out

    c30 = chart_keys(taste.chart30, "obsession")
    c60, c90 = chart_keys(taste.chart60, "mid"), chart_keys(taste.chart90, "mid")
    by_key = {lkey(t): t for t in library}
    playlist_keys = {lkey(t) for e in playlist if (t := local(e.artist, e.title, e.duration))}

    # 1. recent obsession (+ playlist tracks)
    peak30 = max(c30.values(), default=0)
    for k in sorted(set(c30) | playlist_keys):
        if k in c30:
            s = 0.5 + 0.5 * _scale(c30[k], peak30) + (0.3 if k in playlist_keys else 0.0)
        else:
            s = 0.6  # playlist-only: curated by you, but not played lately
        put(by_key[k], "obsession", s)

    # 2. 30-90 days ago
    older: dict[tuple[str, str], int] = {}
    for chart in (c60, c90):
        for k, n in chart.items():
            older[k] = older.get(k, 0) + n
    peak_old = max(older.values(), default=0)
    for k, n in older.items():
        put(by_key[k], "mid", 0.4 + 0.4 * _scale(n, peak_old))

    # 3. lost gems: loved or heavily played all-time, but not in the last 90 days
    recent = set(c30) | set(c60) | set(c90)
    overall = chart_keys(taste.overall, "lost")
    loved = {lkey(t) for a, ti in taste.loved if (t := local(a, ti))}
    peak_all = max(overall.values(), default=0)
    for k in set(overall) | loved:
        if k in recent:
            continue
        s = 0.3 + 0.5 * _scale(overall.get(k, 0), peak_all) + (0.2 if k in loved else 0.0)
        put(by_key[k], "lost", s)

    # 4. unplayed tracks by artists you know
    artist_plays: dict[str, int] = {}
    for chart in (taste.overall, taste.chart30, taste.chart60, taste.chart90):
        for a, _, n in chart:
            artist_plays[norm(a)] = artist_plays.get(norm(a), 0) + n
    known = {a for a, n in artist_plays.items() if n >= KNOWN_ARTIST_PLAYS}
    peak_artist = max(artist_plays.values(), default=0)
    famous = {
        (norm(a), norm(t)) for tracks in taste.artist_tracks.values() for a, t in tracks
    }
    played = set(overall) | recent
    for k, tr in by_key.items():
        a = norm(tr.artist)
        if k in played or k in taken or a not in known:
            continue
        s = 0.3 + 0.4 * _scale(artist_plays[a], peak_artist) + (0.2 if (a, norm(tr.title)) in famous else 0.0)
        put(tr, "fresh", s)
    for tracks in taste.artist_tracks.values():
        for a, t in tracks:
            if not local(a, t):
                want(a, t, "fresh", 0.4)

    # 5. recommendations: similar tracks + genre top tracks
    for a, t, m in taste.similar:
        tr = local(a, t)
        if tr:
            put(tr, "rec", 0.3 + 0.5 * m)
        else:
            want(a, t, "rec", 0.3 + 0.5 * m)
    for a, t in taste.genre_tracks:
        tr = local(a, t)
        if tr:
            put(tr, "rec", 0.4)
        else:
            want(a, t, "rec", 0.4)

    # a wish that turned out to be owned (matched under another bucket) is not missing
    wishes = [c for k, c in wanted.items() if not local(c.artist, c.title)]
    return sorted(taken.values(), key=lambda c: -c.score), sorted(wishes, key=lambda c: -c.score)
