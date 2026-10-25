from pathlib import Path

from swimscrobble import acquire, generator, history, matcher, poolsync, pocketcasts, taste, ytm
from swimscrobble.library import Track
from swimscrobble.planner import plan_session


def T(a, t, d=200):
    return Track(Path(f"{a}-{t}.mp3"), a, t, "", d)


def test_ytm_parse_topic_and_plain_upload():
    data = {"entries": [
        {"id": "a1", "title": "Sansara", "channel": "Closterkeller - Topic", "duration": 311},
        {"id": "b2", "title": "Nine Inch Nails - Closer", "channel": "SomeUploader", "duration": 372},
        None, {"title": "no id"},
    ]}
    e = ytm.parse(data)
    assert [(x.artist, x.title) for x in e] == [("Closterkeller", "Sansara"), ("Nine Inch Nails", "Closer")]


def test_ytm_fetch_uses_runner():
    class P: stdout = '{"entries":[{"id":"x","title":"T","channel":"A - Topic"}]}'
    seen = {}
    def run(cmd, **kw): seen["cmd"] = cmd; return P()
    assert ytm.fetch("https://u", run)[0].video_id == "x" and "--flat-playlist" in seen["cmd"]


def test_matcher_statuses():
    lib = [T("Closterkeller", "Sansara", 311), T("Depeche Mode", "Martyr", 200)]
    entries = [
        ytm.Entry("1", "Closterkeller", "Sansara (Remastered)", 311),
        ytm.Entry("2", "Depeche Mode", "Martyr [MIXED]", 200),
        ytm.Entry("3", "Totally Else", "Unknown Song", 100),
    ]
    r = matcher.match(entries, lib)
    assert [x.status for x in r] == [matcher.MATCH, matcher.MATCH, matcher.MISSING]
    assert r[0].track.title == "Sansara" and r[2].track is None


def test_norm_strips_noise():
    assert matcher.norm("Sansara (feat. X) [MIXED]") == "sansara"
    assert matcher.norm("Mój sen srebrny") == matcher.norm("Moj sen srebrny")


def test_acquire_gate_and_failures(tmp_path):
    es = [ytm.Entry("a", "A", "T", None), ytm.Entry("b", "B", "U", None)]
    calls = []
    class R:
        def __init__(s, c): s.returncode = c
    def run(cmd): calls.append(cmd); return R(1 if cmd[-1].endswith("=b") else 0)
    assert acquire.acquire(es, tmp_path / "d", run, ask=lambda _: "n") == es and not calls
    failed = acquire.acquire(es, tmp_path / "d", run, ask=lambda _: "y")
    assert failed == [es[1]] and len(calls) == 2 and "mp3" in calls[0]


def test_taste_parsing():
    def tr(p):
        m = p["method"]
        return {
            "user.getTopTracks": {"toptracks": {"track": [{"name": "S", "artist": {"name": "A"}, "playcount": "9"}]}},
            "user.getLovedTracks": {"lovedtracks": {"track": [{"name": "L", "artist": {"name": "B"}}]}},
            "track.getSimilar": {"similartracks": {"track": [{"name": "X", "artist": {"name": "C"}, "match": "0.5"}]}},
        }[m]
    import os; os.environ["LASTFM_API_KEY"] = "k"
    assert taste.top_tracks("u", transport=tr) == [("A", "S", 9)]
    assert taste.loved_tracks("u", transport=tr) == [("B", "L")]
    assert taste.similar("A", "S", transport=tr) == [("C", "X", 0.5)]


def test_generator_ranking_and_weights():
    pl = [generator.key("A", "one"), generator.key("B", "two")]
    c = generator.score_candidates(pl, [("B", "two")], [("C", "three", 50)], [("A", "one", 0.4)])
    assert c[0].title == "two" and c[0].sources == {"playlist", "loved"}
    w = generator.pool_weights(c, {generator.key("B", "two"): 3})
    assert w[generator.key("B", "two")] < c[0].score


def test_weighted_plan_prefers_heavy():
    lib = [T(f"A{i}", f"T{i}", 600) for i in range(30)]
    heavy = {(f"a{i}", f"t{i}"): 1000.0 for i in range(6)}
    wins = 0
    for seed in range(20):
        plan = plan_session(lib, minutes=60, seed=seed, weights=heavy)
        wins += sum(1 for p in plan if (p.track.artist.casefold(), p.track.title.casefold()) in heavy)
    assert wins / 20 > 4  # of 6 heavy tracks, most make a 6-track cut


def test_poolsync(tmp_path):
    db = history.connect(tmp_path / "h.db")
    cands = [generator.Candidate("A", "one", 3, {"playlist"}), generator.Candidate("B", "two", 2, {"top"}),
             generator.Candidate("C", "gone", 1, {"top"}), generator.Candidate("D", "over", 1, {"top"})]
    lib = {("a", "one"), ("b", "two"), ("d", "over")}
    rep = poolsync.sync_pool(db, cands, lib, {("d", "over"): 9}, max_size=1)
    assert rep.added == [("a", "one")] and [c.title for c in rep.missing] == ["gone"]
    assert poolsync.active_keys(db) == {("a", "one")}
    rep2 = poolsync.sync_pool(db, [cands[1]], lib, {}, max_size=5)
    assert rep2.retired == [("a", "one")] and poolsync.active_keys(db) == {("b", "two")}


def test_pocketcasts_queue_sync(tmp_path):
    db = history.connect(tmp_path / "h.db")
    eps = [{"uuid": f"u{i}", "title": f"Ep: {i}", "url": f"https://x/{i}.mp3?t=1", "podcast": "p"} for i in range(3)]
    got = []
    def dl(url, dest): got.append(url); dest.write_bytes(b"x")
    dest = tmp_path / "dev"
    r = pocketcasts.sync_queue(eps, dest, db, max_episodes=2, download=dl)
    assert sorted(p.name for p in dest.iterdir()) == ["01 - Ep 0.mp3", "02 - Ep 1.mp3"] and len(got) == 2
    # queue changes: first episode finished, order shifts
    r = pocketcasts.sync_queue(eps[1:], dest, db, max_episodes=2, download=dl)
    assert sorted(p.name for p in dest.iterdir()) == ["01 - Ep 1.mp3", "02 - Ep 2.mp3"]
    assert r["removed"] == ["01 - Ep 0.mp3"] and r["renamed"] == ["01 - Ep 1.mp3"] and len(got) == 3


def test_pocketcasts_up_next_normalises(monkeypatch, tmp_path):
    t = lambda path, body, tok: {"episodes": {"u1": {"title": "a", "url": "https://x/a.mp3"}, "u2": {"title": "b"}}}
    assert [e["uuid"] for e in pocketcasts.up_next("tok", t)] == ["u1"]


def test_pocketcasts_login_saves_token_private(monkeypatch, tmp_path):
    monkeypatch.setenv("SWIMSCROBBLE_HOME", str(tmp_path))
    tok = pocketcasts.login("e", "p", lambda path, body, t: {"token": "abc"})
    assert tok == "abc" and pocketcasts.saved_token() == "abc"
    assert (tmp_path / "pocketcasts.json").stat().st_mode & 0o777 == 0o600
