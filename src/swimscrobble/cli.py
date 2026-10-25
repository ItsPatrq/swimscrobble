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
from .mix import build_mix
from .planner import plan_session, total_seconds
from .scrobble import build_scrobbles, confirm, to_csv
from .session import played_part, to_manifest


def _fmt(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def notify(msg: str) -> None:
    print(msg)
    if os.uname().sysname == "Darwin":
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg)} with title "swimscrobble"'])


def cmd_plan(a) -> None:
    cfg = cfgmod.load()
    tracks = scan(Path(a.music or cfg.music_path))
    swim = a.minutes or cfg.minutes
    plan = plan_session(tracks, swim * cfg.buffer_factor, a.seed, arc=cfg.arc)
    for p in plan:
        print(f"{p.position:03d}  {_fmt(p.track.duration)}  {p.track.artist} - {p.track.title}")
    print(f"\n{len(plan)} tracks, {_fmt(total_seconds(plan))}")
    manifest = to_manifest(plan, a.seed, swim)
    Path(a.out).write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"manifest: {a.out}")
    if a.stage:
        import shutil

        dest = Path(a.stage)
        dest.mkdir(parents=True, exist_ok=True)
        for p in plan:
            shutil.copy2(p.track.path, dest / p.staged_name)
        print(f"staged {len(plan)} files into {dest} (originals untouched)")


def _scrobbles_from_args(manifest, a):
    """Skipped tracks never played; the swim length (or --played-minutes) bounds what was heard."""
    skipped = {int(x) for x in a.skip.split(",")} if a.skip else set()
    plan, _ = played_part(manifest, a.played_minutes, skipped)
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
    manifest = json.loads(Path(a.manifest).read_text())
    _deliver(_scrobbles_from_args(manifest, a), a, cfg)


def cmd_scrobble_pending(a) -> None:
    cfg = cfgmod.load()
    db = history.connect()
    row = history.pending_session(db)
    if not row:
        raise SystemExit("no pending session")
    manifest = json.loads(row["manifest"])
    skipped = {int(x) for x in a.skip.split(",")} if a.skip else set()
    _, listened = played_part(manifest, a.played_minutes, skipped)
    a.start = a.start or (
        datetime.now() - timedelta(minutes=cfg.session_offset_minutes) - timedelta(seconds=listened)
    ).isoformat(timespec="seconds")
    scrobbles = _scrobbles_from_args(manifest, a)
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
    from . import acquire, generator, poolsync, taste, ytm

    cfg = cfgmod.load()
    db = history.connect()
    library = scan(cfg.music_path)
    lib_keys = {generator.key(t.artist, t.title) for t in library}
    playlist = []
    if cfg.ytm_playlist:
        playlist = [generator.key(e.artist, e.title) for e in ytm.fetch(cfg.ytm_playlist)]
    loved, top, sim = [], [], []
    if cfg.lastfm_user:
        top = taste.top_tracks(cfg.lastfm_user, period=cfg.taste_period)
        loved = taste.loved_tracks(cfg.lastfm_user)
        for artist, title, _ in top[:10]:  # recommendations seeded by what you played most lately
            sim += taste.similar(artist, title)
    cands = generator.score_candidates(playlist, loved, top, sim)
    rep = poolsync.sync_pool(db, cands, lib_keys, history.play_counts(db), a.size)
    print(f"added {len(rep.added)}, retired {len(rep.retired)}, wanted-but-missing {len(rep.missing)}")
    for c in rep.missing[:20]:
        print(f"  missing ({','.join(sorted(c.sources))}): {c.artist} - {c.title}")
    if a.fetch_missing and rep.missing:
        wanted = [acquire.resolve(c.artist, c.title) for c in rep.missing[: a.fetch_missing]]
        failed = acquire.acquire([e for e in wanted if e], cfg.music_path, ask=input)
        print(f"fetched {len([e for e in wanted if e]) - len(failed)}; re-run sync to add them to the pool")


def cmd_mix(a) -> None:
    from . import flow
    from .energy import cached_features

    cfg = cfgmod.load()
    db = history.connect()
    swim = a.minutes or cfg.minutes
    library = scan(Path(a.music or cfg.music_path))
    plan = build_mix(
        library, flow.pool_candidates(db), lambda t: cached_features(db, t),
        minutes=swim * cfg.buffer_factor, seed=a.seed, rec_ratio=cfg.rec_ratio, min_energy=a.min_energy or cfg.min_energy,
        plays=history.play_counts(db), recent=history.recent_plays(db, cfg.no_repeat_days),
    )
    for p in plan:
        print(f"{p.position:03d}  {_fmt(p.track.duration)}  e={p.energy:.2f}  {p.source:3}  {p.track.artist} - {p.track.title}")
    recs = sum(1 for p in plan if p.source == "rec")
    print(f"\n{len(plan)} tracks ({recs} recommendations), {_fmt(total_seconds(plan))}")
    Path(a.out).write_text(json.dumps(to_manifest(plan, a.seed, swim), indent=2, ensure_ascii=False))
    if a.stage:
        import shutil

        dest = Path(a.stage)
        dest.mkdir(parents=True, exist_ok=True)
        for p in plan:
            shutil.copy2(p.track.path, dest / p.staged_name)
        print(f"staged {len(plan)} files into {dest}")


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

    mx = sub.add_parser("mix", help="energising favourites + a few recommendations (uses pool, energy analysis)")
    mx.add_argument("music", nargs="?")
    mx.add_argument("--minutes", type=float)
    mx.add_argument("--seed", type=int)
    mx.add_argument("--min-energy", type=float)
    mx.add_argument("--out", default="session.json")
    mx.add_argument("--stage")
    mx.set_defaults(func=cmd_mix)

    sub.add_parser("lastfm-auth", help="authorise on Last.fm").set_defaults(func=cmd_lastfm_auth)

    pl = sub.add_parser("playlist", help="match a YouTube Music playlist to local files")
    pl.add_argument("url", nargs="?")
    pl.add_argument("--download", action="store_true", help="offer to download missing tracks")
    pl.set_defaults(func=cmd_playlist)

    sy = sub.add_parser("sync", help="refresh the swim pool from playlist + Last.fm taste")
    sy.add_argument("--size", type=int, default=150)
    sy.add_argument("--fetch-missing", type=int, default=0, metavar="N",
                    help="offer to download up to N wanted-but-missing tracks")
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
