"""Numbers for the dashboard and the stats screen (pure queries, no printing)."""
from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field

from . import history
from .config import Config, config_dir

DAY = 86400


@dataclass
class Dashboard:
    device_connected: bool
    device_free_gb: float | None
    staged: dict | None  # id, tracks, minutes, age_hours, swim_tracks, buckets
    scrobbles: int
    scrobbles_30d: int
    pool: dict[str, int] = field(default_factory=dict)
    swum_recently: int = 0
    wishlist: int = 0
    last_sync_hours: float | None = None
    agent_installed: bool = False
    orphans: int = 0  # staged files still on the headphones that belong to no pending session
    orphan_session: int | None = None


def swim_split(manifest: dict, swim_minutes: float) -> tuple[int, float]:
    """(number of tracks inside the swim window, seconds they cover)."""
    t, n = 0.0, 0
    for tr in sorted(manifest["tracks"], key=lambda x: x["position"]):
        if t >= swim_minutes * 60:
            break
        t += tr["duration"]
        n += 1
    return n, t


def wishlist_lines(weights: dict[str, float] | None = None) -> list[tuple[str, str, str]]:
    """(label, bucket, url) from the file `sync` writes. With `weights`, interleaved by bucket share
    (the file is sorted by score, which would put one bucket first)."""
    f = config_dir() / "wishlist.txt"
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            out.append((parts[0], parts[1].strip("[]"), parts[2]))
    if weights:
        from types import SimpleNamespace

        from .acquire import balanced

        items = [SimpleNamespace(sources={b}, row=(label, b, url)) for label, b, url in out]
        out = [i.row for i in balanced(items, weights)]
    return out


def dashboard(cfg: Config, db, now: float | None = None, agent_installed: bool = False) -> Dashboard:
    now = now if now is not None else time.time()
    row = history.pending_session(db)
    staged = None
    if row:
        m = json.loads(row["manifest"])
        n_swim, _ = swim_split(m, m.get("minutes", cfg.minutes))
        staged = {
            "id": row["id"], "tracks": len(m["tracks"]), "minutes": sum(t["duration"] for t in m["tracks"]) / 60,
            "age_hours": (now - row["planned_at"]) / 3600, "swim_tracks": n_swim,
            "swim_minutes": m.get("minutes", cfg.minutes),
            "buckets": dict(Counter(t.get("source", "") for t in m["tracks"])),
        }
    free = None
    if cfg.device_path.is_dir():
        free = shutil.disk_usage(cfg.device_path).free / 1e9
    meta = db.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
    from .flow import orphans

    n_orphans, orphan_id = orphans(cfg, db)
    return Dashboard(
        device_connected=cfg.device_path.is_dir(), device_free_gb=free, staged=staged,
        scrobbles=db.execute("SELECT COUNT(*) c FROM scrobbles").fetchone()["c"],
        scrobbles_30d=db.execute("SELECT COUNT(*) c FROM scrobbles WHERE ts >= ?", (now - 30 * DAY,)).fetchone()["c"],
        pool={r["source"]: r["c"] for r in db.execute(
            "SELECT source, COUNT(*) c FROM pool WHERE retired=0 GROUP BY source ORDER BY c DESC")},
        swum_recently=len(history.recent_plays(db, cfg.no_repeat_days)),
        wishlist=len(wishlist_lines()),
        last_sync_hours=(now - float(meta["value"])) / 3600 if meta else None,
        agent_installed=agent_installed, orphans=n_orphans, orphan_session=orphan_id,
    )


def top_artists(db, days: int = 30, n: int = 8, now: float | None = None) -> list[tuple[str, int]]:
    now = now if now is not None else time.time()
    return [(r["artist"], r["c"]) for r in db.execute(
        "SELECT artist, COUNT(*) c FROM scrobbles WHERE ts >= ? GROUP BY artist ORDER BY c DESC LIMIT ?",
        (now - days * DAY, n))]


def top_tracks(db, days: int = 30, n: int = 8, now: float | None = None) -> list[tuple[str, str, int]]:
    now = now if now is not None else time.time()
    from .buckets import looks_like_song

    rows = db.execute(
        "SELECT artist, title, COUNT(*) c FROM scrobbles WHERE ts >= ? GROUP BY artist, title ORDER BY c DESC LIMIT ?",
        (now - days * DAY, n * 4))
    return [(r["artist"], r["title"], r["c"]) for r in rows if looks_like_song(r["title"])][:n]  # no podcast episodes


def swim_stats(db) -> dict:
    sessions = {r["status"]: r["c"] for r in db.execute("SELECT status, COUNT(*) c FROM sessions GROUP BY status")}
    scrobbled = db.execute("SELECT SUM(minutes) m, MAX(scrobbled_at) last FROM sessions WHERE status='scrobbled'").fetchone()
    most = [(r["artist"], r["title"], r["c"]) for r in db.execute(
        "SELECT artist, title, COUNT(*) c FROM plays GROUP BY artist, title ORDER BY c DESC, MAX(ts) DESC LIMIT 5")]
    return {"sessions": sessions, "swim_minutes": scrobbled["m"] or 0.0, "last_swim": scrobbled["last"], "most_swum": most}


def listening_mix(db, days: int = 30, now: float | None = None) -> dict[str, float]:
    """Of the tracks you played in the window: how many were new to you, how many returned after a year?"""
    now = now if now is not None else time.time()
    start = now - days * DAY
    rows = db.execute(
        """SELECT w.artist, w.title, MIN(a.ts) first_ever, MAX(CASE WHEN a.ts < ? THEN a.ts END) last_before
           FROM (SELECT DISTINCT artist, title FROM scrobbles WHERE ts >= ?) w
           JOIN scrobbles a ON a.artist = w.artist AND a.title = w.title
           GROUP BY w.artist, w.title""", (start, start)).fetchall()
    if not rows:
        return {}
    n = len(rows)
    new = sum(1 for r in rows if r["first_ever"] >= start)
    back = sum(1 for r in rows if r["last_before"] and start - r["last_before"] > 365 * DAY)
    return {"tracks": float(n), "new to you": new / n, "back after 1y+": back / n, "repeat": 1 - (new + back) / n}
