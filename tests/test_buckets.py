from pathlib import Path

from swimscrobble import taste
from swimscrobble.buckets import assign
from swimscrobble.library import Track
from swimscrobble.taste import Taste
from swimscrobble.ytm import Entry


def T(artist, title, dur=200):
    return Track(Path(f"{artist}-{title}.mp3"), artist, title, "", dur)


LIB = [
    T("Now Band", "Obsessed"), T("Now Band", "Unplayed Deep Cut"), T("Old Band", "Two Months Ago"),
    T("Gem Band", "Forgotten Banger"), T("Loved Band", "Loved Old"), T("New Band", "Similar Song"),
    T("Genre Band", "Genre Hit"), T("Playlist Band", "List Only"), T("Stranger", "Random Unknown"),
]


def by_bucket(owned):
    out = {}
    for c in owned:
        out.setdefault(next(iter(c.sources)), []).append(c.title)
    return {k: sorted(v) for k, v in out.items()}


def test_every_bucket_is_filled_from_history():
    t = Taste(
        chart30=[("Now Band", "Obsessed", 20)],
        chart60=[("Old Band", "Two Months Ago", 6)],
        overall=[("Gem Band", "Forgotten Banger", 80), ("Now Band", "Obsessed", 90), ("Now Band", "Another", 10)],
        loved=[("Loved Band", "Loved Old")],
        similar=[("New Band", "Similar Song", 0.7)],
        artist_tracks={"Now Band": [("Now Band", "Unplayed Deep Cut")]},
        genre_tracks=[("Genre Band", "Genre Hit")],
    )
    owned, wanted = assign(t, [Entry("x", "Playlist Band", "List Only", 200, 1)], LIB)
    b = by_bucket(owned)
    assert b["obsession"] == ["List Only", "Obsessed"]
    assert b["mid"] == ["Two Months Ago"]
    assert b["lost"] == ["Forgotten Banger", "Loved Old"]  # not played in 90 days
    assert b["fresh"] == ["Unplayed Deep Cut"]  # known artist, track never played
    assert b["rec"] == ["Genre Hit", "Similar Song"]
    assert "Random Unknown" not in {c.title for c in owned}  # unknown artist, no signal: not in the pool
    assert [(c.title, next(iter(c.sources))) for c in wanted] == [("Another", "lost")]  # all-time favourite, not owned


def test_recent_play_beats_lost_gem_and_buckets_do_not_overlap():
    t = Taste(chart30=[("Gem Band", "Forgotten Banger", 5)], overall=[("Gem Band", "Forgotten Banger", 80)])
    owned, _ = assign(t, [], LIB)
    assert by_bucket(owned) == {"obsession": ["Forgotten Banger"]}
    assert len({(c.artist, c.title) for c in owned}) == len(owned)


def test_unowned_tracks_become_wishes_with_their_bucket():
    t = Taste(
        chart30=[("Now Band", "Obsessed", 9), ("Now Band", "Not Owned Hit", 5)],
        similar=[("Far Band", "Rec Not Owned", 0.9)],
        artist_tracks={"Now Band": [("Now Band", "Famous Not Owned")]},
    )
    _, wanted = assign(t, [], LIB)
    got = {c.title: next(iter(c.sources)) for c in wanted}
    assert got == {"Not Owned Hit": "obsession", "Rec Not Owned": "rec", "Famous Not Owned": "fresh"}


def test_no_lastfm_data_still_keeps_playlist_tracks():
    owned, _ = assign(None, [Entry("x", "Playlist Band", "List Only", 200, 1)], LIB)
    assert by_bucket(owned) == {"obsession": ["List Only"]}


def test_fetch_taste_uses_dated_windows_and_collects_everything():
    calls = []

    def tr(p):
        calls.append(p)
        m = p["method"]
        if m == "user.getWeeklyTrackChart":
            n = {0: "A", 1: "B", 2: "C"}[(1_000_000 - p["to"]) // (30 * 86400)]
            return {"weeklytrackchart": {"track": [{"artist": {"#text": f"Art{n}"}, "name": f"Song{n}", "playcount": "4"}]}}
        if m == "user.getTopTracks":
            return {"toptracks": {"track": [{"name": "Old", "artist": {"name": "Gem"}, "playcount": "50"}]}}
        if m == "user.getLovedTracks":
            return {"lovedtracks": {"track": [{"name": "L", "artist": {"name": "Love"}}]}}
        if m == "track.getSimilar":
            return {"similartracks": {"track": [{"name": "Sim", "artist": {"name": "Other"}, "match": "0.6"}]}}
        if m == "artist.getTopTracks":
            return {"toptracks": {"track": [{"name": "Hit", "artist": {"name": p["artist"]}}]}}
        if m == "artist.getTopTags":
            return {"toptags": {"tag": [{"name": "seen live"}, {"name": "Industrial"}]}}
        if m == "tag.getTopTracks":
            return {"tracks": {"track": [{"name": "GenreSong", "artist": {"name": "G"}}]}}
        raise AssertionError(m)

    import os
    os.environ["LASTFM_API_KEY"] = "k"
    t = taste.fetch_taste("me", now=1_000_000, transport=tr)
    charts = [c for c in calls if c["method"] == "user.getWeeklyTrackChart"]
    assert [(c["from"], c["to"]) for c in charts] == [
        (1_000_000 - 30 * 86400, 1_000_000), (1_000_000 - 60 * 86400, 1_000_000 - 30 * 86400),
        (1_000_000 - 90 * 86400, 1_000_000 - 60 * 86400)]
    assert t.chart30[0][1] == "SongA" and t.chart60[0][1] == "SongB" and t.chart90[0][1] == "SongC"
    assert t.overall == [("Gem", "Old", 50)] and t.loved == [("Love", "L")]
    assert t.similar and "ArtA" in t.artist_tracks
    assert t.genre_tracks[0] == ("G", "GenreSong")
    assert {c["tag"] for c in calls if c["method"] == "tag.getTopTracks"} == {"industrial"}  # generic tag skipped
