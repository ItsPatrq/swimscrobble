# Plan

Goal: swim with offline headphones, then confidently scrobble what played.

## Assumptions
- Device plays files alphabetically by filename (to verify, see issue on playback-order check).
- A session is ~60 min; every track that fits the budget counts as played.
- Device has no play log (investigated: FAT32, `SYSTEM/allsong.lst` + `order.lst` are only an index and order).

## Status
All milestones below are implemented and unit/e2e tested without hardware, except live verification
against Last.fm, Pocket Casts, the launchd install and device playback order (see issues).

## Milestones
1. **Session planner (done, v0)** – scan library, seeded shuffle, time-budget fit, prefixed staging, manifest.
2. **Scrobbling** – timestamp math (done), CSV (done), Last.fm submit, ListenBrainz submit, 14-day window check.
3. **Playlist source** – follow a YouTube Music playlist; map entries to local files; fetch missing.
4. **Playlist maintenance** – generate/refresh the swim playlist from Last.fm taste + other playlists + recommendations.
5. **Polish** – config file, dedupe/no-repeat history, packaging, CI.

## Open design questions
- How are tracks matched to the YouTube Music playlist (title+artist fuzzy vs. video ID in tags)?
- Where do missing files come from (user-supplied folder, yt-dlp, ask the user)?
- Does a partially played last track count? (Last.fm: >=50% or 4 min.)
