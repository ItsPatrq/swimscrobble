"""Mirror the Pocket Casts Up Next queue to a folder (unofficial, reverse-engineered API).

Pocket Casts does not support third-party clients; use your own account at your own risk.
"""
from __future__ import annotations

import json
import re
import shutil
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from .config import config_dir, secret

API = "https://api.pocketcasts.com"
TOKEN_FILE = "pocketcasts.json"

Transport = Callable[[str, dict, str | None], dict]  # (path, body, token) -> json
Downloader = Callable[[str, Path], None]


def http_post(path: str, body: dict, token: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def http_download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.rename(dest)  # never leave half files that look complete


def login(email: str | None = None, password: str | None = None, transport: Transport = http_post) -> str:
    email = email or secret("POCKETCASTS_EMAIL")
    password = password or secret("POCKETCASTS_PASSWORD")
    if not email or not password:
        raise SystemExit("set POCKETCASTS_EMAIL and POCKETCASTS_PASSWORD")
    token = transport("/user/login", {"email": email, "password": password}, None)["token"]
    f = config_dir() / TOKEN_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"token": token}))
    f.chmod(0o600)
    return token


def saved_token() -> str | None:
    try:
        return json.loads((config_dir() / TOKEN_FILE).read_text())["token"]
    except FileNotFoundError:
        return None


def up_next(token: str, transport: Transport = http_post) -> list[dict]:
    data = transport("/up_next/list", {"version": 2, "model": "webplayer", "serverModified": "0"}, token)
    eps = data.get("episodes", [])
    if isinstance(eps, dict):  # uuid-keyed map in some responses
        eps = [{"uuid": u, **e} for u, e in eps.items()]
    return [e for e in eps if e.get("url") and e.get("uuid")]


def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "", s).strip()[:80]


def filename_for(position: int, episode: dict) -> str:
    ext = Path(urlparse(episode["url"]).path).suffix.lower()
    ext = ext if ext in {".mp3", ".m4a", ".aac", ".ogg", ".opus"} else ".mp3"
    return f"{position:02d} - {_safe(episode.get('title', 'episode'))}{ext}"


def sync_queue(
    episodes: list[dict],
    dest: Path,
    db,
    max_episodes: int = 10,
    download: Downloader = http_download,
) -> dict[str, list[str]]:
    """Make `dest` hold the first N queue episodes, queue-ordered by filename prefix."""
    dest.mkdir(parents=True, exist_ok=True)
    want = {e["uuid"]: (filename_for(i + 1, e), e) for i, e in enumerate(episodes[:max_episodes])}
    have = {r["uuid"]: r["filename"] for r in db.execute("SELECT uuid, filename FROM podcasts")}
    report: dict[str, list[str]] = {"downloaded": [], "renamed": [], "removed": []}

    for uuid, name in have.items():
        if uuid not in want:
            (dest / name).unlink(missing_ok=True)
            db.execute("DELETE FROM podcasts WHERE uuid=?", (uuid,))
            report["removed"].append(name)
    for uuid, (name, ep) in want.items():
        old = have.get(uuid)
        if old and old != name and (dest / old).exists():
            (dest / old).rename(dest / name)
            report["renamed"].append(name)
        if not (dest / name).exists():
            download(ep["url"], dest / name)
            report["downloaded"].append(name)
        db.execute(
            "INSERT INTO podcasts(uuid,title,filename,downloaded_at) VALUES(?,?,?,strftime('%s','now')) "
            "ON CONFLICT(uuid) DO UPDATE SET filename=excluded.filename",
            (uuid, ep.get("title", ""), name),
        )
    db.commit()
    return report
