# swimscrobble

Offline MP3-mode headphones (built for the Shokz OpenSwim Pro) keep **no play history**, so
nothing can be scrobbled from the device. swimscrobble makes the session deterministic: it
plans what will play, you swim, then it scrobbles exactly what was planned.

## The flow

1. **Plan** (the *mix*): build a varied, energising 2-hour playlist and copy it to the device as
   `001 - `, `002 - ` ... Headphones play alphabetically, so filename order is play order.
   - **Pool first**: there is one library folder, `music_dir` (default `~/Music/YouTubeMusic`). Everything
     in it is eligible; playlist tracks and wishlist tracks are downloaded into it, and the playlist(s)
     (`ytm_playlist` / `ytm_playlists`) are just another source of downloads. Set `pool = "playlist"` to
     restrict the session to playlist tracks (plus wishlist downloads).
   - **Then check each against your Last.fm history**, which is mirrored locally (the last
     `history_days`, then updated incrementally), so the answers are exact. Five buckets, each with a share of the
     time (`mix_weights`):
     - **obsession** (40%): played in the last 30 days;
     - **mid** (15%): played 30-90 days ago and dropped since;
     - **fresh** (15%): never played, but by an artist you do listen to;
     - **lost** (10%): played or loved long ago, silent for 90+ days;
     - **rec** (20%): never played, by artists you have not played either (discovery), boosted when
       Last.fm calls them similar to your recent obsession or in your genres.
   A bucket that comes up short hands its time to the others. Within a bucket tracks are drawn at
   random (taste is flattened so the same favourites do not win every time), at most 3 per artist
   (`max_per_artist`).
   - *Energy*: every file is analysed locally (tempo, loudness, onset density, pulse clarity; cached).
     Tracks below `min_energy` (default 0.5, i.e. slow ballads and ambient pieces) are dropped, and
     high-energy tracks are drawn more often. This applies to every bucket, including lost gems.
   - *Order*: a randomised walk that avoids big energy jumps, back-to-back artists and long runs of
     one bucket. Tracks you swam to in the last `no_repeat_days` are skipped, and heavily swum ones
     fade out.
   - *Wishlist, no playlist upkeep needed*: tracks you play a lot (or Last.fm suggests) but have no
     file for are looked up on YouTube Music (songs only; the title and artist must both match, so no
     covers or live versions) and downloaded, up to `auto_fetch` (default 20) per sync, into the same
     library folder (`<album>/<artist> - <title>.mp3`). Only tracks you played 3+ times in the last year are wanted (podcast-looking titles are skipped, the
     list is capped at 300). Misses are remembered, retried after 30 days, and do not use up the quota. The list with search links is also
     written to `~/.config/swimscrobble/wishlist.txt`. Adding tracks to your playlist still works too
     and gives you control over what is in the pool.
2. **Swim.**
3. **Scrobble**: the staged list (`playlist_minutes`, default 120) is longer than the swim
   (`minutes`, default 60) so it can't run out. Only the first `minutes` of it counts as heard: next time you plug in, those tracks are
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
music_dir = "~/Music/YouTubeMusic"   # the single library: scanned recursively, downloaded into, staged to the device from here
exclude_dirs = []
auto_download = true          # on-connect: download playlist tracks missing from music_dir + wishlist tracks
auto_fetch = 20               # wishlist tracks per sync (0 = off)
cookies_file = "~/music.youtube.com_cookies.txt"
device_name = "SWIM PRO"
minutes = 60                 # how long you swim (the part that gets scrobbled)
playlist_minutes = 120       # staged playlist length; the rest is buffer
auto_submit = false          # true: on-connect scrobbles without asking
session_offset_minutes = 0   # gap between finishing the swim and plugging in
ytm_playlist = "https://music.youtube.com/playlist?list=..."
history_days = 365           # first Last.fm history pull goes this far back; later pulls are incremental
ytm_playlists = []           # optional extra playlists, all synced
pool = "library"             # or "playlist"
lastfm_user = "yourname"
listenbrainz = false
arc = false                  # (plain `plan` only) warm-up -> peak -> cool-down by BPM tag
mix_weights = { obsession = 0.40, mid = 0.15, fresh = 0.15, lost = 0.10, rec = 0.20 }
max_per_artist = 3
min_energy = 0.5             # 0..1; raise to cut more mid-tempo tracks
max_podcasts = 10
no_repeat_days = 7
```

## Commands

| command | what |
|---|---|
| `mix [dir] [--playlist-minutes N] [--min-energy X] [--stage DIR]` | the varied energising session (shows energy and bucket per track) |
| `plan [dir] --stage DIR` | plain shuffle without taste or energy, for comparison |
| `scrobble session.json --start ISO [--played-minutes N] [--skip 3,7] [--confirm] [--submit]` | CSV by default; `--submit` sends to Last.fm |
| `scrobble-pending` | scrobble the last staged session (start inferred from now) |
| `lastfm-auth` | one-time Last.fm authorisation (`LASTFM_API_KEY`/`LASTFM_API_SECRET`) |
| `playlist [url] [--download]` | list which playlist tracks exist under `music_dir`; `--download` fetches the missing ones |
| `sync [--download [--yes]] [--fetch-missing N]` | refresh the swim pool: Last.fm 30/60/90-day charts, all-time top/loved, similar tracks, artist and genre top tracks, plus the playlist; optionally offer to download N wanted tracks you don't have yet |
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

`music_dir` (default `~/Music/YouTubeMusic`) is scanned recursively on every `sync` / `on-connect`. Files under
`exclude_dirs`, hidden files and anything under 60 s are ignored. The playlist and your Last.fm tracks
are matched to those files by title and artist (tolerant of `(Remastered)`, `[MIXED]`, accents and
`A, B` credits), and the swim pool only ever contains files you actually have.

Playlist tracks with no matching file are downloaded with the same command as your `ymp3` alias
(`download_command` in the config, run through `yt-dlp` with your cookies), limited to just the missing
positions via `--playlist-items`, so nothing already in your library is downloaded again. New files land
in `music_dir` and are picked up on the next scan. On connect, the chosen tracks are copied from there to
the device (the library itself is never emptied).

## Caveats

- Scrobbles are estimates: the tool assumes you listened to the whole session in order. Last.fm
  rejects scrobbles older than 14 days.
- Pocket Casts has no public API; the client uses the reverse-engineered endpoints and Pocket Casts
  does not support third-party clients. Use your own account at your own risk.
- `playlist --download` uses yt-dlp; make sure you have the right to download what you fetch.
- Energy is a heuristic from the audio (it catches ballads/ambient well, but fast-but-mellow or slow-but-heavy tracks can misfire). Tune `min_energy`, check with `swimscrobble mix`.
- Whether the firmware really plays strictly by filename is unverified, see issue #2.

MIT licensed.
