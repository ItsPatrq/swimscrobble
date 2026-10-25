"""Interactive shell: dashboard, stats, playlist editing, reroll, sync, scrobble, agent.

Run `swimscrobble` with no arguments. Every screen is a method; all slow or system-touching actions
(Last.fm, YouTube, the launchd agent) go through `Actions`, so the whole thing runs against fakes in tests.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import editing, flow, history, stats
from .config import Config
from .library import Track
from .link import LocalIndex
from .matcher import norm
from .scrobble import build_scrobbles
from .session import played_part

COLORS = {"obsession": "magenta", "mid": "cyan", "lost": "yellow", "fresh": "green", "rec": "bright_blue", "added": "white"}
HELP = """\
[bold]p[/] playlist      view the staged playlist (and edit it: d 3,5 delete · m 7 2 move · a add)
[bold]r[/] reroll        replace the staged playlist with a new one
[bold]a[/] add track     search your library (or YouTube Music) and put a track into the playlist
[bold]s[/] stats         listening, pool and swim statistics
[bold]w[/] wishlist      tracks you play or Last.fm suggests but you do not own; download them
[bold]y[/] sync          refresh Last.fm history and the pool; download missing tracks
[bold]c[/] scrobble      settle the last swim (how long, what you skipped) and send it to Last.fm
[bold]k[/] skip          forget the staged playlist: you did not swim
[bold]o[/] podcasts      mirror your Pocket Casts Up Next queue to the headphones
[bold]e[/] on-connect    run the whole automatic routine now (settle, sync, stage)
[bold]g[/] agent         install / remove the launchd agent that runs the routine when you plug in
[bold]q[/] quit"""


@dataclass
class Actions:
    """Everything that talks to the network, the disk-heavy analysers or the OS."""

    load_library: Callable[[], list[Track]]
    features: Callable[[Track], object]
    sync: Callable[[bool], str]  # download? -> summary
    search_youtube: Callable[[str], list[dict]]
    download_video: Callable[[str], bool]
    find_song: Callable[[str, str], object]  # artist, title -> Entry | None
    podcasts: Callable[[], str]
    on_connect: Callable[[], list[str]]
    submit: Callable[[list], str]
    agent_installed: Callable[[], bool]
    agent_set: Callable[[bool], None]


def fmt_min(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


class Shell:
    def __init__(self, cfg: Config, db, actions: Actions, console: Console | None = None,
                 ask: Callable[[str], str] = input):
        self.cfg, self.db, self.act = cfg, db, actions
        self.console = console or Console(highlight=False)
        self._ask = ask
        self._library: list[Track] | None = None

    # -- plumbing ---------------------------------------------------------------------------------
    def ask(self, prompt: str, default: str = "") -> str:
        try:
            answer = self._ask(f"{prompt}{f' [{default}]' if default else ''} ").strip()
        except EOFError:
            raise SystemExit(0) from None
        return answer or default

    def say(self, *a, **kw) -> None:
        self.console.print(*a, **kw)

    def yes(self, prompt: str, default: bool = False) -> bool:
        a = self.ask(f"{prompt} ({'Y/n' if default else 'y/N'})").lower()
        return default if a == "" else a.startswith("y")

    def library(self, refresh: bool = False) -> list[Track]:
        if self._library is None or refresh:
            self._library = self.act.load_library()
        return self._library

    def session(self):
        row = history.pending_session(self.db)
        return (row, json.loads(row["manifest"])) if row else (None, None)

    # -- dashboard --------------------------------------------------------------------------------
    def dashboard(self) -> None:
        d = stats.dashboard(self.cfg, self.db, agent_installed=self.act.agent_installed())
        t = Table.grid(padding=(0, 2))
        t.add_column(style="bold")
        t.add_column()
        dev = (f"[green]connected[/] · {d.device_free_gb:.1f} GB free" if d.device_connected
               else "[red]not connected[/]")
        t.add_row("Headphones", f"{self.cfg.device_name} {dev}")
        if d.staged:
            s = d.staged
            ranked = sorted(s["buckets"].items(), key=lambda kv: -kv[1])
            mix = "  ".join(f"[{COLORS.get(b, 'white')}]{b} {n}[/]" for b, n in ranked)
            t.add_row("Staged", f"session {s['id']}: {s['tracks']} tracks, {s['minutes']:.0f} min, "
                                f"staged {s['age_hours']:.1f} h ago")
            t.add_row("", f"{s['swim_tracks']} tracks fill your {s['swim_minutes']:.0f}-min swim · {mix}")
        else:
            t.add_row("Staged", "[dim]nothing waiting: press r to stage a playlist[/]")
        sync = "never" if d.last_sync_hours is None else f"{d.last_sync_hours:.1f} h ago"
        t.add_row("Library", f"{sum(d.pool.values())} tracks in the pool · wishlist {d.wishlist} · synced {sync}")
        t.add_row("History", f"{d.scrobbles:,} scrobbles ({d.scrobbles_30d:,} in 30 days) · {d.swum_recently} swum in the last "
                             f"{self.cfg.no_repeat_days} days (skipped)")
        t.add_row("Agent", "[green]installed[/] (runs when you plug in)" if d.agent_installed else "[dim]not installed (g)[/]")
        self.say(Panel(t, title="[bold]swimscrobble[/]", border_style="cyan"))
        self.say(f"[dim]{self.hint(d)}[/]")

    def hint(self, d) -> str:
        if d.staged and d.staged["age_hours"] * 60 >= self.cfg.min_swim_gap_minutes and d.device_connected:
            return "Next: swimming? plug in afterwards and press c to scrobble it · r re-rolls the playlist"
        if not d.staged:
            return "Next: r stages a playlist on the headphones" if d.device_connected else "Next: connect the headphones, then r"
        return "Keys: p playlist · r reroll · a add track · s stats · w wishlist · y sync · c scrobble · ? help · q quit"

    # -- screens ----------------------------------------------------------------------------------
    def playlist(self) -> None:
        while True:
            row, m = self.session()
            if not row:
                self.say("[yellow]No staged playlist.[/] Press r to create one.")
                return
            tracks = sorted(m["tracks"], key=lambda t: t["position"])
            swim = m.get("minutes", self.cfg.minutes) * 60
            table = Table(title=f"Session {row['id']} · {len(tracks)} tracks", header_style="bold", show_edge=False)
            cols = (("#", "right"), ("at", "right"), ("len", "right"), ("energy", "right"), ("bucket", "left"),
                    ("track", "left"))
            for h, j in cols:
                table.add_column(h, justify=j)
            t, marked = 0.0, False
            for tr in tracks:
                if not marked and t >= swim:
                    table.add_row("", "", "", "", "", f"[dim]── {swim / 60:.0f} min: end of your swim, buffer below ──[/]")
                    marked = True
                b = tr.get("source", "")
                e = tr.get("energy")
                table.add_row(str(tr["position"]), fmt_min(t), fmt_min(tr["duration"]), f"{e:.2f}" if e else "",
                              f"[{COLORS.get(b, 'white')}]{b}[/]", f"{tr['artist']} - {tr['title']}")
                t += tr["duration"]
            self.say(table)
            cmd = self.ask("[playlist] d 3,5 delete · m 7 2 move · a add · r reroll · Enter back >").split()
            if not cmd:
                return
            try:
                if cmd[0] == "d" and len(cmd) > 1:
                    pos = {int(x) for x in cmd[1].split(",")}
                    editing.remove_tracks(self.cfg, self.db, pos)
                    self.say(f"[green]removed {len(pos)}[/]")
                elif cmd[0] == "m" and len(cmd) == 3:
                    editing.move_track(self.cfg, self.db, int(cmd[1]), int(cmd[2]))
                    self.say("[green]moved[/]")
                elif cmd[0] == "a":
                    self.add_track()
                elif cmd[0] == "r":
                    self.reroll()
                else:
                    self.say("[yellow]unknown command[/]")
            except (ValueError, RuntimeError) as e:
                self.say(f"[red]{e}[/]")

    def reroll(self) -> None:
        if not self.cfg.device_path.is_dir():
            self.say(f"[red]{self.cfg.device_name} is not connected.[/]")
            return
        row, _ = self.session()
        if row and not self.yes("Replace the staged playlist? It will not be scrobbled", default=True):
            return
        if self.yes("Refresh Last.fm history and the pool first? (slower)", default=False):
            self.say(f"[dim]{self.act.sync(False)}[/]")
            self._library = None
        seed = self.ask("Seed (blank = random)")
        self.say("[dim]building playlist ...[/]")
        try:
            m = flow.reroll(self.cfg, self.db, int(seed) if seed.isdigit() else None,
                            library_loader=lambda _: self.library(), feature_of=self.act.features)
        except RuntimeError as e:
            self.say(f"[red]{e}[/]")
            return
        flow._set_handled(True)
        self.say(f"[green]staged session {m['id']}:[/] {len(m['tracks'])} tracks, "
                 f"{sum(t['duration'] for t in m['tracks']) / 60:.0f} min")
        self.playlist()

    def search_local(self, query: str, limit: int = 10) -> list[Track]:
        words = norm(query).split()
        hits = [t for t in self.library() if all(w in norm(f"{t.artist} {t.title}") for w in words)]
        return sorted(hits, key=lambda t: (t.artist.casefold(), t.title.casefold()))[:limit]

    def add_track(self) -> None:
        row, m = self.session()
        if not row:
            self.say("[yellow]No staged playlist to add to.[/] Press r first.")
            return
        q = self.ask("Search (artist / title)")
        if not q:
            return
        track = self.pick_local(q) or self.pick_youtube(q)
        if track is None:
            return
        n_swim, _ = stats.swim_split(m, m.get("minutes", self.cfg.minutes))
        pos = self.ask(f"Position 1-{len(m['tracks']) + 1} (1 plays first; Enter = {n_swim}, the end of your swim)", str(n_swim))
        feat = self.act.features(track)
        if feat.energy < self.cfg.min_energy and not self.yes(
                f"Energy {feat.energy:.2f} is below your minimum {self.cfg.min_energy}: a slow track. Add anyway?"):
            return
        try:
            editing.add_track(self.cfg, self.db, track, int(pos) if pos.isdigit() else n_swim, round(feat.energy, 2))
        except (ValueError, RuntimeError) as e:
            self.say(f"[red]{e}[/]")
            return
        self.say(f"[green]added[/] {track.artist} - {track.title} at position {pos}")

    def pick_local(self, query: str) -> Track | None:
        hits = self.search_local(query)
        if hits:
            t = Table(show_edge=False, header_style="bold")
            for h in ("#", "artist", "title", "len"):
                t.add_column(h)
            for i, h in enumerate(hits, 1):
                t.add_row(str(i), h.artist, h.title, fmt_min(h.duration))
            self.say(t)
        else:
            self.say("[dim]nothing in your library matches[/]")
        a = self.ask("Number to pick · y = search YouTube Music · Enter cancels")
        if a.isdigit() and 1 <= int(a) <= len(hits):
            return hits[int(a) - 1]
        if a.lower() == "y":
            return None
        raise _Cancel

    def pick_youtube(self, query: str) -> Track | None:
        results = self.act.search_youtube(query)
        if not results:
            self.say("[yellow]no results[/]")
            return None
        t = Table(show_edge=False, header_style="bold")
        for h in ("#", "artist", "title", "album", "len"):
            t.add_column(h)
        for i, r in enumerate(results, 1):
            t.add_row(str(i), ", ".join(a["name"] for a in r.get("artists") or []), r.get("title", ""),
                      (r.get("album") or {}).get("name", ""), fmt_min(r.get("duration_seconds") or 0))
        self.say(t)
        a = self.ask("Number to download and use · Enter cancels")
        if not (a.isdigit() and 1 <= int(a) <= len(results)):
            return None
        r = results[int(a) - 1]
        self.say("[dim]downloading ...[/]")
        if not self.act.download_video(r["videoId"]):
            self.say("[red]download failed[/]")
            return None
        lib = self.library(refresh=True)
        artist = ", ".join(x["name"] for x in r.get("artists") or [])
        return LocalIndex(lib).find(artist, r["title"], r.get("duration_seconds"))

    def stats(self) -> None:
        now = time.time()
        for title, rows in (("Top artists, last 30 days", stats.top_artists(self.db, 30, 8, now)),):
            t = Table(title=title, show_edge=False, header_style="bold")
            t.add_column("artist")
            t.add_column("plays", justify="right")
            for a, c in rows:
                t.add_row(a, str(c))
            self.say(t)
        t = Table(title="Top tracks, last 30 days", show_edge=False, header_style="bold")
        t.add_column("track")
        t.add_column("plays", justify="right")
        for a, ti, c in stats.top_tracks(self.db, 30, 8, now):
            t.add_row(f"{a} - {ti}", str(c))
        self.say(t)
        mix = stats.listening_mix(self.db, 30, now)
        if mix:
            self.say(f"Of {int(mix['tracks'])} different tracks in 30 days: [green]{mix['new to you']:.0%} new to you[/], "
                     f"[yellow]{mix['back after 1y+']:.0%} back after a year+[/], {mix['repeat']:.0%} repeats")
        d = stats.dashboard(self.cfg, self.db)
        lib = self.library()
        self.say(f"Library: {len(lib)} tracks, {sum(t.duration for t in lib) / 3600:.1f} h · pool: "
                 + (", ".join(f"[{COLORS.get(b, 'white')}]{b} {n}[/]" for b, n in d.pool.items()) or "empty"))
        s = stats.swim_stats(self.db)
        done = s["sessions"].get("scrobbled", 0)
        last = datetime.fromtimestamp(s["last_swim"]).strftime("%Y-%m-%d") if s["last_swim"] else "never"
        self.say(f"Swims: {done} scrobbled ({s['swim_minutes'] / 60:.1f} h in the water), "
                 f"{s['sessions'].get('discarded', 0)} discarded, last {last}")
        if s["most_swum"]:
            self.say("Most swum: " + " · ".join(f"{a} - {ti} ×{c}" for a, ti, c in s["most_swum"]))

    def wishlist(self) -> None:
        items = stats.wishlist_lines(self.cfg.mix_weights)
        if not items:
            self.say("[yellow]The wishlist is empty.[/] Run a sync (y) to build it.")
            return
        t = Table(title=f"Wishlist ({len(items)}): tracks you play or Last.fm suggests, not in your library",
                  show_edge=False, header_style="bold")
        for h in ("#", "bucket", "track"):
            t.add_column(h)
        for i, (label, bucket, _url) in enumerate(items[:20], 1):
            t.add_row(str(i), f"[{COLORS.get(bucket, 'white')}]{bucket}[/]", label)
        self.say(t)
        a = self.ask("Download which? (e.g. 1,3,5 or 'top 5') · Enter back")
        if not a:
            return
        idx = list(range(1, 6)) if a.startswith("top") else [int(x) for x in a.replace(" ", "").split(",") if x.isdigit()]
        got = 0
        for i in idx:
            if not 1 <= i <= min(20, len(items)):
                continue
            label = items[i - 1][0]
            artist, _, title = label.partition(" - ")
            entry = self.act.find_song(artist, title)
            if entry and self.act.download_video(entry.video_id):
                got += 1
                self.say(f"[green]downloaded[/] {label}")
            else:
                self.say(f"[yellow]could not get[/] {label}")
        if got:
            self.library(refresh=True)
            self.say("They are in your library now; add one to the playlist with a, or sync (y) to pool them.")

    def sync(self) -> None:
        download = self.yes("Also download missing playlist and wishlist tracks?", default=True)
        self.say("[dim]syncing ... (first run after a long gap can take a while)[/]")
        self.say(self.act.sync(download))
        self.library(refresh=True)

    def scrobble(self) -> None:
        row, m = self.session()
        if not row:
            self.say("[yellow]Nothing staged, so nothing to scrobble.[/]")
            return
        age_min = (time.time() - row["planned_at"]) / 60
        if age_min < self.cfg.min_swim_gap_minutes and not self.yes(
                f"This was staged only {age_min:.0f} min ago. Scrobble anyway?"):
            return
        swim = float(self.ask("How many minutes did you swim?", f"{m.get('minutes', self.cfg.minutes):.0f}") or 0)
        skip = self.ask("Positions you skipped (comma separated, blank = none)")
        skipped = {int(x) for x in skip.replace(" ", "").split(",") if x.isdigit()}
        plan, listened = played_part(m, swim, skipped)
        end = datetime.now() - timedelta(minutes=self.cfg.session_offset_minutes)
        scrobbles = build_scrobbles(plan, end - timedelta(seconds=listened))
        t = Table(title=f"{len(scrobbles)} scrobbles", show_edge=False, header_style="bold")
        t.add_column("time")
        t.add_column("track")
        for s in scrobbles:
            t.add_row(f"{s.timestamp:%a %H:%M:%S}", f"{s.artist} - {s.title}")
        self.say(t)
        if not scrobbles or not self.yes("Send these to Last.fm?", default=True):
            return
        try:
            msg = self.act.submit(scrobbles)
        except Exception as e:  # keep the session so nothing is lost
            self.say(f"[red]failed: {e}[/] (the session is still pending)")
            return
        history.mark_scrobbled(self.db, row["id"], [(s.artist, s.title, s.timestamp.timestamp()) for s in scrobbles])
        flow._set_handled(False)
        self.say(f"[green]{msg}[/]")
        if self.yes("Stage the next playlist now?", default=True):
            self.reroll()

    def skip(self) -> None:
        row, _ = self.session()
        if not row:
            self.say("Nothing staged.")
        elif self.yes("Forget the staged playlist without scrobbling (you did not swim)?"):
            history.discard_pending(self.db)
            flow._set_handled(False)
            self.say("[green]discarded[/]; nothing was scrobbled")

    def agent(self) -> None:
        on = self.act.agent_installed()
        self.say("The agent runs the whole routine automatically whenever the headphones are plugged in "
                 f"(currently [bold]{'installed' if on else 'not installed'}[/]).")
        if self.yes(f"{'Remove' if on else 'Install'} it?"):
            self.act.agent_set(not on)
            self.say("[green]done[/]")

    def run(self) -> None:
        handlers: dict[str, Callable[[], None]] = {
            "p": self.playlist, "r": self.reroll, "a": self.add_track, "s": self.stats, "w": self.wishlist,
            "y": self.sync, "c": self.scrobble, "k": self.skip, "o": lambda: self.say(self.act.podcasts()),
            "e": lambda: [self.say(x) for x in self.act.on_connect()], "g": self.agent,
            "?": lambda: self.say(HELP), "h": lambda: self.say(HELP),
        }
        names = {"playlist": "p", "reroll": "r", "add": "a", "stats": "s", "wishlist": "w", "sync": "y", "scrobble": "c",
                 "skip": "k", "podcasts": "o", "on-connect": "e", "agent": "g", "help": "?", "quit": "q", "exit": "q"}
        self.dashboard()
        while True:
            key = self.ask("\nswimscrobble>").lower()
            key = names.get(key, key[:1]) if key else ""
            if key in ("q",):
                return
            if not key:
                self.dashboard()
                continue
            fn = handlers.get(key)
            if fn is None:
                self.say("[yellow]unknown key[/] · ? for help")
                continue
            try:
                fn()
            except _Cancel:
                self.say("[dim]cancelled[/]")
            except KeyboardInterrupt:
                self.say("\n[dim]interrupted[/]")
            except Exception as e:  # a failing action must not kill the shell
                self.say(f"[red]error: {e}[/]")
            self.dashboard()


class _Cancel(Exception):
    pass


def real_actions(cfg: Config, db) -> Actions:
    """The production wiring: the same functions the command line uses."""
    from . import acquire, cli, lastfm, launchd, listenbrainz, pocketcasts
    from .energy import cached_features

    def sync(download: bool) -> str:
        rep = cli.run_sync(cfg, db, download=download)
        p = rep.pool
        return (f"pool +{len(p.added)} -{len(p.retired)} · playlist {rep.playlist_total - len(rep.playlist_missing)}/"
                f"{rep.playlist_total} found · {len(p.missing)} wishlist entries")

    def podcasts() -> str:
        token = pocketcasts.saved_token() or pocketcasts.login()
        return str(pocketcasts.sync_queue(pocketcasts.up_next(token), cfg.device_path / cfg.device_podcast_subdir,
                                          db, cfg.max_podcasts))

    def on_connect() -> list[str]:
        flow._set_handled(False)
        return flow.on_connect(cfg, db, notify=cli.notify, pool_sync=lambda: sync(cfg.auto_download))

    def submit(scrobbles: list) -> str:
        stale = lastfm.check_window(scrobbles)
        if stale:
            raise ValueError(f"{len(stale)} scrobbles are older than Last.fm's 14-day limit")
        n = lastfm.submit(scrobbles, lastfm.load_session_key())
        if cfg.listenbrainz:
            listenbrainz.submit(scrobbles)
        return f"Last.fm accepted {n}/{len(scrobbles)}"

    import os

    def agent_set(on: bool) -> None:
        launchd.install(os.getuid()) if on else launchd.uninstall(os.getuid())

    return Actions(
        load_library=lambda: cli._scan(cfg),
        features=lambda t: cached_features(db, t),
        sync=sync,
        search_youtube=lambda q: acquire.yt_search(q, 8),
        download_video=lambda vid: acquire.download_video(cfg, vid),
        find_song=lambda a, t: acquire.find_song(a, t),
        podcasts=podcasts, on_connect=on_connect, submit=submit,
        agent_installed=lambda: Path(launchd.PLIST).exists(), agent_set=agent_set,
    )
