"""Read-only Last.fm taste data (top/loved tracks, similar tracks)."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import secret

API_URL = "https://ws.audioscrobbler.com/2.0/"
Transport = Callable[[dict], dict]


def http_get(params: dict) -> dict:
    with urllib.request.urlopen(f"{API_URL}?{urllib.parse.urlencode(params)}", timeout=30) as r:
        return json.load(r)


def _get(method: str, transport: Transport, **params) -> dict:
    key = secret("LASTFM_API_KEY")
    if not key:
        raise SystemExit("set LASTFM_API_KEY")
    resp = transport({"method": method, "api_key": key, "format": "json", **params})
    if "error" in resp:
        raise RuntimeError(f"Last.fm error {resp['error']}: {resp.get('message')}")
    return resp


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


def track_chart(user: str, start: int, end: int, transport: Transport = http_get) -> list[tuple[str, str, int]]:
    """Tracks played between two unix timestamps (any range, unlike the fixed top-track periods)."""
    d = _get("user.getWeeklyTrackChart", transport, user=user, **{"from": start, "to": end})
    return [
        (t["artist"]["#text"], t["name"], int(t.get("playcount", 0)))
        for t in d.get("weeklytrackchart", {}).get("track", [])
    ]


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
    chart30: list[tuple[str, str, int]] = field(default_factory=list)  # most recent obsession
    chart60: list[tuple[str, str, int]] = field(default_factory=list)  # 30-60 days ago
    chart90: list[tuple[str, str, int]] = field(default_factory=list)  # 60-90 days ago
    overall: list[tuple[str, str, int]] = field(default_factory=list)  # all-time top tracks
    loved: list[tuple[str, str]] = field(default_factory=list)
    similar: list[tuple[str, str, float]] = field(default_factory=list)  # similar to the recent obsession
    artist_tracks: dict[str, list[tuple[str, str]]] = field(default_factory=dict)  # known artist -> top tracks
    genre_tracks: list[tuple[str, str]] = field(default_factory=list)  # top tracks of your genres


def fetch_taste(
    user: str,
    now: float | None = None,
    transport: Transport = http_get,
    *,
    seeds: int = 10,
    artists: int = 8,
    tags: int = 3,
) -> Taste:
    now = int(now if now is not None else time.time())
    day = 86400
    t = Taste(
        chart30=track_chart(user, now - 30 * day, now, transport),
        chart60=track_chart(user, now - 60 * day, now - 30 * day, transport),
        chart90=track_chart(user, now - 90 * day, now - 60 * day, transport),
        overall=top_tracks(user, period="overall", limit=1000, transport=transport),
        loved=loved_tracks(user, transport=transport),
    )
    recent = sorted(t.chart30, key=lambda x: -x[2])
    for artist, title, _ in recent[:seeds]:
        t.similar += similar(artist, title, transport=transport)
    top_artists: list[str] = []
    for artist, _, _ in recent:
        if artist not in top_artists:
            top_artists.append(artist)
    for artist in top_artists[:artists]:
        t.artist_tracks[artist] = artist_top_tracks(artist, transport=transport)
    genres: list[str] = []
    for artist in top_artists[:5]:
        for tag in artist_top_tags(artist, transport=transport):
            if tag not in GENERIC_TAGS and tag not in genres:
                genres.append(tag)
    for tag in genres[:tags]:
        t.genre_tracks += tag_top_tracks(tag, transport=transport)
    return t
