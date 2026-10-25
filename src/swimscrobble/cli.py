from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from .library import scan
from .planner import plan_session, total_seconds
from .scrobble import build_scrobbles, to_csv


def _fmt(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def cmd_plan(args) -> None:
    tracks = scan(Path(args.music))
    plan = plan_session(tracks, args.minutes, args.seed)
    for p in plan:
        print(f"{p.position:03d}  {_fmt(p.track.duration)}  {p.track.artist} - {p.track.title}")
    print(f"\n{len(plan)} tracks, {_fmt(total_seconds(plan))} of {args.minutes} min")
    manifest = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "seed": args.seed,
        "tracks": [
            {"position": p.position, "file": p.track.path.name, "staged": p.staged_name,
             "artist": p.track.artist, "title": p.track.title, "album": p.track.album,
             "duration": round(p.track.duration)}
            for p in plan
        ],
    }
    out = Path(args.out)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"manifest: {out}")
    if args.stage:
        dest = Path(args.stage)
        dest.mkdir(parents=True, exist_ok=True)
        for p in plan:
            shutil.copy2(p.track.path, dest / p.staged_name)
        print(f"staged {len(plan)} files into {dest} (originals untouched)")


def cmd_scrobble(args) -> None:
    from .planner import PlannedTrack
    from .library import Track

    manifest = json.loads(Path(args.manifest).read_text())
    plan = [
        PlannedTrack(t["position"], Track(Path(t["file"]), t["artist"], t["title"], t["album"], t["duration"]))
        for t in manifest["tracks"]
    ]
    start = datetime.fromisoformat(args.start)
    csv_text = to_csv(build_scrobbles(plan, start))
    if args.out:
        Path(args.out).write_text(csv_text)
        print(f"wrote {args.out}")
    else:
        print(csv_text)


def main() -> None:
    ap = argparse.ArgumentParser(prog="swimscrobble")
    sub = ap.add_subparsers(required=True)

    p = sub.add_parser("plan", help="build a randomised session for the headphones")
    p.add_argument("music", help="folder with audio files")
    p.add_argument("--minutes", type=float, default=60)
    p.add_argument("--seed", type=int)
    p.add_argument("--out", default="session.json")
    p.add_argument("--stage", help="copy files here with position prefixes (alphabetical == play order)")
    p.set_defaults(func=cmd_plan)

    s = sub.add_parser("scrobble", help="emit scrobble records for a played session")
    s.add_argument("manifest")
    s.add_argument("--start", required=True, help="session start, ISO e.g. 2026-10-25T07:30")
    s.add_argument("--out")
    s.set_defaults(func=cmd_scrobble)

    args = ap.parse_args()
    args.func(args)
