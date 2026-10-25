"""Sort tracks into variety buckets by checking each against your Last.fm history.

obsession  played in the last 30 days
mid        played 30-90 days ago, not since
lost       played or loved long ago (nothing in the last 90 days)
fresh      never played, but by an artist you do listen to
rec        never played, by an artist you have not played either (discovery)

The pool is chosen first (your YouTube Music playlist(s), or the whole library) and every track in it
is then judged against the history, so nothing needs to be in a "top" list to be found.
"""
from __future__ import annotations

import math
import re

from .generator import Candidate
from .lfhistory import Stat
from .library import Track
from .link import LocalIndex
from .matcher import norm
from .mix import DEFAULT_QUOTAS
from .taste import Taste

BUCKETS = ("obsession", "mid", "fresh", "lost", "rec")
WISH_MIN_PLAYS = 3
WISH_MAX_AGE = 365 * 86400  # only ask for tracks you still listen to, not what you played in 2014
MAX_WISHES = 300  # split between buckets in proportion to their mix share
# podcast / video / long-form titles that end up in Last.fm history but are not songs
NOT_A_SONG = re.compile(r"#\d+|\b(episode|odcinek|ep\.)\s*\d+|\||\bpodcast\b", re.I)


def looks_like_song(title: str) -> bool:
    return len(title) <= 70 and not NOT_A_SONG.search(title)
KNOWN_ARTIST_PLAYS = 3  # all-time plays before an artist counts as "known"


def _scale(n: int, peak: int) -> float:
    return math.log1p(n) / math.log1p(peak or 1)


def _bucket(st: Stat | None, artist_plays: int, loved: bool) -> str:
    if st and st.p30:
        return "obsession"
    if st and (st.p60 or st.p90):
        return "mid"
    if (st and st.total) or loved:
        return "lost"
    return "fresh" if artist_plays >= KNOWN_ARTIST_PLAYS else "rec"


def assign(
    taste: Taste | None,
    history: dict[tuple[str, str], Stat],
    library: list[Track],
    pool: set[tuple[str, str]] | None = None,
    idx: LocalIndex | None = None,
    now: float | None = None,
) -> tuple[list[Candidate], list[Candidate]]:
    """Return (candidates for owned pool tracks, wanted-but-not-owned candidates).

    `pool` holds local keys (casefolded artist, title) eligible for the session; None = whole library.
    """
    idx = idx or LocalIndex(library)
    taste = taste or Taste()
    lkey = lambda t: (t.artist.casefold(), t.title.casefold())  # noqa: E731

    # Last.fm spells artists/titles its own way: fold every history row onto the local file it matches
    owned_stat: dict[tuple[str, str], Stat] = {}
    foreign: dict[tuple[str, str], Stat] = {}  # played on Last.fm but no local file
    artist_plays: dict[str, int] = {}
    for (a, t), st in history.items():
        artist_plays[norm(a)] = artist_plays.get(norm(a), 0) + st.total
        tr = idx.find(a, t)
        if tr:
            k = lkey(tr)
            old = owned_stat.get(k)
            owned_stat[k] = st if not old else Stat(
                old.total + st.total, old.p30 + st.p30, old.p60 + st.p60, old.p90 + st.p90, max(old.last, st.last)
            )
        else:
            foreign[(a, t)] = st

    loved = {lkey(tr) for a, t in taste.loved if (tr := idx.find(a, t))}
    boost: dict[tuple[str, str], float] = {}
    for a, t, m in taste.similar:
        if tr := idx.find(a, t):
            boost[lkey(tr)] = max(boost.get(lkey(tr), 0.0), 0.3 + 0.5 * m)
    for a, t in taste.genre_tracks:
        if tr := idx.find(a, t):
            boost[lkey(tr)] = max(boost.get(lkey(tr), 0.0), 0.4)
    famous = {(norm(a), norm(t)) for tracks in taste.artist_tracks.values() for a, t in tracks}

    peaks = {
        "obsession": max((s.p30 for s in owned_stat.values()), default=0),
        "mid": max((s.p60 + s.p90 for s in owned_stat.values()), default=0),
        "lost": max((s.total for s in owned_stat.values()), default=0),
        "fresh": max(artist_plays.values(), default=0),
    }

    def score(bucket: str, st: Stat | None, artist: str, title: str, is_loved: bool, extra: float) -> float:
        if bucket == "obsession":
            return 0.5 + 0.5 * _scale(st.p30, peaks[bucket])
        if bucket == "mid":
            return 0.4 + 0.4 * _scale(st.p60 + st.p90, peaks[bucket])
        if bucket == "lost":
            total = st.total if st else 0
            return 0.3 + 0.5 * _scale(total, peaks[bucket]) + (0.2 if is_loved else 0.0)
        if bucket == "fresh":
            return 0.3 + 0.4 * _scale(artist_plays.get(norm(artist), 0), peaks[bucket]) + (
                0.2 if (norm(artist), norm(title)) in famous else 0.0)
        return max(0.4, extra)  # rec: discovery, boosted when Last.fm calls it similar

    owned: list[Candidate] = []
    for t in library:
        k = lkey(t)
        if pool is not None and k not in pool:
            continue
        st = owned_stat.get(k)
        a_plays = artist_plays.get(norm(t.artist), 0)
        b = _bucket(st, a_plays, k in loved)
        owned.append(Candidate(t.artist, t.title, score(b, st, t.artist, t.title, k in loved, boost.get(k, 0.0)), {b}))

    # wishes: what you clearly like (or Last.fm suggests) but have no file for
    wanted: dict[tuple[str, str], Candidate] = {}

    def want(a: str, t: str, bucket: str, s: float) -> None:
        wanted.setdefault((norm(a), norm(t)), Candidate(a, t, s, {bucket}))

    for (a, t), st in foreign.items():
        recent_enough = now is None or st.last >= now - WISH_MAX_AGE
        if st.total >= WISH_MIN_PLAYS and recent_enough and looks_like_song(t):
            b = _bucket(st, artist_plays.get(norm(a), 0), False)
            want(a, t, b, score(b, st, a, t, False, 0.0))
    for a, t, m in taste.similar:
        if not idx.find(a, t) and (a, t) not in history:
            want(a, t, "rec", 0.3 + 0.5 * m)
    for a, t in taste.genre_tracks:
        if not idx.find(a, t) and (a, t) not in history:
            want(a, t, "rec", 0.4)
    for tracks in taste.artist_tracks.values():
        for a, t in tracks:
            if not idx.find(a, t) and (a, t) not in history:
                want(a, t, "fresh", 0.4)
    # cap per bucket (not overall), otherwise heavily played tracks crowd out discoveries
    shares = DEFAULT_QUOTAS
    per_bucket: dict[str, list[Candidate]] = {}
    for c in sorted(wanted.values(), key=lambda c: -c.score):
        per_bucket.setdefault(next(iter(c.sources)), []).append(c)
    wishes = sorted(
        (c for b, cs in per_bucket.items() for c in cs[: max(10, round(MAX_WISHES * shares.get(b, 0.1)))]),
        key=lambda c: -c.score,
    )
    return sorted(owned, key=lambda c: -c.score), wishes
