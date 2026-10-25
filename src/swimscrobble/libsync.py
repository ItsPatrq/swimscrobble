"""Sync the swim pool with the local library: link wanted tracks to files, find what is missing."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import history, poolsync
from .config import Config
from .generator import Candidate, key, score_candidates
from .library import Track, scan
from .link import LocalIndex
from .ytm import Entry


@dataclass
class LibSyncReport:
    pool: poolsync.SyncReport
    playlist_total: int = 0
    playlist_missing: list[Entry] = field(default_factory=list)
    downloaded: bool = False


def sync_library(
    cfg: Config,
    db,
    *,
    size: int = 150,
    fetch_playlist: Callable[[str], list[Entry]],
    taste_fetch: Callable[[str, str], tuple[list, list, list]] | None = None,
    download: Callable[[list[Entry]], None] | None = None,
    library_loader: Callable[[], list[Track]] | None = None,
) -> LibSyncReport:
    """Scan ~/Music, link playlist + Last.fm candidates to local files, optionally download the rest.

    `download` receives the playlist entries missing locally; the library is rescanned afterwards.
    Pool keys are always the *local* files' artist/title, so later lookups are exact.
    """
    load = library_loader or (lambda: scan(cfg.music_path, exclude=tuple(cfg.exclude_dirs), min_seconds=cfg.min_track_seconds))
    library = load()
    entries = fetch_playlist(cfg.ytm_playlist) if cfg.ytm_playlist else []
    idx = LocalIndex(library)
    missing = [e for e in entries if not idx.find(e.artist, e.title, e.duration)]
    downloaded = False
    if missing and download:
        download(missing)
        library = load()
        idx = LocalIndex(library)
        missing = [e for e in entries if not idx.find(e.artist, e.title, e.duration)]
        downloaded = True

    def local_key(artist: str, title: str, duration: float | None = None):
        t = idx.find(artist, title, duration)
        return (t.artist.casefold(), t.title.casefold()) if t else None

    playlist = [k for e in entries if (k := local_key(e.artist, e.title, e.duration))]
    loved, top, sim = taste_fetch(cfg.lastfm_user, cfg.taste_period) if (taste_fetch and cfg.lastfm_user) else ([], [], [])

    # re-key Last.fm tracks onto local files; unmatched ones stay as "wanted" (original names)
    loved_k = [k for a, t in loved if (k := local_key(a, t))]
    top_k = [(*k, n) for a, t, n in top if (k := local_key(a, t))]
    sim_k = [(*k, m) for a, t, m in sim if (k := local_key(a, t))]
    wanted_extra = [
        Candidate(a, t, 0.0, {"similar"}) for a, t, _ in sim if not local_key(a, t)
    ]
    display = {(t.artist.casefold(), t.title.casefold()): (t.artist, t.title) for t in library}
    cands = score_candidates(playlist, [(a, t) for a, t in loved_k], top_k, sim_k, display)
    rep = poolsync.sync_pool(
        db, cands, {key(*display[k]) for k in display}, history.play_counts(db), size
    )
    rep.missing.extend(wanted_extra)
    return LibSyncReport(rep, len(entries), missing, downloaded)


def music_dir_hint(cfg: Config) -> Path:
    return cfg.music_path
