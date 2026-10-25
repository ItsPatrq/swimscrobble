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
   - **Then check each against your Last.fm history**, mirrored locally (the last `history_days`, then
     updated incrementally). Recency-decayed play counts (chosen by offline evaluation, see
     [docs/ALGORITHM.md](docs/ALGORITHM.md)) put every track in one of five buckets, each with a share of the
     time (`mix_weights`):
     - **obsession** (40%): by an artist you are into right now, any of their tracks, played or not;
     - **mid** (15%): played within the last 90 days;
     - **fresh** (15%): never played, but by an artist you do listen to;
     - **lost** (10%): played or loved long ago, silent for 90+ days;
     - **rec** (20%): never played, by artists you have not played either (discovery), boosted when
       Last.fm calls them similar to your recent obsession or in your genres.
   - **Selection**: a determinantal point process picks relevant tracks that are also *different* from each
     other (sound and artist), different every run (`variety`), steered towards the bucket shares.
   - **Sequence**: an optimiser orders them as a workout: warm-up, a few surges, a small taper over the swim
     window, smooth tempo and energy transitions, no artist twice in a row.
   - *Energy*: every file is analysed locally (tempo, loudness, onset density, pulse clarity; cached).
     Tracks below `min_energy` (default 0.5, i.e. slow ballads and ambient pieces) are dropped, and
     high-energy tracks are drawn more often. This applies to every bucket, including lost gems.
   - *Rest*: tracks you swam to in the last `no_repeat_days` are skipped, and heavily swum ones fade out.
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
max_per_artist = 4           # soft cap; the selection already spreads artists
variety = 2.5                # randomness of the selection
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
| `status` | what is staged, history and pool sizes, what is excluded right now |
| `reroll [--sync]` | replace the staged playlist on the connected headphones with a fresh one (old session discarded, not scrobbled) |
| `skip` | forget the staged session without scrobbling (you did not swim) |
| `eval` | replay your Last.fm history and score the models behind the playlist (reproduces docs/ALGORITHM.md) |
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

## The interactive shell

Run `swimscrobble` with no arguments (or `swimscrobble shell`). A dashboard shows the headphones (connected, free
space), the staged playlist (tracks, minutes, how many fill your swim, bucket mix), library and pool sizes,
wishlist, your Last.fm history, what is excluded as recently swum, and whether the agent is installed. Then
one key per action:

| key | |
|---|---|
| `p` | the staged playlist with times, energy and bucket per track, a marker where your swim ends; edit in place: `d 3,5` delete, `m 7 2` move, `a` add |
| `r` | reroll: a new playlist (optionally refreshing Last.fm first, optional seed); the old one is discarded, not scrobbled |
| `a` | add a track: search your library, or YouTube Music (downloads it), then pick the position; slow tracks are flagged |
| `s` | statistics: top artists and tracks, how much of your listening is new / returning / repeat, library and pool, swims |
| `w` | wishlist (balanced across buckets): download chosen tracks right now |
| `y` | sync: Last.fm history, pool, downloads |
| `c` | scrobble the last swim: how long you swam, which tracks you skipped, preview, send; then stage the next |
| `k` | skip: forget the staged playlist (you did not swim) |
| `u` / `x` | use / remove staged files still on the headphones after a discarded session (the dashboard warns when there are some) |
| `o` / `e` / `g` | podcasts / run the whole automatic routine now / install or remove the launchd agent |

**Keyboard navigation**: on a terminal the menu is driven by `↑`/`↓` (or `j`/`k`), `Enter` and `Esc`; every
item also has a shortcut letter that fires immediately. Lists (search results, YouTube Music results) are picked
with the arrows; the wishlist is a checklist (`Space` ticks, `a` all/none, `Enter` downloads); yes/no questions
are `y`/`n`. In the playlist view the cursor walks the tracks: `Enter` picks a track up, `↑`/`↓` carry it, `Enter`
drops it; `d` deletes (with confirmation), `a` adds, `r` rerolls, `q` goes back. Use `swimscrobble shell --plain`
(or `SWIMSCROBBLE_PLAIN=1`) for plain line input; that is also used automatically when not on a terminal.

**What "staged" means**: the playlist this tool copied to the headphones (files named `001 - …`) and recorded in
its database, so it knows what to scrobble. If you discard the record (`k`) but leave the files, they still play;
the dashboard then says "none tracked" and offers `u` (adopt them again) or `x` (remove them). `k` now also offers
to remove the files.

The shell and the agent share everything (database, config, device state), so use either, or both: the agent
does the routine when you plug in, the shell is for looking, correcting and rerolling.

## Daily use

With the agent installed (`swimscrobble agent install`) and `auto_submit = true`, the routine is just plugging in:

1. **Before a swim**: plug in. The previous swim is scrobbled (first `minutes` of that playlist), the pool is
   refreshed (new Last.fm plays, wishlist downloads), a new 2-hour playlist is staged. Eject, swim.
2. **After the swim**: plug in again (also to charge/transfer). That is step 1 for the next swim.

Without the agent, run `swimscrobble on-connect` at those moments. If `auto_submit` is off, the previous swim
waits as `~/.config/swimscrobble/pending/*.csv` until `swimscrobble scrobble-pending [--played-minutes N]
[--skip 3,7] --confirm --submit`, then `swimscrobble on-connect` stages the next one.

**Every playlist is new.** The selection is randomised (`variety`), history moves on (what you played since
changes the buckets), tracks you swam to in the last `no_repeat_days` (7) are skipped, and the more you swam to
a track the less it is picked. Only the first `minutes` of the 2-hour list count as played; the unplayed
buffer is *not* marked as swum, so it comes back first.

**Rotation / corrections**
- Do not like the list? `swimscrobble reroll` (add `--sync` to refresh from Last.fm first) stages another one.
- Staged but did not swim? `swimscrobble skip`. Plugging in again within `min_swim_gap_minutes` (20) of staging
  never scrobbles, so a second plug-in is harmless.
- Swam a different length? `scrobble-pending --played-minutes 45` (or `minutes` in the config).
- Check where things stand: `swimscrobble status`.
- More variety over time: keep running `sync --download` (or let `auto_download` do it) so the library grows.

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
