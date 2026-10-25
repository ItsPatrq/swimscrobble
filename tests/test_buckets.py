from pathlib import Path

from swimscrobble import taste
from swimscrobble.affinity import Aff, ArtistAff
from swimscrobble.buckets import assign, classify, looks_like_song, relevance
from swimscrobble.library import Track
from swimscrobble.taste import Taste

DAY = 86400
NOW = 4000 * DAY


def T(artist, title, dur=200):
    return Track(Path(f"{artist}-{title}.mp3"), artist, title, "", dur)


def aff(now=0.0, long=0.0, total=0, days_ago=None, weeks=0):
    return Aff(now, long, total, int(NOW - days_ago * DAY) if days_ago is not None else 0, weeks)


def art(now=0.0, total=0, days_ago=None):
    return ArtistAff(now, int(NOW - days_ago * DAY) if days_ago is not None else 0, total)


LIB = [
    T("Hot Band", "Played Lately"), T("Hot Band", "Never Played Deep Cut"), T("Warm Band", "Last Month"),
    T("Gem Band", "Forgotten Banger"), T("Loved Band", "Loved Old"), T("Known Band", "Unplayed By Known Artist"),
    T("New Band", "Similar Song"), T("Stranger", "Random Unknown"),
]
# Last.fm spells things its own way (accents, remaster tags) - linking must cope
TRACKS = {
    ("Hot Band", "Played Lately"): aff(now=9, long=12, total=40, days_ago=2, weeks=6),
    ("Warm Band", "Last Month (Remastered)"): aff(now=0.9, long=2, total=4, days_ago=40, weeks=3),
    ("Gem Band", "Forgotten Banger"): aff(now=0.0, long=1.5, total=80, days_ago=200, weeks=30),
    ("Hot Band", "Another Old Hit"): aff(now=0.0, long=3, total=30, days_ago=300, weeks=10),
    ("Dead Band", "Played In 2014"): aff(total=50, days_ago=3000, weeks=20),
    ("CD-Action Air", "Film Cyberpunk 2077! Kto zagra Keanu Reevesa? | #179"): aff(now=8, total=9, days_ago=1),
    ("Rare Band", "Played Twice"): aff(now=0.5, total=2, days_ago=50),
    ("Unowned Band", "Played A Lot"): aff(now=20, long=25, total=25, days_ago=1),
}
ARTISTS = {
    "Hot Band": art(now=40, total=900, days_ago=1), "Warm Band": art(now=1.0, total=20, days_ago=40),
    "Gem Band": art(now=0.0, total=100, days_ago=200), "Known Band": art(now=0.4, total=15, days_ago=100),
    "Loved Band": art(now=0.0, total=2, days_ago=900),
}


def by_bucket(owned):
    out = {}
    for c in owned:
        out.setdefault(next(iter(c.sources)), []).append(c.title)
    return {k: sorted(v) for k, v in out.items()}


def test_each_track_is_judged_against_history():
    t = Taste(loved=[("Loved Band", "Loved Old")], similar=[("New Band", "Similar Song", 0.7)])
    owned, _ = assign(t, TRACKS, ARTISTS, LIB, now=NOW)
    b = by_bucket(owned)
    assert b["obsession"] == ["Never Played Deep Cut", "Played Lately"]  # the ARTIST is hot: unplayed tracks too
    assert b["mid"] == ["Last Month"]
    assert b["lost"] == ["Forgotten Banger", "Loved Old"]  # silent 90+ days / loved
    assert b["fresh"] == ["Unplayed By Known Artist"]
    assert b["rec"] == ["Random Unknown", "Similar Song"]
    assert len(owned) == len(LIB)


def test_classify_rules():
    assert classify(None, art(now=40, total=900, days_ago=1), False, NOW) == "obsession"
    assert classify(aff(now=2, total=6, days_ago=3), art(now=0.2, total=50, days_ago=3), False, NOW) == "mid"
    assert classify(aff(total=9, days_ago=500), art(total=9, days_ago=500), False, NOW) == "lost"
    assert classify(None, art(total=9, days_ago=500), True, NOW) == "lost"  # loved counts as a past favourite
    assert classify(None, art(total=9, days_ago=30), False, NOW) == "fresh"
    assert classify(None, art(total=9, days_ago=800), False, NOW) == "rec"  # known long ago, not lately
    assert classify(None, None, False, NOW) == "rec"


def test_relevance_rises_with_track_and_artist_activity():
    quiet = relevance(None, None)
    assert quiet == 0
    assert relevance(aff(now=3, long=5), None) > relevance(aff(now=0.5, long=1), None) > quiet
    assert relevance(None, art(now=30)) > relevance(None, art(now=3)) > 0


def test_similarity_boosts_discovery_score_only():
    t = Taste(similar=[("New Band", "Similar Song", 0.9)])
    owned, _ = assign(t, TRACKS, ARTISTS, LIB, now=NOW)
    score = {c.title: c.score for c in owned}
    assert score["Similar Song"] > score["Random Unknown"] >= 0.35


def test_pool_restricts_eligibility_but_history_still_applies():
    pool = {("hot band", "played lately"), ("stranger", "random unknown")}
    owned, _ = assign(None, TRACKS, ARTISTS, LIB, pool, now=NOW)
    assert by_bucket(owned) == {"obsession": ["Played Lately"], "rec": ["Random Unknown"]}


def test_played_but_unowned_tracks_become_wishes_discoveries_too():
    t = Taste(similar=[("Far Band", "Rec Not Owned", 0.9), ("Hot Band", "Played Lately", 0.5)],
              artist_tracks={"Hot Band": [("Hot Band", "Famous Not Owned")]},
              genre_tracks=[("Far Band", "Genre Not Owned")])
    _, wanted = assign(t, TRACKS, ARTISTS, LIB, now=NOW)
    got = {c.title: next(iter(c.sources)) for c in wanted}
    assert got["Played A Lot"] in ("obsession", "mid")  # 20 recent plays, no local file: top priority
    assert got["Another Old Hit"] == "obsession"  # old, but its artist is hot now
    assert got["Rec Not Owned"] == "rec" and got["Genre Not Owned"] == "rec" and got["Famous Not Owned"] == "fresh"
    assert "Played Lately" not in got  # owned and already played: not a wish
    assert wanted[0].title in ("Played A Lot", "Another Old Hit")


def test_no_history_at_all_everything_is_discovery():
    owned, wanted = assign(None, {}, {}, LIB, now=NOW)
    assert set(by_bucket(owned)) == {"rec"} and wanted == []


def test_wishlist_skips_old_rare_and_non_song_entries():
    _, wanted = assign(Taste(), TRACKS, ARTISTS, LIB, now=NOW)
    titles = {c.title for c in wanted}
    assert titles == {"Played A Lot", "Another Old Hit"}  # not: 2014 plays, 2-play track, podcast episode
    assert looks_like_song("Enjoy the Silence") and not looks_like_song("Weekly Show | #12") and not looks_like_song("x" * 80)


def test_wishlist_is_capped_per_bucket_best_first():
    from swimscrobble import buckets
    tr = {(f"Art{i}", f"Song{i}"): aff(now=10 + i, long=20 + i, total=50 + i, days_ago=1) for i in range(400)}
    ar = {f"Art{i}": art(now=40, total=500, days_ago=1) for i in range(400)}
    _, wanted = assign(None, tr, ar, [], now=NOW)
    assert len(wanted) == round(buckets.MAX_WISHES * 0.40) and wanted[0].title == "Song399"  # obsession gets 40% of 300


def test_discoveries_are_not_crowded_out_of_the_wishlist():
    tr = {(f"Art{i}", f"Song{i}"): aff(now=10 + i, long=20, total=50 + i, days_ago=1) for i in range(500)}
    ar = {f"Art{i}": art(now=40, total=500, days_ago=1) for i in range(500)}
    t = Taste(similar=[(f"Far{i}", f"Rec{i}", 0.5) for i in range(40)],
              genre_tracks=[(f"Gen{i}", f"Hit{i}") for i in range(40)],
              artist_tracks={"Art1": [("Art1", f"Deep{i}") for i in range(30)]})
    _, wanted = assign(t, tr, ar, [], now=NOW)
    kinds = {next(iter(c.sources)) for c in wanted}
    assert {"obsession", "rec", "fresh"} <= kinds and len(wanted) <= 300 + 30


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
