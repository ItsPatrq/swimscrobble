# swimscrobble

Offline MP3-mode headphones (built for the Shokz OpenSwim Pro) keep **no play history**, so
nothing can be scrobbled from the device. swimscrobble makes the session deterministic: it
plans what will play, you swim, then it scrobbles exactly what was planned.

## The flow

1. **Plan** (the *mix*): build an energising, semi-random session that fits the time budget and copy
   it to the device as `001 - `, `002 - ` ... Headphones play alphabetically, so filename order is
   play order. How the mix is built:
   - *Favourites* (about 80%): tracks in your YouTube Music playlist, your Last.fm top tracks of the
     last 30 days (`taste_period`), or loved. *Recommendations* (about 20%, `rec_ratio`): tracks
     Last.fm only knows as "similar" to your recent top tracks, spread evenly through the session.
   - *Energy*: every file is analysed locally (tempo, loudness, onset density, pulse clarity; cached).
     Tracks below `min_energy` (default 0.5, i.e. slow ballads and ambient pieces) are dropped, and
     high-energy tracks are sampled more often.
   - *Order*: a randomised walk that avoids big energy jumps and back-to-back artists. Tracks you
     swam to in the last `no_repeat_days` are skipped, and heavily swum ones fade out.
2. **Swim.**
3. **Scrobble**: the staged list is `buffer_factor` (default 2x) longer than the swim so it can't run
   out. Only the first `minutes` of it counts as heard: next time you plug in, those tracks are
   timestamped back from plug-in time and sent to Last.fm (and optionally ListenBrainz). If you swam
   a different length, say so with `--played-minutes`.
4. **Podcasts**: the first N episodes of your Pocket Casts *Up Next* queue are mirrored to the device's
   `PocketCast/` folder (not scrobbled).

## Install

```
uv tool install --editable .     # puts `swimscrobble` on your PATH (~/.local/bin)
uv venv && uv pip install -e '.[dev]'   # only needed for development / running tests
```
Requires Python 3.11+, `yt-dlp` and `ffmpeg` for playlist/download features.

## Config (`~/.config/swimscrobble/config.toml`)

```toml
music_dir = "~/Music"          # the whole tree is scanned (recursively)
exclude_dirs = ["Logic", "Audio Music Apps"]
auto_download = true          # on-connect: download playlist tracks missing from music_dir
cookies_file = "~/music.youtube.com_cookies.txt"
download_dir = "~/Music/YouTubeMusic"
device_name = "SWIM PRO"
minutes = 60                 # how long you swim
buffer_factor = 2.0          # staged playlist = minutes x this, so it never runs dry
auto_submit = false          # true: on-connect scrobbles without asking
session_offset_minutes = 0   # gap between finishing the swim and plugging in
ytm_playlist = "https://music.youtube.com/playlist?list=..."
lastfm_user = "yourname"
listenbrainz = false
arc = false                  # (plain `plan` only) warm-up -> peak -> cool-down by BPM tag
taste_period = "1month"      # Last.fm window for top tracks
rec_ratio = 0.2              # share of the session for recommendations
min_energy = 0.5             # 0..1; raise to cut more mid-tempo tracks
max_podcasts = 10
no_repeat_days = 7
```

## Commands

| command | what |
|---|---|
| `mix [dir] [--minutes N] [--min-energy X] [--stage DIR]` | the energising favourites + recommendations session (shows energy and fav/rec per track) |
| `plan [dir] --stage DIR` | plain shuffle without taste or energy, for comparison |
| `scrobble session.json --start ISO [--played-minutes N] [--skip 3,7] [--confirm] [--submit]` | CSV by default; `--submit` sends to Last.fm |
| `scrobble-pending` | scrobble the last staged session (start inferred from now) |
| `lastfm-auth` | one-time Last.fm authorisation (`LASTFM_API_KEY`/`LASTFM_API_SECRET`) |
| `playlist [url] [--download]` | list which playlist tracks exist under `music_dir`; `--download` fetches the missing ones |
| `sync [--download [--yes]] [--fetch-missing N]` | refresh the swim pool from the playlist + 30-day Last.fm top/loved/similar; optionally offer to download N wanted tracks you don't have yet |
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

## Library and downloads

`music_dir` (default `~/Music`) is scanned recursively on every `sync` / `on-connect`. Files under
`exclude_dirs`, hidden files and anything under 60 s are ignored. The playlist and your Last.fm tracks
are matched to those files by title and artist (tolerant of `(Remastered)`, `[MIXED]`, accents and
`A, B` credits), and the swim pool only ever contains files you actually have.

Playlist tracks with no matching file are downloaded with the same command as your `ymp3` alias
(`download_command` in the config, run through `yt-dlp` with your cookies), limited to just the missing
positions via `--playlist-items`, so nothing already in your library is downloaded again. New files land
in `download_dir` and are picked up on the next scan.

## Caveats

- Scrobbles are estimates: the tool assumes you listened to the whole session in order. Last.fm
  rejects scrobbles older than 14 days.
- Pocket Casts has no public API; the client uses the reverse-engineered endpoints and Pocket Casts
  does not support third-party clients. Use your own account at your own risk.
- `playlist --download` uses yt-dlp; make sure you have the right to download what you fetch.
- Energy is a heuristic from the audio (it catches ballads/ambient well, but fast-but-mellow or slow-but-heavy tracks can misfire). Tune `min_energy`, check with `swimscrobble mix`.
- Whether the firmware really plays strictly by filename is unverified, see issue #2.

MIT licensed.
