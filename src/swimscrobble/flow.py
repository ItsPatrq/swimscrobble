"""The automatic flow that runs when the headphones are connected.

1. settle the previous session (scrobble it, or park it for confirmation)
2. refresh podcasts from the Pocket Casts queue
3. plan + stage the next session
"""
from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from . import history, lastfm, listenbrainz
from .config import Config, config_dir
from .energy import Features, cached_features
from .generator import Candidate
from .library import Track, scan
from .mix import build_mix
from .scrobble import build_scrobbles, to_csv
from .session import played_part, to_manifest

STAGED_RE = re.compile(r"^\d{3} - ")


def state_file() -> Path:
    return config_dir() / "device_state.json"


def _handled() -> bool:
    try:
        return json.loads(state_file().read_text()).get("handled", False)
    except FileNotFoundError:
        return False


def _set_handled(v: bool) -> None:
    state_file().parent.mkdir(parents=True, exist_ok=True)
    state_file().write_text(json.dumps({"handled": v}))


def submit_session(db, session_row, scrobbles, cfg: Config, *, lastfm_submit=None, lb_submit=None) -> str:
    """Send to configured services, then mark the session scrobbled exactly once."""
    lastfm_submit = lastfm_submit or (lambda s: lastfm.submit(s, lastfm.load_session_key()))
    lb_submit = lb_submit or listenbrainz.submit
    stale = lastfm.check_window(scrobbles)
    if stale:
        raise ValueError(f"{len(stale)} scrobbles outside the 14-day window")
    n = lastfm_submit(scrobbles)
    if cfg.listenbrainz:
        lb_submit(scrobbles)
    history.mark_scrobbled(
        db, session_row["id"], [(s.artist, s.title, s.timestamp.timestamp()) for s in scrobbles]
    )
    return f"scrobbled {n} tracks"


def settle_previous(db, cfg: Config, now: datetime, notify, **deps) -> str:
    row = history.pending_session(db)
    if row is None:
        return "no previous session"
    if now.timestamp() - row["planned_at"] < cfg.min_swim_gap_minutes * 60:
        return "too soon"  # staged moments ago (plugged in twice?): nothing can have been swum yet
    # the staged list is longer than the swim: only the first `minutes` of it was heard
    plan, listened = played_part(json.loads(row["manifest"]))
    end = now - timedelta(minutes=cfg.session_offset_minutes)
    scrobbles = build_scrobbles(plan, end - timedelta(seconds=listened))
    if cfg.auto_submit:
        try:
            msg = submit_session(db, row, scrobbles, cfg, **deps)
        except Exception as e:  # keep the session pending so nothing is lost
            notify(f"swimscrobble: scrobbling failed: {e}")
            return f"failed: {e}"
        notify(f"swimscrobble: {msg}")
        return msg
    out = config_dir() / "pending" / f"session-{row['id']}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_csv(scrobbles))
    notify(f"swimscrobble: session {row['id']} awaiting confirmation (swimscrobble scrobble-pending)")
    return "awaiting confirmation"


def clear_staged(stage_dir: Path, previous_manifest: dict | None) -> int:
    """Remove only files this tool staged earlier (named in the manifest, prefix-checked)."""
    if not previous_manifest:
        return 0
    n = 0
    for t in previous_manifest["tracks"]:
        f = stage_dir / t["staged"]
        if STAGED_RE.match(f.name) and f.exists():
            f.unlink()
            n += 1
    return n


def pool_candidates(db) -> list[Candidate]:
    return [
        Candidate(r["artist"], r["title"], float(r["score"]), set(filter(None, (r["source"] or "").split(","))))
        for r in db.execute("SELECT artist, title, score, source FROM pool WHERE retired=0")
    ]


def plan_and_stage(
    db,
    cfg: Config,
    library: list[Track],
    seed: int | None = None,
    previous: dict | None = None,
    feature_of: Callable[[Track], Features] | None = None,
    now: datetime | None = None,
) -> dict:
    stage_dir = cfg.stage_path
    stage_dir.mkdir(parents=True, exist_ok=True)
    clear_staged(stage_dir, previous)
    plan = build_mix(
        library, pool_candidates(db), feature_of or (lambda t: cached_features(db, t)),
        minutes=cfg.playlist_minutes, swim_minutes=cfg.minutes, seed=seed, quotas=cfg.mix_weights,
        min_energy=cfg.min_energy, max_per_artist=cfg.max_per_artist, variety=cfg.variety,
        plays=history.play_counts(db), recent=history.recent_plays(db, cfg.no_repeat_days),
    )
    for p in plan:
        shutil.copy2(p.track.path, stage_dir / p.staged_name)
    manifest = to_manifest(plan, seed, cfg.minutes)
    manifest["id"] = history.add_session(
        db, manifest, cfg.minutes, seed, str(stage_dir), at=now.timestamp() if now else None)
    return manifest


def on_connect(
    cfg: Config,
    db,
    now: datetime | None = None,
    notify: Callable[[str], None] = print,
    library_loader: Callable[[Path], list[Track]] | None = None,
    feature_of: Callable[[Track], Features] | None = None,
    podcast_sync: Callable[[], dict] | None = None,
    pool_sync: Callable[[], str] | None = None,
    **deps,
) -> list[str]:
    now = now or datetime.now()
    log: list[str] = []
    library_loader = library_loader or (
        lambda p: scan(p, exclude=tuple(cfg.exclude_dirs), min_seconds=cfg.min_track_seconds)
    )
    if not cfg.device_path.is_dir():
        _set_handled(False)  # unplugged: the next connection is a new one
        return ["device not connected"]
    if _handled():
        return ["already handled this connection"]
    _set_handled(True)

    settled = settle_previous(db, cfg, now, notify, **deps)
    log.append(settled)
    if settled in ("awaiting confirmation", "too soon") or settled.startswith("failed"):
        return log  # do not plan over an unsettled (or still current) session

    if podcast_sync:
        try:
            log.append(f"podcasts: {podcast_sync()}")
        except Exception as e:
            log.append(f"podcasts failed: {e}")

    if pool_sync:  # a failed refresh must never block staging; the old pool still works
        try:
            log.append(f"pool: {pool_sync()}")
        except Exception as e:
            log.append(f"pool sync failed: {e}")

    prev_row = db.execute("SELECT manifest FROM sessions WHERE staged_dir IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    previous = json.loads(prev_row["manifest"]) if prev_row else None
    manifest = plan_and_stage(db, cfg, library_loader(cfg.music_path), previous=previous, feature_of=feature_of,
                              now=now)
    log.append(f"staged session {manifest['id']}: {len(manifest['tracks'])} tracks")
    notify(f"swimscrobble: staged {len(manifest['tracks'])} tracks for the next swim")
    return log


def reroll(
    cfg: Config,
    db,
    seed: int | None = None,
    library_loader: Callable[[Path], list[Track]] | None = None,
    feature_of: Callable[[Track], Features] | None = None,
    now: datetime | None = None,
) -> dict:
    """Replace the staged playlist with a fresh one. The old session is discarded, not scrobbled."""
    if not cfg.device_path.is_dir():
        raise RuntimeError(f"{cfg.device_name} is not connected")
    library_loader = library_loader or (
        lambda p: scan(p, exclude=tuple(cfg.exclude_dirs), min_seconds=cfg.min_track_seconds)
    )
    prev_row = db.execute("SELECT manifest FROM sessions WHERE staged_dir IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    previous = json.loads(prev_row["manifest"]) if prev_row else None
    history.discard_pending(db)
    return plan_and_stage(db, cfg, library_loader(cfg.music_path), seed=seed, previous=previous,
                          feature_of=feature_of, now=now)


def last_staged(db):
    return db.execute("SELECT * FROM sessions WHERE staged_dir IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()


def orphans(cfg: Config, db) -> tuple[int, int | None]:
    """(prefixed files from the latest session still on the device, that session's id) when nothing is pending."""
    if history.pending_session(db) is not None or not cfg.device_path.is_dir():
        return 0, None
    row = last_staged(db)
    if row is None:
        return 0, None
    manifest = json.loads(row["manifest"])
    n = sum(1 for t in manifest["tracks"] if (cfg.stage_path / t["staged"]).exists())
    return n, row["id"]


def adopt_orphans(cfg: Config, db) -> int | None:
    """Make the playlist still on the headphones the current session again (so it can be scrobbled)."""
    n, sid = orphans(cfg, db)
    if not n:
        return None
    db.execute("UPDATE sessions SET status='planned' WHERE id=?", (sid,))
    db.commit()
    return sid


def clear_orphans(cfg: Config, db) -> int:
    n, sid = orphans(cfg, db)
    if not n:
        return 0
    return clear_staged(cfg.stage_path, json.loads(last_staged(db)["manifest"]))
