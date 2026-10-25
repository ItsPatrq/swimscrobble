"""Minimal Last.fm client: web auth flow + batch track.scrobble (stdlib only)."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from .config import secret
from .scrobble import Scrobble

API_URL = "https://ws.audioscrobbler.com/2.0/"
AUTH_URL = "https://www.last.fm/api/auth/"
MAX_AGE_SECONDS = 14 * 24 * 3600
BATCH = 50
SESSION_FILE = Path.home() / ".config" / "swimscrobble" / "lastfm.json"

Transport = Callable[[dict], dict]


def sign(params: dict, secret: str) -> str:
    """api_sig: md5 of sorted name+value pairs plus secret; `format` is not signed."""
    raw = "".join(f"{k}{params[k]}" for k in sorted(params) if k != "format")
    return hashlib.md5((raw + secret).encode("utf-8")).hexdigest()


def http_post(params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(API_URL, data=data), timeout=30) as r:
        return json.load(r)


def _call(method: str, params: dict, key: str, secret: str, transport: Transport) -> dict:
    body = {**params, "method": method, "api_key": key}
    body["api_sig"] = sign(body, secret)
    body["format"] = "json"
    resp = transport(body)
    if "error" in resp:
        raise RuntimeError(f"Last.fm error {resp['error']}: {resp.get('message')}")
    return resp


def credentials() -> tuple[str, str]:
    key, shared = secret("LASTFM_API_KEY"), secret("LASTFM_API_SECRET")
    if not key or not shared:
        raise SystemExit("set LASTFM_API_KEY and LASTFM_API_SECRET (https://www.last.fm/api/account/create)")
    return key, shared


def authenticate(transport: Transport = http_post, wait: Callable[[str], None] = input) -> str:
    """Web auth: get token, user approves in browser, exchange for a session key."""
    key, secret = credentials()
    token = _call("auth.getToken", {}, key, secret, transport)["token"]
    wait(f"Open {AUTH_URL}?api_key={key}&token={token} , approve, then press Enter: ")
    sk = _call("auth.getSession", {"token": token}, key, secret, transport)["session"]["key"]
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({"session_key": sk}))
    SESSION_FILE.chmod(0o600)
    return sk


def load_session_key() -> str:
    try:
        return json.loads(SESSION_FILE.read_text())["session_key"]
    except FileNotFoundError:
        raise SystemExit("not authenticated; run: swimscrobble lastfm-auth") from None


def check_window(scrobbles: list[Scrobble], now: float | None = None) -> list[Scrobble]:
    """Return scrobbles Last.fm would reject (older than 14 days or in the future)."""
    now = now if now is not None else time.time()
    return [s for s in scrobbles if not (now - MAX_AGE_SECONDS <= s.timestamp.timestamp() <= now)]


def submit(
    scrobbles: list[Scrobble],
    session_key: str,
    transport: Transport = http_post,
) -> int:
    """Send in batches of 50. Returns number accepted."""
    key, secret = credentials()
    accepted = 0
    for i in range(0, len(scrobbles), BATCH):
        params: dict = {"sk": session_key}
        for j, s in enumerate(scrobbles[i : i + BATCH]):
            params[f"artist[{j}]"] = s.artist
            params[f"track[{j}]"] = s.title
            params[f"timestamp[{j}]"] = int(s.timestamp.timestamp())
            if s.album:
                params[f"album[{j}]"] = s.album
            params[f"duration[{j}]"] = s.duration
        resp = _call("track.scrobble", params, key, secret, transport)
        accepted += int(resp["scrobbles"]["@attr"]["accepted"])
    return accepted
