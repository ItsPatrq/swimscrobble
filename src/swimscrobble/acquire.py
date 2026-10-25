"""Fetch files for playlist entries missing locally (explicit opt-in, via yt-dlp)."""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from .ytm import Entry


def download_command(entry: Entry, dest: Path) -> list[str]:
    return [
        "yt-dlp", "-x", "--audio-format", "mp3", "--embed-metadata", "--no-playlist",
        "-o", str(dest / "%(artist,channel)s - %(title)s.%(ext)s"),
        f"https://music.youtube.com/watch?v={entry.video_id}",
    ]


def acquire(
    entries: list[Entry],
    dest: Path,
    run: Callable = subprocess.run,
    ask: Callable[[str], str] | None = None,
) -> list[Entry]:
    """Download each entry; `ask` (if given) gates the batch. Returns entries that failed."""
    if ask and ask(f"Download {len(entries)} missing tracks into {dest}? [y/N] ").strip().lower() != "y":
        return list(entries)
    dest.mkdir(parents=True, exist_ok=True)
    failed = []
    for e in entries:
        if run(download_command(e, dest)).returncode != 0:
            failed.append(e)
    return failed


def resolve(artist: str, title: str, fetch: Callable = None) -> Entry | None:
    """Find the best YouTube Music match for a wanted track (first search hit)."""
    from . import ytm

    fetch = fetch or ytm.fetch
    hits = fetch(f"ytsearch1:{artist} {title}")
    return hits[0] if hits else None


def playlist_download_command(cfg, url: str, items: list[int] | None = None) -> list[str]:
    """The user's `ymp3` command, optionally limited to playlist positions (--playlist-items)."""
    subs = {
        "cookies": str(Path(cfg.cookies_file).expanduser()),
        "download_dir": str(Path(cfg.download_dir).expanduser()),
    }
    cmd = [part.format(**subs) for part in cfg.download_command]
    if items:
        cmd += ["--playlist-items", ",".join(str(i) for i in sorted(items))]
    return [*cmd, url]


def download_missing(
    cfg, url: str, missing: list[Entry], run: Callable = subprocess.run, ask: Callable[[str], str] | None = None
) -> bool:
    """Download only the missing playlist positions with the configured command. True if it ran OK."""
    if not missing:
        return True
    if ask and ask(f"Download {len(missing)} missing tracks into {cfg.download_dir}? [y/N] ").strip().lower() != "y":
        return False
    return run(playlist_download_command(cfg, url, [e.index for e in missing if e.index])).returncode == 0
