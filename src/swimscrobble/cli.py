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


def _scan(cfg, folder=None):
    return scan(Path(folder) if folder else cfg.music_path, exclude=tuple(cfg.exclude_dirs), min_seconds=cfg.min_track_seconds)


def _fmt(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def notify(msg: str) -> None:
    print(msg)
    if os.uname().sysname == "Darwin":
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(msg)} with title "swimscrobble"'])


def cmd_plan(a) -> None:
    cfg = cfgmod.load()
    tracks = _scan(cfg, a.music)
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
        from . import flow

        flow._set_handled(False)  # lets `on-connect` stage the next session without re-plugging
        print("settled; run `swimscrobble on-connect` to stage the next session")


def cmd_lastfm_auth(a) -> None:
    from . import lastfm

    lastfm.authenticate()
    print("saved session")


def cmd_playlist(a) -> None:
    """Show which playlist tracks exist under ~/Music; --download fetches the rest with ymp3's command."""
    from . import acquire, ytm
    from .link import LocalIndex

    cfg = cfgmod.load()
    url = a.url or cfg.ytm_playlist
    if not url:
        raise SystemExit("pass a playlist URL or set ytm_playlist in config")
    idx = LocalIndex(_scan(cfg))
    missing = []
    for e in ytm.fetch(url, cookies=cfg.cookies_path):
        t = idx.find(e.artist, e.title, e.duration)
        print(f"{'found  ' if t else 'MISSING'}  #{e.index:<3} {e.artist} - {e.title}" + (f"  -> {t.path}" if t else ""))
        if not t:
            missing.append(e)
    print(f"\n{len(missing)} missing")
    if a.download and missing:
        ok = acquire.download_missing(cfg, url, missing, ask=input)
        print("done; re-run to verify" if ok else "download skipped/failed")


def _taste_fetch(user: str, period: str):
    from . import taste

    top = taste.top_tracks(user, period=period)
    loved = taste.loved_tracks(user)
    sim = []
    for artist, title, _ in top[:10]:  # recommendations seeded by what you played most lately
        sim += taste.similar(artist, title)
    return loved, top, sim


def run_sync(cfg, db, size: int = 150, download: str | bool = False):
    """Scan the library, match the playlist + Last.fm taste to it, refresh the pool.

    download: False = report only, "ask" = prompt first, True = fetch missing playlist tracks.
    """
    from . import acquire, libsync, ytm

    def dl(missing):
        ask = input if download == "ask" else None
        ok = acquire.download_missing(cfg, cfg.ytm_playlist, missing, ask=ask)
        print("download finished" if ok else "download skipped/failed")

    return libsync.sync_library(
        cfg, db, size=size,
        fetch_playlist=lambda url: ytm.fetch(url, cookies=cfg.cookies_path),
        taste_fetch=_taste_fetch,
        download=dl if download else None,
    )


def cmd_sync(a) -> None:
    from . import acquire

    cfg = cfgmod.load()
    rep = run_sync(cfg, history.connect(), a.size, download=("ask" if not a.yes else True) if a.download else False)
    p = rep.pool
    print(f"pool: +{len(p.added)} -{len(p.retired)}; playlist {rep.playlist_total - len(rep.playlist_missing)}/"
          f"{rep.playlist_total} found locally")
    for e in rep.playlist_missing:
        print(f"  missing from library: #{e.index} {e.artist} - {e.title}")
    for c in p.missing[:20]:
        print(f"  recommended, not owned: {c.artist} - {c.title}")
    if a.fetch_missing and p.missing:
        import subprocess

        for c in p.missing[: a.fetch_missing]:
            url = f"ytsearch1:{c.artist} {c.title}"
            subprocess.run(acquire.playlist_download_command(cfg, url))
        print("fetched; re-run sync to add them to the pool")


def cmd_mix(a) -> None:
    from . import flow
    from .energy import cached_features

    cfg = cfgmod.load()
    db = history.connect()
    swim = a.minutes or cfg.minutes
    library = _scan(cfg, a.music)
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
    def pool_sync():
        if not (cfg.lastfm_user or cfg.ytm_playlist):
            return "skipped (no lastfm_user / ytm_playlist configured)"
        rep = run_sync(cfg, db, download=cfg.auto_download and bool(cfg.ytm_playlist))
        left = len(rep.playlist_missing)
        return f"+{len(rep.pool.added)} -{len(rep.pool.retired)}, {left} playlist tracks still missing"

    for line in flow.on_connect(cfg, db, notify=notify, podcast_sync=sync, pool_sync=pool_sync):
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
    sy.add_argument("--download", action="store_true", help="download playlist tracks missing locally (asks first)")
    sy.add_argument("--yes", action="store_true", help="with --download: do not ask")
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
