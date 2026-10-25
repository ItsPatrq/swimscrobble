"""Sync the swim pool with the local library: link wanted tracks to files, find what is missing."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import history, lfhistory, poolsync
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
    history_sync: Callable[[], object] | None = None,
    taste_fetch: Callable[[str, list, list], Taste] | None = None,
    download: Callable[[list[Entry]], None] | None = None,
    fetch_wishes: Callable[[list], int] | None = None,
    library_loader: Callable[[], list[Track]] | None = None,
    now: float | None = None,
) -> LibSyncReport:
    """Pool first (playlist tracks, downloaded if missing), then judge each against Last.fm history.

    `download` receives the playlist entries missing locally; the library is rescanned afterwards.
    Pool keys are always the *local* files' artist/title, so later lookups are exact.
    """
    load = library_loader or (
        lambda: scan(cfg.music_path, exclude=tuple(cfg.exclude_dirs), min_seconds=cfg.min_track_seconds)
    )
    library = load()
    entries = [e for url in cfg.playlists for e in fetch_playlist(url)]
    idx = LocalIndex(library)
    missing = [e for e in entries if not idx.find(e.artist, e.title, e.duration)]
    downloaded = False
    if missing and download:
        download(missing)
        library = load()
        idx = LocalIndex(library)
        missing = [e for e in entries if not idx.find(e.artist, e.title, e.duration)]
        downloaded = True

    key = lambda t: (t.artist.casefold(), t.title.casefold())  # noqa: E731
    when = now if now is not None else time.time()

    def judge():
        """Pool first (playlist + auto-fetched files), then check each track against the history."""
        idx = LocalIndex(library)
        pool = None
        if cfg.pool == "playlist" and entries:
            pool = {key(t) for e in entries if (t := idx.find(e.artist, e.title, e.duration))}
            pool |= {key(t) for t in library if t.path.is_relative_to(cfg.auto_path)}
        taste = None
        if cfg.lastfm_user:
            if history_sync and not synced:
                history_sync()
                synced.append(1)
            st = lfhistory.stats(db, now)
            recent = sorted((kv for kv in st.items() if kv[1].p30), key=lambda kv: -kv[1].p30)
            artists: dict[str, int] = {}
            for (a, _), s in recent:
                artists[a] = artists.get(a, 0) + s.p30
            if taste_fetch:
                taste = taste_fetch(cfg.lastfm_user, [k for k, _ in recent[:10]],
                                    sorted(artists, key=lambda a: -artists[a]))
            return assign(taste, st, library, pool, idx, when)
        return assign(None, {}, library, pool, idx, when)  # no history: everything is "discovery"

    synced: list[int] = []
    owned, wanted = judge()
    if wanted and fetch_wishes and fetch_wishes(wanted):
        library = load()  # new files arrived: rescan and judge again
        owned, wanted = judge()
    rep = poolsync.sync_pool(db, owned, {key(t) for t in library}, history.play_counts(db), size)
    rep.missing.extend(wanted)
    return LibSyncReport(rep, len(entries), missing, downloaded)
