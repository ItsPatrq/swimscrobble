"""Scan a music folder into Track records."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile

AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav"}


@dataclass(frozen=True)
class Track:
    path: Path
    artist: str
    title: str
    album: str
    duration: float  # seconds
    bpm: float | None = None


def _first(tags, key: str, default: str = "") -> str:
    value = tags.get(key) if tags else None
    if not value:
        return default
    return str(value[0]).strip() or default


def _bpm(tags) -> float | None:
    try:
        return float(_first(tags, "bpm")) or None
    except ValueError:
        return None


def read_track(path: Path) -> Track | None:
    audio = MutagenFile(path, easy=True)
    if audio is None or audio.info is None:
        return None
    return Track(
        path=path,
        artist=_first(audio.tags, "artist"),
        title=_first(audio.tags, "title", path.stem),
        album=_first(audio.tags, "album"),
        duration=float(audio.info.length),
        bpm=_bpm(audio.tags),
    )


def scan(folder: Path) -> list[Track]:
    tracks = []
    for p in sorted(folder.iterdir()):
        # skip macOS AppleDouble sidecars (._foo.mp3)
        if p.name.startswith(".") or p.suffix.lower() not in AUDIO_EXTS:
            continue
        track = read_track(p)
        if track:
            tracks.append(track)
    return tracks
