"""Read-only Last.fm taste data (top/loved tracks, similar tracks)."""
from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import secret
from .lastfm import API_URL, check
from .net import request_json
Transport = Callable[[dict], dict]


def http_get(params: dict) -> dict:
    return request_json(f"{API_URL}?{urllib.parse.urlencode(params)}")


def _get(method: str, transport: Transport, **params) -> dict:
    key = secret("LASTFM_API_KEY")
    if not key:
        raise SystemExit("set LASTFM_API_KEY")
    return check(transport({"method": method, "api_key": key, "format": "json", **params}))


def top_tracks(user: str, period: str = "6month", limit: int = 200, transport: Transport = http_get):
    d = _get("user.getTopTracks", transport, user=user, period=period, limit=limit)
    return [
        (t["artist"]["name"], t["name"], int(t.get("playcount", 0)))
        for t in d.get("toptracks", {}).get("track", [])
    ]


def loved_tracks(user: str, limit: int = 200, transport: Transport = http_get):
    d = _get("user.getLovedTracks", transport, user=user, limit=limit)
    return [(t["artist"]["name"], t["name"]) for t in d.get("lovedtracks", {}).get("track", [])]


def similar(artist: str, title: str, limit: int = 20, transport: Transport = http_get):
    d = _get("track.getSimilar", transport, artist=artist, track=title, limit=limit, autocorrect=1)
    return [
        (t["artist"]["name"], t["name"], float(t.get("match", 0)))
        for t in d.get("similartracks", {}).get("track", [])
    ]


# --- richer taste data: dated windows, lost gems, known artists, genres -------------------------

GENERIC_TAGS = {
    "seen live", "favorites", "favourites", "favourite", "albums i own", "male vocalists",
    "female vocalists", "under 2000 listeners", "all", "good", "awesome", "love", "my music",
}


def artist_top_tracks(artist: str, limit: int = 10, transport: Transport = http_get) -> list[tuple[str, str]]:
    d = _get("artist.getTopTracks", transport, artist=artist, limit=limit, autocorrect=1)
    return [(t["artist"]["name"], t["name"]) for t in d.get("toptracks", {}).get("track", [])]


def artist_top_tags(artist: str, limit: int = 5, transport: Transport = http_get) -> list[str]:
    d = _get("artist.getTopTags", transport, artist=artist, autocorrect=1)
    return [t["name"].casefold() for t in d.get("toptags", {}).get("tag", [])][:limit]


def tag_top_tracks(tag: str, limit: int = 30, transport: Transport = http_get) -> list[tuple[str, str]]:
    d = _get("tag.getTopTracks", transport, tag=tag, limit=limit)
    return [(t["artist"]["name"], t["name"]) for t in d.get("tracks", {}).get("track", [])]


@dataclass
class Taste:
    """What Last.fm knows beyond your own history: loved tracks and who/what is similar."""

    loved: list[tuple[str, str]] = field(default_factory=list)
    similar: list[tuple[str, str, float]] = field(default_factory=list)  # similar to the recent obsession
    artist_tracks: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # known artist -> top tracks
    genre_tracks: list[tuple[str, str]] = field(default_factory=list)  # top tracks of your genres


def fetch_taste(
    user: str,
    recent_tracks: list[tuple[str, str]],
    recent_artists: list[str],
    transport: Transport = http_get,
    *,
    artists: int = 8,
    tags: int = 3,
) -> Taste:
    """Seeded by what you played lately (computed from your local history copy)."""
    t = Taste(loved=loved_tracks(user, transport=transport))
    for artist, title in recent_tracks:
        t.similar += similar(artist, title, transport=transport)
    for artist in recent_artists[:artists]:
        t.artist_tracks[artist] = artist_top_tracks(artist, transport=transport)
    genres: list[str] = []
    for artist in recent_artists[:5]:
        for tag in artist_top_tags(artist, transport=transport):
            if tag not in GENERIC_TAGS and tag not in genres:
                genres.append(tag)
    for tag in genres[:tags]:
        t.genre_tracks += tag_top_tracks(tag, transport=transport)
    return t
