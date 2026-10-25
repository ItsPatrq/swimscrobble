from pathlib import Path

from swimscrobble import taste
from swimscrobble.buckets import assign
from swimscrobble.lfhistory import Stat
from swimscrobble.library import Track
from swimscrobble.taste import Taste

DAY = 86400
NOW = 4000 * DAY


def T(artist, title, dur=200):
    return Track(Path(f"{artist}-{title}.mp3"), artist, title, "", dur)


LIB = [
    T("Now Band", "Obsessed"), T("Now Band", "Unplayed Deep Cut"), T("Old Band", "Two Months Ago"),
    T("Gem Band", "Forgotten Banger"), T("Loved Band", "Loved Old"), T("New Band", "Similar Song"),
    T("Genre Band", "Genre Hit"), T("Stranger", "Random Unknown"),
]
# Last.fm spells things its own way (accents, remaster tags) - linking must cope
HISTORY = {
    ("Now Band", "Obsessed"): Stat(90, 20, 10, 10, 1),
    ("Now Band", "Another Old Hit"): Stat(30, 0, 0, 0, NOW - 200 * DAY),
    ("Dead Band", "Played In 2014"): Stat(50, 0, 0, 0, NOW - 3000 * DAY),
    ("CD-Action Air", "Film Cyberpunk 2077! Kto zagra Keanu Reevesa? | #179"): Stat(9, 9, 0, 0, NOW - DAY),
    ("Rare Band", "Played Twice"): Stat(2, 0, 2, 0, NOW - 50 * DAY),
    ("Old Band", "Two Months Ago (Remastered)"): Stat(6, 0, 6, 0, 1),
    ("Gem Band", "Forgotten Banger"): Stat(80, 0, 0, 0, 1),
    ("Unowned Band", "Played A Lot"): Stat(25, 25, 0, 0, NOW - DAY),
}


def by_bucket(owned):
    out = {}
    for c in owned:
        out.setdefault(next(iter(c.sources)), []).append(c.title)
    return {k: sorted(v) for k, v in out.items()}


def test_each_track_is_judged_against_history():
    t = Taste(loved=[("Loved Band", "Loved Old")], similar=[("New Band", "Similar Song", 0.7)],
              genre_tracks=[("Genre Band", "Genre Hit")])
    owned, _ = assign(t, HISTORY, LIB)
    b = by_bucket(owned)
    assert b["obsession"] == ["Obsessed"]
    assert b["mid"] == ["Two Months Ago"]
    assert b["lost"] == ["Forgotten Banger", "Loved Old"]  # silent 90+ days / loved
    assert b["fresh"] == ["Unplayed Deep Cut"]  # artist you play (120 plays), this track never
    assert b["rec"] == ["Genre Hit", "Random Unknown", "Similar Song"]  # never played, unknown artists
    assert len(owned) == len(LIB)


def test_similarity_boosts_discovery_score_only():
    t = Taste(similar=[("New Band", "Similar Song", 0.9)])
    owned, _ = assign(t, HISTORY, LIB)
    score = {c.title: c.score for c in owned}
    assert score["Similar Song"] > score["Random Unknown"] >= 0.4


def test_pool_restricts_eligibility_but_history_still_applies():
    pool = {("now band", "obsessed"), ("stranger", "random unknown")}
    owned, _ = assign(None, HISTORY, LIB, pool)
    assert by_bucket(owned) == {"obsession": ["Obsessed"], "rec": ["Random Unknown"]}


def test_played_but_unowned_tracks_become_wishes_discoveries_too():
    t = Taste(similar=[("Far Band", "Rec Not Owned", 0.9), ("Now Band", "Obsessed", 0.5)],
              artist_tracks={"Now Band": [("Now Band", "Famous Not Owned")]},
              genre_tracks=[("Far Band", "Genre Not Owned")])
    _, wanted = assign(t, HISTORY, LIB, now=NOW)
    got = {c.title: next(iter(c.sources)) for c in wanted}
    assert got["Played A Lot"] == "obsession"  # 25 plays last month, no local file: top priority
    assert got["Another Old Hit"] == "lost"
    assert got["Rec Not Owned"] == "rec" and got["Genre Not Owned"] == "rec" and got["Famous Not Owned"] == "fresh"
    assert "Obsessed" not in got  # owned and already played: not a wish
    assert wanted[0].title == "Played A Lot"


def test_no_history_at_all_everything_is_discovery():
    owned, wanted = assign(None, {}, LIB)
    assert set(by_bucket(owned)) == {"rec"} and wanted == []


def test_fetch_taste_uses_seeds_from_history():
    calls = []

    def tr(p):
        calls.append(p)
        m = p["method"]
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
    t = taste.fetch_taste("me", [("ArtA", "SongA"), ("ArtB", "SongB")], ["ArtA", "ArtB"], tr)
    assert t.loved == [("Love", "L")] and len(t.similar) == 2 and set(t.artist_tracks) == {"ArtA", "ArtB"}
    assert t.genre_tracks[0] == ("G", "GenreSong")
    assert {c["tag"] for c in calls if c["method"] == "tag.getTopTracks"} == {"industrial"}  # generic tag skipped
    assert [c["artist"] for c in calls if c["method"] == "track.getSimilar"] == ["ArtA", "ArtB"]


def test_wishlist_skips_old_rare_and_non_song_entries():
    _, wanted = assign(Taste(), HISTORY, LIB, now=NOW)
    titles = {c.title for c in wanted}
    assert titles == {"Played A Lot", "Another Old Hit"}  # not: 2014 plays, 2-play track, podcast episode
    from swimscrobble.buckets import looks_like_song
    assert looks_like_song("Enjoy the Silence") and not looks_like_song("Weekly Show | #12") and not looks_like_song("x" * 80)


def test_wishlist_is_capped_per_bucket_best_first():
    from swimscrobble import buckets
    hist = {(f"Art{i}", f"Song{i}"): Stat(10 + i, 10 + i, 0, 0, NOW - DAY) for i in range(400)}
    _, wanted = assign(None, hist, [], now=NOW)
    assert len(wanted) == round(buckets.MAX_WISHES * 0.40) and wanted[0].title == "Song399"  # obsession gets 40% of 300


def test_discoveries_are_not_crowded_out_of_the_wishlist():
    hist = {(f"Art{i}", f"Song{i}"): Stat(50 + i, 50 + i, 0, 0, NOW - DAY) for i in range(500)}  # 500 heavy plays
    t = Taste(similar=[(f"Far{i}", f"Rec{i}", 0.5) for i in range(40)],
              genre_tracks=[(f"Gen{i}", f"Hit{i}") for i in range(40)],
              artist_tracks={"Art1": [("Art1", f"Deep{i}") for i in range(30)]})
    _, wanted = assign(t, hist, [], now=NOW)
    kinds = {next(iter(c.sources)) for c in wanted}
    assert {"obsession", "rec", "fresh"} <= kinds and len(wanted) <= 300 + 30
