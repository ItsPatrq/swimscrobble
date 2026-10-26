"""Swims recorded on a Garmin watch: when the swim started and how long it lasted (unofficial Garmin Connect API).

Authorise once with `swimscrobble garmin-auth` (asks for email, password and the MFA code if your account has one);
only the resulting tokens are stored, in ~/.config/swimscrobble/garmin/. Later runs, launchd included, reuse them.
"""
from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .config import config_dir, secret

TOKEN_DIR = "garmin"

Fetch = Callable[[str, str], list[dict]]  # (start YYYY-MM-DD, end YYYY-MM-DD) -> activities


@dataclass(frozen=True)
class Swim:
    start: datetime  # local wall-clock time, like the rest of the app
    minutes: float  # elapsed: rests at the wall count, the music keeps playing through them
    kind: str
    distance_m: float | None = None

    def describe(self) -> str:
        dist = f", {self.distance_m:.0f} m" if self.distance_m else ""
        return f"{self.kind.replace('_', ' ')} {self.start:%a %H:%M}, {self.minutes:.0f} min{dist}"


def _store():
    d = config_dir() / TOKEN_DIR
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def authorised() -> bool:
    d = config_dir() / TOKEN_DIR
    return d.is_dir() and any(d.iterdir())


def client(ask: Callable[[str], str] | None = None, ask_secret: Callable[[str], str] | None = None):
    """Logged-in client. With `ask`, missing credentials and the MFA code are prompted for (garmin-auth);
    without it, saved tokens are used (falling back to GARMIN_EMAIL / GARMIN_PASSWORD from secrets)."""
    from garminconnect import Garmin  # heavy; only when Garmin is actually used

    email, password = secret("GARMIN_EMAIL"), secret("GARMIN_PASSWORD")
    if ask:
        email = email or ask("Garmin Connect email: ")
        password = password or (ask_secret or ask)("Garmin Connect password: ")
    g = Garmin(email, password, prompt_mfa=(lambda: ask("Garmin MFA code: ")) if ask else None)
    g.login(str(_store()))  # loads saved tokens, or logs in and saves new ones
    return g


def finder(cfg):
    """swim_between when Garmin is enabled and authorised, else None (callers then ask or use the plan)."""
    return swim_between if cfg.garmin and authorised() else None


@functools.cache
def _saved_client():
    """One login per process: each login also fetches the profile and settings (extra round trips)."""
    return client()


def fetch_swims(start: str, end: str) -> list[dict]:
    return _saved_client().get_activities_by_date(start, end, "swimming")


def parse(activity: dict) -> Swim | None:
    kind = (activity.get("activityType") or {}).get("typeKey", "")
    seconds = activity.get("elapsedDuration") or activity.get("duration")
    if "swim" not in kind or not seconds or not activity.get("startTimeLocal"):
        return None
    return Swim(datetime.fromisoformat(activity["startTimeLocal"]), seconds / 60, kind, activity.get("distance"))


def swim_between(since: datetime, until: datetime, fetch: Fetch = fetch_swims) -> Swim | None:
    """The first swim that started inside the window (the staged playlist plays from its start)."""
    swims = [s for a in fetch(since.date().isoformat(), until.date().isoformat())
             if (s := parse(a)) and since <= s.start <= until]
    return min(swims, key=lambda s: s.start, default=None)
