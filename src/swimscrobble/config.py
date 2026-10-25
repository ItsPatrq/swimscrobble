"""Config from ~/.config/swimscrobble/config.toml; env SWIMSCROBBLE_CONFIG overrides the path."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("SWIMSCROBBLE_HOME", Path.home() / ".config" / "swimscrobble"))


@dataclass
class Config:
    music_dir: str = "~/Music"  # whole tree is scanned
    exclude_dirs: list[str] = field(default_factory=lambda: ["Logic", "Audio Music Apps"])
    min_track_seconds: float = 60
    cookies_file: str = "~/music.youtube.com_cookies.txt"
    auto_download: bool = True  # on-connect: fetch playlist tracks missing locally
    download_dir: str = "~/Music/YouTubeMusic"
    # {url} and {items} are filled in; this is the `ymp3` alias spelled out
    download_command: list[str] = field(default_factory=lambda: [
        "yt-dlp", "--cookies", "{cookies}",
        "--extractor-args", "youtube:player_client=web_music+BGUTIL__1,web_creator+BGUTIL__1",
        "--format", "bestaudio", "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
        "--embed-metadata", "--embed-thumbnail", "--parse-metadata", "playlist_index:%(track_number)s",
        "--output", "{download_dir}/%(album|Unknown Album)s/%(track_number)02d - %(title)s.%(ext)s",
    ])
    device_name: str = "SWIM PRO"
    device_music_subdir: str = "Music"
    device_podcast_subdir: str = "PocketCast"
    minutes: float = 60  # how long you swim
    buffer_factor: float = 2.0  # staged playlist is minutes * this, so it never runs out mid-swim
    auto_submit: bool = False  # on-connect: submit scrobbles without asking
    session_offset_minutes: float = 0  # gap between swim ending and plugging in
    ytm_playlist: str = ""
    listenbrainz: bool = False
    podcast_dir: str = "~/Music/swim-podcasts"
    max_podcasts: int = 10
    arc: bool = False
    taste_period: str = "1month"  # Last.fm window: 7day | 1month | 3month | ...
    rec_ratio: float = 0.2  # share of the session given to recommendations
    min_energy: float = 0.5  # drop slow/ballad tracks below this energy (0..1)
    volumes_dir: str = "/Volumes"
    stage_subdir: str = ""  # under the device music folder; "" = its root
    lastfm_user: str = ""
    no_repeat_days: int = 7

    @property
    def cookies_path(self) -> str | None:
        p = Path(self.cookies_file).expanduser()
        return str(p) if p.exists() else None

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


def secret(name: str) -> str | None:
    """Env var first, then ~/.config/swimscrobble/secrets.toml (launchd does not inherit shell env)."""
    if os.environ.get(name):
        return os.environ[name]
    f = config_dir() / "secrets.toml"
    if f.exists():
        return tomllib.loads(f.read_text()).get(name)
    return None
