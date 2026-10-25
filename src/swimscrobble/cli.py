from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from . import config as cfgmod
from . import history
from .library import scan
from .planner import plan_session, total_seconds
from .scrobble import build_scrobbles, confirm, skip_positions, to_csv, trim_to_played
from .session import from_manifest, to_manifest


def _fmt(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def notify(msg: str) -> None:
    print(msg)
    if os.uname().sysname == "Darwin":
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg)} with title "swimscrobble"'])


def cmd_plan(a) -> None:
    cfg = cfgmod.load()
    tracks = scan(Path(a.music or cfg.music_path))
    plan = plan_session(tracks, a.minutes or cfg.minutes, a.seed, arc=cfg.arc)
    for p in plan:
        print(f"{p.position:03d}  {_fmt(p.track.duration)}  {p.track.artist} - {p.track.title}")
    print(f"\n{len(plan)} tracks, {_fmt(total_seconds(plan))}")
    manifest = to_manifest(plan, a.seed, a.minutes or cfg.minutes)
    Path(a.out).write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"manifest: {a.out}")
    if a.stage:
        import shutil

        dest = Path(a.stage)
        dest.mkdir(parents=True, exist_ok=True)
        for p in plan:
            shutil.copy2(p.track.path, dest / p.staged_name)
        print(f"staged {len(plan)} files into {dest} (originals untouched)")


def _scrobbles_from_args(plan, a):
    if a.skip:
        plan = skip_positions(plan, {int(x) for x in a.skip.split(",")})
    if a.played_minutes is not None:
        plan = trim_to_played(plan, a.played_minutes * 60)
    return build_scrobbles(plan, datetime.fromisoformat(a.start))


def _deliver(scrobbles, a, cfg) -> bool:
    if a.confirm and not confirm(scrobbles):
        print("aborted")
        return False
    if a.submit:
        from . import lastfm, listenbrainz

        stale = lastfm.check_window(scrobbles)
        if stale:
            raise SystemExit(f"{len(stale)} scrobbles outside Last.fm's 14-day window; fix --start")
        n = lastfm.submit(scrobbles, lastfm.load_session_key())
        print(f"Last.fm accepted {n}/{len(scrobbles)}")
        if cfg.listenbrainz or a.listenbrainz:
            print(f"ListenBrainz sent {listenbrainz.submit(scrobbles)}")
        return True
    text = to_csv(scrobbles)
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out}")
    else:
        print(text)
    return False


def cmd_scrobble(a) -> None:
    cfg = cfgmod.load()
    plan = from_manifest(json.loads(Path(a.manifest).read_text()))
    _deliver(_scrobbles_from_args(plan, a), a, cfg)


def cmd_scrobble_pending(a) -> None:
    cfg = cfgmod.load()
    db = history.connect()
    row = history.pending_session(db)
    if not row:
        raise SystemExit("no pending session")
    plan = from_manifest(json.loads(row["manifest"]))
    a.start = a.start or (
        datetime.now() - timedelta(minutes=cfg.session_offset_minutes)
        - timedelta(seconds=sum(p.track.duration for p in plan))
    ).isoformat(timespec="seconds")
    scrobbles = _scrobbles_from_args(plan, a)
    if _deliver(scrobbles, a, cfg):
        history.mark_scrobbled(db, row["id"], [(s.artist, s.title, s.timestamp.timestamp()) for s in scrobbles])


def cmd_lastfm_auth(a) -> None:
    from . import lastfm

    lastfm.authenticate()
    print("saved session")


def cmd_playlist(a) -> None:
    from . import acquire, matcher, ytm

    cfg = cfgmod.load()
    url = a.url or cfg.ytm_playlist
    if not url:
        raise SystemExit("pass a playlist URL or set ytm_playlist in config")
    results = matcher.match(ytm.fetch(url), scan(cfg.music_path))
    for r in results:
        print(f"{r.status:9} {r.score:.2f}  {r.entry.artist} - {r.entry.title}"
              + (f"  -> {r.track.path.name}" if r.track else ""))
    missing = [r.entry for r in results if r.status == matcher.MISSING]
    if a.download and missing:
        failed = acquire.acquire(missing, cfg.music_path, ask=input)
        print(f"{len(missing) - len(failed)} downloaded, {len(failed)} failed")


def cmd_sync(a) -> None:
    from . import generator, poolsync, taste, ytm

    cfg = cfgmod.load()
    db = history.connect()
    library = scan(cfg.music_path)
    lib_keys = {generator.key(t.artist, t.title) for t in library}
    playlist = []
    if cfg.ytm_playlist:
        playlist = [generator.key(e.artist, e.title) for e in ytm.fetch(cfg.ytm_playlist)]
    loved, top, sim = [], [], []
    if cfg.lastfm_user:
        loved = taste.loved_tracks(cfg.lastfm_user)
        top = taste.top_tracks(cfg.lastfm_user)
        for artist, title in loved[:10]:
            sim += taste.similar(artist, title)
    cands = generator.score_candidates(playlist, loved, top, sim)
    rep = poolsync.sync_pool(db, cands, lib_keys, history.play_counts(db), a.size)
    print(f"added {len(rep.added)}, retired {len(rep.retired)}, wanted-but-missing {len(rep.missing)}")
    for c in rep.missing[:20]:
        print(f"  missing: {c.artist} - {c.title}")


def cmd_podcasts(a) -> None:
    from . import pocketcasts

    cfg = cfgmod.load()
    db = history.connect()
    token = pocketcasts.saved_token() or pocketcasts.login()
    dest = Path(a.dest) if a.dest else cfg.podcast_path
    print(pocketcasts.sync_queue(pocketcasts.up_next(token), dest, db, cfg.max_podcasts))


def cmd_on_connect(a) -> None:
    from . import flow, pocketcasts

    cfg = cfgmod.load()
    db = history.connect()
    sync = None
    if pocketcasts.saved_token():
        sync = lambda: pocketcasts.sync_queue(  # noqa: E731
            pocketcasts.up_next(pocketcasts.saved_token()),
            cfg.device_path / cfg.device_podcast_subdir, db, cfg.max_podcasts)
    for line in flow.on_connect(cfg, db, notify=notify, podcast_sync=sync):
        print(line)


def cmd_agent(a) -> None:
    from . import launchd

    if a.action == "install":
        print(f"installed {launchd.install(os.getuid())}")
    else:
        launchd.uninstall(os.getuid())
        print("uninstalled")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="swimscrobble")
    sub = ap.add_subparsers(required=True)

    p = sub.add_parser("plan", help="build a randomised session")
    p.add_argument("music", nargs="?")
    p.add_argument("--minutes", type=float)
    p.add_argument("--seed", type=int)
    p.add_argument("--out", default="session.json")
    p.add_argument("--stage", help="copy files here with position prefixes")
    p.set_defaults(func=cmd_plan)

    def scrobble_opts(x, start_required):
        x.add_argument("--start", required=start_required, help="session start, ISO e.g. 2026-10-25T07:30")
        x.add_argument("--played-minutes", type=float, help="stopped early: only count what was heard")
        x.add_argument("--skip", help="comma-separated positions you skipped, e.g. 3,7")
        x.add_argument("--confirm", action="store_true", help="show the list and ask first")
        x.add_argument("--submit", action="store_true", help="send to Last.fm (default: print CSV)")
        x.add_argument("--listenbrainz", action="store_true")
        x.add_argument("--out")

    s = sub.add_parser("scrobble", help="scrobble a session manifest")
    s.add_argument("manifest")
    scrobble_opts(s, True)
    s.set_defaults(func=cmd_scrobble)

    sp = sub.add_parser("scrobble-pending", help="scrobble the last staged session")
    scrobble_opts(sp, False)
    sp.set_defaults(func=cmd_scrobble_pending)

    sub.add_parser("lastfm-auth", help="authorise on Last.fm").set_defaults(func=cmd_lastfm_auth)

    pl = sub.add_parser("playlist", help="match a YouTube Music playlist to local files")
    pl.add_argument("url", nargs="?")
    pl.add_argument("--download", action="store_true", help="offer to download missing tracks")
    pl.set_defaults(func=cmd_playlist)

    sy = sub.add_parser("sync", help="refresh the swim pool from playlist + Last.fm taste")
    sy.add_argument("--size", type=int, default=150)
    sy.set_defaults(func=cmd_sync)

    po = sub.add_parser("podcasts", help="mirror the Pocket Casts Up Next queue")
    po.add_argument("--dest")
    po.set_defaults(func=cmd_podcasts)

    sub.add_parser("on-connect", help="the automatic flow (run by launchd)").set_defaults(func=cmd_on_connect)

    ag = sub.add_parser("agent", help="install/uninstall the launchd agent")
    ag.add_argument("action", choices=["install", "uninstall"])
    ag.set_defaults(func=cmd_agent)

    args = ap.parse_args(argv)
    args.func(args)
