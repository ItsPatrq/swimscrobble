import pytest

from swimscrobble import history, lfhistory

NOW = 100 * 86400
DAY = 86400


def page(tracks, page_no, pages, nowplaying=False):
    items = [{"artist": {"#text": a}, "name": t, "date": {"uts": str(ts)}} for a, t, ts in tracks]
    if nowplaying:
        items.insert(0, {"artist": {"#text": "Live"}, "name": "Now", "@attr": {"nowplaying": "true"}})
    return {"recenttracks": {"track": items, "@attr": {"page": str(page_no), "totalPages": str(pages)}}}


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("LASTFM_API_KEY", "k")


def test_pages_are_followed_and_nowplaying_skipped(tmp_path):
    db = history.connect(tmp_path / "h.db")
    pages = {
        1: page([("A", "x", 300), ("A", "x", 200)], 1, 2, nowplaying=True),
        2: page([("B", "y", 100)], 2, 2),
    }
    seen = []
    n = lfhistory.sync_history(db, "me", lambda p: (seen.append(p) or pages[p["page"]]), sleep=lambda s: None)
    assert n == 3 and [p["page"] for p in seen] == [1, 2] and "from" not in seen[0]
    assert db.execute("SELECT COUNT(*) c FROM scrobbles").fetchone()["c"] == 3


def test_incremental_sync_only_asks_for_newer_and_is_idempotent(tmp_path):
    db = history.connect(tmp_path / "h.db")
    lfhistory.sync_history(db, "me", lambda p: page([("A", "x", 300)], 1, 1), sleep=lambda s: None)
    seen = []
    n = lfhistory.sync_history(db, "me", lambda p: (seen.append(p) or page([("A", "x", 300), ("A", "z", 400)], 1, 1)),
                               sleep=lambda s: None)
    assert seen[0]["from"] == 301 and n == 1  # the overlapping row is not double counted
    assert db.execute("SELECT COUNT(*) c FROM scrobbles").fetchone()["c"] == 2


def test_single_track_response_not_wrapped_in_list(tmp_path):
    db = history.connect(tmp_path / "h.db")
    one = {"recenttracks": {"track": {"artist": {"#text": "A"}, "name": "x", "date": {"uts": "5"}},
                            "@attr": {"totalPages": "1"}}}
    assert lfhistory.sync_history(db, "me", lambda p: one, sleep=lambda s: None) == 1


def test_stats_windows(tmp_path):
    db = history.connect(tmp_path / "h.db")
    rows = [(NOW - 5 * DAY, "A", "x"), (NOW - 10 * DAY, "A", "x"), (NOW - 40 * DAY, "A", "x"),
            (NOW - 70 * DAY, "A", "x"), (NOW - 200 * DAY, "A", "x"), (NOW - 200 * DAY, "B", "old")]
    db.executemany("INSERT INTO scrobbles VALUES(?,?,?)", rows)
    st = lfhistory.stats(db, NOW)
    assert st[("A", "x")] == lfhistory.Stat(total=5, p30=2, p60=1, p90=1, last=NOW - 5 * DAY)
    assert st[("B", "old")].total == 1 and st[("B", "old")].p30 == 0
