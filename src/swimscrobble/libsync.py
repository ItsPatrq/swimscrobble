"""Sync the swim pool with the local library: link wanted tracks to files, find what is missing."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import history, poolsync
from .buckets import assign
from .config import Config
from .library import Track, scan
from .link import LocalIndex
from .taste import Taste
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
    size: int = 1000,
    fetch_playlist: Callable[[str], list[Entry]],
    taste_fetch: Callable[[str], Taste] | None = None,
    download: Callable[[list[Entry]], None] | None = None,
    library_loader: Callable[[], list[Track]] | None = None,
) -> LibSyncReport:
    """Scan ~/Music, bucket local tracks by your listening history, optionally download the rest.

    `download` receives the playlist entries missing locally; the library is rescanned afterwards.
    Pool keys are always the *local* files' artist/title, so later lookups are exact.
    """
    load = library_loader or (
        lambda: scan(cfg.music_path, exclude=tuple(cfg.exclude_dirs), min_seconds=cfg.min_track_seconds)
    )
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

    taste = taste_fetch(cfg.lastfm_user) if (taste_fetch and cfg.lastfm_user) else None
    owned, wanted = assign(taste, entries, library, idx)
    rep = poolsync.sync_pool(
        db, owned, {(t.artist.casefold(), t.title.casefold()) for t in library}, history.play_counts(db), size
    )
    rep.missing.extend(wanted)
    return LibSyncReport(rep, len(entries), missing, downloaded)
