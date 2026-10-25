"""Read-only Last.fm taste data (top/loved tracks, similar tracks)."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable

API_URL = "https://ws.audioscrobbler.com/2.0/"
Transport = Callable[[dict], dict]


def http_get(params: dict) -> dict:
    with urllib.request.urlopen(f"{API_URL}?{urllib.parse.urlencode(params)}", timeout=30) as r:
        return json.load(r)


def _get(method: str, transport: Transport, **params) -> dict:
    key = os.environ.get("LASTFM_API_KEY")
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
