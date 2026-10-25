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
) -> dict:
    stage_dir = cfg.stage_path
    stage_dir.mkdir(parents=True, exist_ok=True)
    clear_staged(stage_dir, previous)
    plan = build_mix(
        library, pool_candidates(db), feature_of or (lambda t: cached_features(db, t)),
        minutes=cfg.minutes * cfg.buffer_factor, seed=seed, rec_ratio=cfg.rec_ratio, min_energy=cfg.min_energy,
        plays=history.play_counts(db), recent=history.recent_plays(db, cfg.no_repeat_days),
    )
    for p in plan:
        shutil.copy2(p.track.path, stage_dir / p.staged_name)
    manifest = to_manifest(plan, seed, cfg.minutes)
    manifest["id"] = history.add_session(db, manifest, cfg.minutes, seed, str(stage_dir))
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
    if settled == "awaiting confirmation" or settled.startswith("failed"):
        return log  # do not plan over an unsettled session

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
    manifest = plan_and_stage(db, cfg, library_loader(cfg.music_path), previous=previous, feature_of=feature_of)
    log.append(f"staged session {manifest['id']}: {len(manifest['tracks'])} tracks")
    notify(f"swimscrobble: staged {len(manifest['tracks'])} tracks for the next swim")
    return log
