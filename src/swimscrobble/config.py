"""Config from ~/.config/swimscrobble/config.toml; env SWIMSCROBBLE_CONFIG overrides the path."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("SWIMSCROBBLE_HOME", Path.home() / ".config" / "swimscrobble"))


@dataclass
class Config:
    music_dir: str = "~/Music/swim"
    device_name: str = "SWIM PRO"
    device_music_subdir: str = "Music"
    device_podcast_subdir: str = "PocketCast"
    minutes: float = 60
    auto_submit: bool = False  # on-connect: submit scrobbles without asking
    session_offset_minutes: float = 0  # gap between swim ending and plugging in
    ytm_playlist: str = ""
    listenbrainz: bool = False
    podcast_dir: str = "~/Music/swim-podcasts"
    max_podcasts: int = 10
    arc: bool = False
    volumes_dir: str = "/Volumes"
    stage_subdir: str = ""  # under the device music folder; "" = its root
    lastfm_user: str = ""
    no_repeat_days: int = 7

    @property
    def music_path(self) -> Path:
        return Path(self.music_dir).expanduser()

    @property
    def podcast_path(self) -> Path:
        return Path(self.podcast_dir).expanduser()

    @property
    def device_path(self) -> Path:
        return Path(self.volumes_dir) / self.device_name

    @property
    def stage_path(self) -> Path:
        return self.device_path / self.device_music_subdir / self.stage_subdir


def load(path: Path | None = None) -> Config:
    path = path or Path(os.environ.get("SWIMSCROBBLE_CONFIG", config_dir() / "config.toml"))
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise SystemExit(f"unknown config keys in {path}: {sorted(unknown)}")
    return Config(**data)
