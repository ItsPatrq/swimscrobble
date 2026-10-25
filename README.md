# swimscrobble

Offline MP3-mode headphones (built for the Shokz OpenSwim Pro) keep **no play history**, so
nothing can be scrobbled from the device. swimscrobble makes the session deterministic: it
plans what will play, you swim, then it scrobbles exactly what was planned.

## The flow

1. **Plan**: shuffle your library (taste-weighted, no recent repeats, optional BPM arc), keep every
   track that fits the time budget, copy them to the device as `001 - `, `002 - ` ... Headphones play
   alphabetically, so filename order is play order.
2. **Swim.**
3. **Scrobble**: next time you plug in, the session is timestamped back from plug-in time and sent to
   Last.fm (and optionally ListenBrainz).
4. **Podcasts**: the first N episodes of your Pocket Casts *Up Next* queue are mirrored to the device's
   `PocketCast/` folder (not scrobbled).

## Install

```
uv venv && uv pip install -e '.[dev]'
```
Requires Python 3.11+, `yt-dlp` and `ffmpeg` for playlist/download features.

## Config (`~/.config/swimscrobble/config.toml`)

```toml
music_dir = "~/Music/swim"
device_name = "SWIM PRO"
minutes = 60
auto_submit = false          # true: on-connect scrobbles without asking
session_offset_minutes = 0   # gap between finishing the swim and plugging in
ytm_playlist = "https://music.youtube.com/playlist?list=..."
lastfm_user = "yourname"
listenbrainz = false
arc = false                  # warm-up -> peak -> cool-down by BPM tag
max_podcasts = 10
no_repeat_days = 7
```

## Commands

| command | what |
|---|---|
| `plan [dir] --stage DIR` | build a session, write `session.json`, optionally copy with prefixes |
| `scrobble session.json --start ISO [--played-minutes N] [--skip 3,7] [--confirm] [--submit]` | CSV by default; `--submit` sends to Last.fm |
| `scrobble-pending` | scrobble the last staged session (start inferred from now) |
| `lastfm-auth` | one-time Last.fm authorisation (`LASTFM_API_KEY`/`LASTFM_API_SECRET`) |
| `playlist [url] [--download]` | match a YouTube Music playlist to local files; offer to fetch missing ones |
| `sync` | refresh the swim pool from the playlist + Last.fm loved/top/similar |
| `podcasts` | mirror Pocket Casts Up Next (`POCKETCASTS_EMAIL`/`POCKETCASTS_PASSWORD`) |
| `on-connect` | the automatic flow (settle previous session, podcasts, plan + stage next) |
| `agent install` | launchd agent that runs `on-connect` when volumes change |

### Automatic mode

`swimscrobble agent install` registers a launchd agent watching `/Volumes`. On each mount:

- device absent: nothing, and the next connection counts as new
- device present, first event: settle the previous session, sync podcasts, stage the next session
- with `auto_submit = false` (default) the previous session is parked as
  `~/.config/swimscrobble/pending/session-N.csv` with a notification; run `swimscrobble scrobble-pending`
  (use `--played-minutes` / `--skip` to correct it), then re-run `on-connect` to stage the next one.
- failed submissions keep the session pending, nothing is lost or double-sent.

macOS may ask for permission to access removable volumes for the agent's Python.

## Caveats

- Scrobbles are estimates: the tool assumes you listened to the whole session in order. Last.fm
  rejects scrobbles older than 14 days.
- Pocket Casts has no public API; the client uses the reverse-engineered endpoints and Pocket Casts
  does not support third-party clients. Use your own account at your own risk.
- `playlist --download` uses yt-dlp; make sure you have the right to download what you fetch.
- Whether the firmware really plays strictly by filename is unverified, see issue #2.

MIT licensed.
