# swimscrobble

Offline MP3-mode headphones (built for the Shokz OpenSwim Pro) keep **no play history**, so
nothing can be scrobbled from the device. swimscrobble works around that by making the
session deterministic: it plans what will play, you swim, then it scrobbles exactly what
was planned.

## How it works

1. `swimscrobble plan ~/Music/swim --minutes 60 --stage "/Volumes/SWIM PRO/Music"`
   shuffles your library, keeps every track that fits the time budget, and copies them with
   `001 - `, `002 - ` ... prefixes. The headphones play alphabetically, so filename order is
   play order. A `session.json` manifest records the plan.
2. Swim.
3. `swimscrobble scrobble session.json --start 2026-10-25T07:30` emits timestamped scrobble
   records (CSV today; direct Last.fm / ListenBrainz submission is planned).

## Status

Early. See [docs/PLAN.md](docs/PLAN.md) and the open issues for the roadmap
(YouTube Music playlist sync, taste-based playlist generation, Last.fm submission).

## Dev

```
uv venv && uv pip install -e '.[dev]' && .venv/bin/pytest
```

## Caveat

Scrobbles are *estimates*: the tool assumes you listened to the whole session in order
without pauses or skips. Last.fm rejects scrobbles older than 14 days, so submit promptly.

MIT licensed.
