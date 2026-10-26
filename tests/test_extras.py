import json
import sqlite3
import subprocess
from datetime import datetime
from types import SimpleNamespace

import pytest

from swimscrobble import audio, garmin, history, pocketcasts


def tone(path, seconds=6, volume_db=-30, gaps=False):
    """A sine mp3; with `gaps`, 1 s tone / 2 s silence repeated (a podcast with long pauses)."""
    src = f"sine=frequency=440:duration={seconds}"
    flt = f"volume={volume_db}dB" + (",volume='if(lt(mod(t,3),1),1,0)':eval=frame" if gaps else "")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-af", flt, "-c:a", "libmp3lame", str(path)],
                   check=True)
    return path


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


# --- Garmin ---------------------------------------------------------------------------------------

def act(kind, start, seconds, **kw):
    return {"activityType": {"typeKey": kind}, "startTimeLocal": start, "elapsedDuration": seconds, **kw}


def test_garmin_picks_the_first_swim_inside_the_window():
    acts = [
        act("running", "2026-10-25 07:00:00", 1800),
        act("lap_swimming", "2026-10-25 06:00:00", 3000),  # before the playlist was staged
        act("lap_swimming", "2026-10-25 09:10:00", 2700),
        act("lap_swimming", "2026-10-25 08:05:00", 3300.5, distance=2000.0),
    ]
    asked = []
    swim = garmin.swim_between(datetime(2026, 10, 25, 7, 30), datetime(2026, 10, 25, 10),
                               lambda s, e: (asked.append((s, e)) or acts))
    assert swim.start == datetime(2026, 10, 25, 8, 5) and round(swim.minutes) == 55 and swim.distance_m == 2000
    assert asked == [("2026-10-25", "2026-10-25")]
    assert "lap swimming" in swim.describe() and "2000 m" in swim.describe()


def test_garmin_no_swim_and_not_enabled():
    assert garmin.swim_between(datetime(2026, 10, 25), datetime(2026, 10, 26), lambda s, e: []) is None
    assert garmin.finder(type("C", (), {"garmin": False})()) is None


# --- loudness -------------------------------------------------------------------------------------

@pytest.mark.ffmpeg
def test_quiet_track_is_levelled_once_and_cached(tmp_path, monkeypatch):
    src = tone(tmp_path / "quiet.mp3", volume_db=-30)
    cache = tmp_path / "cache"
    out = audio.normalised(src, -14, cache)
    assert out != src and out.parent == cache
    assert abs(float(audio.measure(out)["input_i"]) + 14) < 1.5
    calls = []
    monkeypatch.setattr(audio, "ffmpeg", lambda *a: calls.append(a))
    assert audio.normalised(src, -14, cache) == out and not calls  # second time: straight from the cache


@pytest.mark.ffmpeg
def test_track_already_at_target_is_copied_untouched(tmp_path):
    src = tmp_path / "loud.mp3"
    probe = tone(tmp_path / "probe.mp3", volume_db=0)
    gain = -14 - float(audio.measure(probe)["input_i"])
    tone(src, volume_db=gain)  # lands on -14 LUFS
    assert audio.normalised(src, -14, tmp_path / "cache") == src
    assert audio.normalised(src, -14, tmp_path / "cache") == src  # remembered without measuring again


@pytest.mark.ffmpeg
def test_unreadable_file_is_staged_as_it_is(tmp_path):
    src = tmp_path / "broken.mp3"
    src.write_bytes(b"not audio")
    assert audio.leveller(-14, tmp_path / "cache")(src) == src  # unreadable: staged as it is
    assert audio.leveller(None)(src) == src


def test_old_cache_entries_are_pruned(tmp_path):
    import os

    (tmp_path / "old.mp3").write_bytes(b"x")
    (tmp_path / "new.mp3").write_bytes(b"x")
    os.utime(tmp_path / "old.mp3", (1, 1))
    assert audio.prune(tmp_path) == 1 and sorted(p.name for p in tmp_path.iterdir()) == [".pruned", "new.mp3"]
    os.utime(tmp_path / "new.mp3", (1, 1))
    assert audio.prune(tmp_path) == 0  # at most once a day


# --- podcasts -------------------------------------------------------------------------------------

def test_podcast_filter():
    assert audio.podcast_filter(1.0, False) is None
    assert audio.podcast_filter(1.3, False) == "atempo=1.30"
    assert audio.podcast_filter(1.3, True).startswith("silenceremove=") and audio.podcast_filter(1.3, True).endswith("atempo=1.30")


@pytest.mark.ffmpeg
def test_podcast_is_sped_up_and_pauses_are_cut(tmp_path):
    src = tone(tmp_path / "ep.mp3", seconds=12, volume_db=-10, gaps=True)  # 4 s speech, 8 s silence
    audio.process_podcast(src, tmp_path / "fast.mp3", 1.5, True)
    assert duration(tmp_path / "fast.mp3") < 0.6 * duration(src)


def test_processing_download_falls_back_to_the_original(tmp_path, monkeypatch):
    monkeypatch.setattr(pocketcasts, "http_download", lambda url, dest: dest.write_bytes(b"raw audio"))
    def boom(*a): raise RuntimeError("ffmpeg failed")
    monkeypatch.setattr(audio, "process_podcast", boom)
    fetcher = pocketcasts.Fetcher.for_settings(1.3, True)
    assert (fetcher.ext, fetcher.variant) == (".mp3", "1.30x+trim")
    fetcher.fetch("https://x/a.mp3", tmp_path / "01 - a.mp3")
    assert [p.name for p in tmp_path.iterdir()] == ["01 - a.mp3"] and (tmp_path / "01 - a.mp3").read_bytes() == b"raw audio"


def test_processed_episodes_are_named_mp3_and_remember_their_podcast(tmp_path):
    db = history.connect(tmp_path / "h.db")
    eps = [{"uuid": "e1", "podcastUuid": "p1", "title": "One", "url": "https://x/1.m4a"}]
    pocketcasts.sync_queue(eps, tmp_path / "dev", db, fetcher=pocketcasts.Fetcher(lambda u, d: d.write_bytes(b"x"), ".mp3"))
    assert pocketcasts.on_device(db) == [{"uuid": "e1", "podcast": "p1", "title": "One", "filename": "01 - One.mp3"}]


def test_changing_the_processing_refetches_instead_of_renaming(tmp_path):
    db = history.connect(tmp_path / "h.db")
    eps = [{"uuid": "e1", "podcastUuid": "p1", "title": "One", "url": "https://x/1.m4a"}]
    got = []
    dl = lambda u, d: (got.append(d.name), d.write_bytes(b"x"))  # noqa: E731
    pocketcasts.sync_queue(eps, tmp_path, db, fetcher=dl)
    pocketcasts.sync_queue(eps, tmp_path, db, fetcher=pocketcasts.Fetcher(dl, ".mp3", "1.30x"))
    pocketcasts.sync_queue(eps, tmp_path, db, fetcher=pocketcasts.Fetcher(dl, ".mp3", "1.30x"))  # unchanged: nothing fetched
    pocketcasts.sync_queue(eps, tmp_path, db, fetcher=pocketcasts.Fetcher(dl, ".mp3", "1.50x"))  # same format, new speed
    assert got == ["01 - One.m4a", "01 - One.mp3", "01 - One.mp3"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["01 - One.mp3", "h.db"]


def test_mark_played_tells_pocket_casts():
    sent = []
    n = pocketcasts.mark_played([{"uuid": "e1", "podcast": "p1"}], "tok",
                                lambda path, body, tok: sent.append((path, body)) or {})
    assert n == 1 and sent == [("/sync/update_episode", {"uuid": "e1", "podcast": "p1", "status": 3})]


def test_old_database_gets_the_podcast_column(tmp_path):
    old = sqlite3.connect(tmp_path / "h.db")
    old.execute("CREATE TABLE podcasts(uuid TEXT PRIMARY KEY, title TEXT, filename TEXT, downloaded_at REAL)")
    old.execute("INSERT INTO podcasts VALUES('e1','One','01 - One.mp3',0)")
    old.commit()
    old.close()
    db = history.connect(tmp_path / "h.db")
    assert pocketcasts.on_device(db)[0]["podcast"] is None


# --- 5: space on the headphones -------------------------------------------------------------------

def _plan(tmp_path, n=10, mb=10):
    from swimscrobble.library import Track
    from swimscrobble.planner import PlannedTrack

    out = []
    for i in range(n):
        f = tmp_path / f"t{i}.mp3"
        f.write_bytes(b"x" * int(mb * 1e6))
        out.append(PlannedTrack(i + 1, Track(f, "A", f"T{i}", "", 300)))
    return out


def test_buffer_tracks_are_dropped_until_the_playlist_fits(tmp_path, monkeypatch):
    from swimscrobble import flow

    plan = _plan(tmp_path)  # 10 x 5 min, 10 MB each
    monkeypatch.setattr(flow.shutil, "disk_usage", lambda p: SimpleNamespace(free=280e6))
    cfg = SimpleNamespace(stage_path=tmp_path, minutes=30, space_reserve_mb=200)
    kept = flow.fit_to_space(plan, cfg)  # 80 MB usable: 7 tracks (11 MB each)
    assert len(kept) == 7
    with pytest.raises(RuntimeError, match="not enough space"):
        flow.fit_to_space(plan, SimpleNamespace(stage_path=tmp_path, minutes=45, space_reserve_mb=200))  # the swim itself does not fit


def test_podcasts_leave_room_for_the_playlist(tmp_path, monkeypatch):
    db = history.connect(tmp_path / "h.db")
    monkeypatch.setattr(pocketcasts.shutil, "disk_usage", lambda p: SimpleNamespace(free=500e6))
    eps = [{"uuid": f"e{i}", "title": f"Ep {i}", "url": f"https://x/{i}.mp3", "size": str(150_000_000)} for i in range(3)]
    rep = pocketcasts.sync_queue(eps, tmp_path / "dev", db, fetcher=lambda u, d: d.write_bytes(b"x"), reserve_mb=400)
    assert rep.no_space == ["01 - Ep 0.mp3", "02 - Ep 1.mp3", "03 - Ep 2.mp3"] and rep.on_device == 0
    rep = pocketcasts.sync_queue(eps, tmp_path / "dev", db, fetcher=lambda u, d: d.write_bytes(b"x"), reserve_mb=300)
    assert rep.downloaded == ["01 - Ep 0.mp3"] and len(rep.no_space) == 2  # 200 MB to spare: room for one


# --- 6: skips -------------------------------------------------------------------------------------

def test_skips_are_counted_and_a_thrice_skipped_track_is_retired(tmp_path):
    from swimscrobble.energy import Features
    from swimscrobble.library import Track
    from swimscrobble.mix import build_mix

    db = history.connect(tmp_path / "h.db")
    for sid in (1, 2, 3):
        db.execute("INSERT INTO sessions(id, status) VALUES(?, 'planned')", (sid,))
        history.mark_scrobbled(db, sid, [], skipped=[("Skip", "Me")] + ([("Skip", "Other")] if sid == 1 else []))
    tracks, artists = history.skip_counts(db)
    assert tracks[("skip", "me")] == 3 and artists["skip"] == 4
    lib = [Track(tmp_path / f"{i}.mp3", "Skip" if i == 0 else f"A{i}", "Me" if i == 0 else f"T{i}", "", 240)
           for i in range(12)]
    plan = build_mix(lib, [], lambda t: Features(125, -7, 150, 0.6, 0.8), minutes=60, swim_minutes=30, seed=1,
                     skips=tracks, artist_skips=artists)
    assert plan and all(p.track.title != "Me" for p in plan)


# --- 7: liked discoveries -------------------------------------------------------------------------

def test_love_and_playlist_id(monkeypatch):
    from swimscrobble import lastfm, ytm

    sent = []
    monkeypatch.setattr(lastfm, "credentials", lambda: ("k", "s"))
    lastfm.love("A", "T", "sk", lambda body: sent.append(body) or {})
    assert sent[0]["method"] == "track.love" and sent[0]["artist"] == "A" and sent[0]["sk"] == "sk"
    assert ytm.playlist_id("https://music.youtube.com/playlist?list=PL123&si=x") == "PL123"


# --- 8: workouts ----------------------------------------------------------------------------------

def test_workout_shapes():
    from swimscrobble.workouts import WORKOUTS

    lo, mid, hi = 0.5, 0.7, 0.9
    iv = [WORKOUTS["intervals"].target(p / 100, lo, mid, hi) for p in range(15, 90)]
    assert {round(x, 2) for x in iv} == {lo, hi} and sum(a != b for a, b in zip(iv, iv[1:])) >= 4
    assert WORKOUTS["pyramid"].target(0.5, lo, mid, hi) == hi and WORKOUTS["pyramid"].target(0.0, lo, mid, hi) == lo
    assert max(WORKOUTS["easy"].target(p / 10, lo, mid, hi) for p in range(10)) < mid


def test_every_workout_orders_a_mix(tmp_path):
    import numpy as np

    from swimscrobble.sequence import order
    from swimscrobble.workouts import WORKOUTS

    rng = np.random.default_rng(0)
    e = rng.uniform(0.5, 1.0, 12)
    for w in WORKOUTS:
        perm = order(e, np.full(12, 128.0), np.zeros(12), np.arange(12), np.zeros(12, int), np.full(12, 240.0),
                     2400, np.random.default_rng(1), iters=500, workout=w)
        assert sorted(perm) == list(range(12))


# --- 9: recap -------------------------------------------------------------------------------------

def test_recap_counts_swims_minutes_distance_and_discoveries(tmp_path):
    from swimscrobble import stats

    db = history.connect(tmp_path / "h.db")
    m = {"tracks": [{"position": 1, "artist": "A", "title": "Old", "source": "obsession"},
                    {"position": 2, "artist": "B", "title": "New", "source": "rec"},
                    {"position": 3, "artist": "C", "title": "Nope", "source": "fresh"}]}
    sid = history.add_session(db, m, 60, None, "x")
    history.mark_scrobbled(db, sid, [("A", "Old", 1.0), ("B", "New", 2.0)], skipped=[("C", "Nope")],
                           minutes=52, distance_m=2100)
    r = stats.recap(db, 7)
    assert (r.swims, r.minutes, r.distance_m, r.tracks, r.skipped) == (1, 52, 2100, 2, 1)
    assert r.discoveries == [("B", "New")]
    assert stats.recap(db, 7, now=__import__("time").time() + 30 * 86400).swims == 0


def test_old_sessions_table_gets_the_swim_columns(tmp_path):
    old = sqlite3.connect(tmp_path / "h.db")
    old.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY, planned_at REAL, seed INTEGER, minutes REAL, "
                "status TEXT NOT NULL DEFAULT 'planned', staged_dir TEXT, manifest TEXT, scrobbled_at REAL)")
    old.commit()
    old.close()
    db = history.connect(tmp_path / "h.db")
    assert {"swum_minutes", "distance_m"} <= {r["name"] for r in db.execute("PRAGMA table_info(sessions)")}


# --- 10: one notification per connection ----------------------------------------------------------

def test_podcast_and_staged_summaries():
    from swimscrobble import flow

    assert str(pocketcasts.PodcastReport(downloaded=["a"], failed=["b: 403"], on_device=6)) == "6 podcasts (1 new), 1 FAILED"
    m = {"tracks": [{"duration": 600, "source": "rec"}, {"duration": 600, "source": "obsession"}], "workout": "intervals"}
    assert flow.staged_summary(m) == "staged 2 tracks, 20 min, 1 new (intervals)"
