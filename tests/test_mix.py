import random
from pathlib import Path

import numpy as np

from swimscrobble import energy, history
from swimscrobble.energy import Features
from swimscrobble.generator import Candidate
from swimscrobble.library import Track
from swimscrobble.mix import Item, build_mix, interleave, order_by_flow


def T(i, artist=None, dur=200):
    return Track(Path(f"t{i}.mp3"), artist or f"Artist{i}", f"Song{i}", "", dur)


def feats(table):
    return lambda t: Features(120, -7, 150, 0.6, table.get(t.title, 0.8))


def test_slow_tracks_are_dropped():
    lib = [T(i) for i in range(30)]
    table = {f"Song{i}": 0.2 for i in range(10)}  # ballads
    plan = build_mix(lib, [], feats(table), minutes=60, seed=1, min_energy=0.45)
    assert plan and all(p.energy >= 0.45 for p in plan)
    assert not {f"Song{i}" for i in range(10)} & {p.track.title for p in plan}


def test_recommendation_share_and_labels():
    lib = [T(i) for i in range(40)]
    cands = [Candidate(f"Artist{i}", f"Song{i}", 2.0, {"top"}) for i in range(20)] + [
        Candidate(f"Artist{i}", f"Song{i}", 1.0, {"similar"}) for i in range(20, 40)
    ]
    plan = build_mix(lib, cands, feats({}), minutes=60, seed=3, rec_ratio=0.2)
    recs = [p for p in plan if p.source == "rec"]
    secs = sum(p.track.duration for p in plan)
    assert secs <= 3600 and 2 <= len(recs) <= 4  # 20% of 18 tracks ~ 3.6
    # favourites are only top-tagged ones; recs only similar-tagged
    assert all(int(p.track.title[4:]) >= 20 for p in recs)
    assert all(int(p.track.title[4:]) < 20 for p in plan if p.source == "fav")


def test_recs_spread_through_session():
    rng = random.Random(0)
    favs = [Item(T(i), "fav", 0.8, 1) for i in range(12)]
    recs = [Item(T(100 + i), "rec", 0.8, 1) for i in range(3)]
    out = interleave(favs, recs, rng)
    pos = [i for i, it in enumerate(out) if it.kind == "rec"]
    assert len(out) == 15 and len(pos) == 3 and pos[0] >= 1 and pos[-1] <= 14 and pos[1] - pos[0] >= 2


def test_no_adjacent_same_artist_when_avoidable():
    items = [Item(T(i, artist=f"A{i % 4}"), "fav", 0.8, 1) for i in range(12)]
    bad = 0
    for seed in range(30):
        out = order_by_flow(items, random.Random(seed))
        bad += sum(1 for a, b in zip(out, out[1:], strict=False) if a.track.artist == b.track.artist)
    assert bad / 30 < 1.5  # random order would give ~2.75 per run


def test_energy_flow_avoids_big_jumps():
    items = [Item(T(i), "fav", 0.45 if i % 2 else 0.95, 1) for i in range(20)]
    jumps = []
    for seed in range(30):
        out = order_by_flow(items, random.Random(seed))
        jumps.append(sum(abs(a.energy - b.energy) > 0.25 for a, b in zip(out, out[1:], strict=False)))
    assert sum(jumps) / 30 < 6  # shuffling alternating 0.45/0.95 randomly would give ~9.5


def test_high_energy_favoured_in_selection():
    lib = [T(i, dur=600) for i in range(20)]
    table = {f"Song{i}": (0.95 if i < 6 else 0.5) for i in range(20)}
    hits = 0
    for seed in range(30):
        plan = build_mix(lib, [], feats(table), minutes=60, seed=seed)
        hits += sum(1 for p in plan if int(p.track.title[4:]) < 6)
    assert hits / 30 > 3.0  # 6 of 20 picked; unweighted would average 1.8 of the 6 energetic


def test_seed_reproducible_and_recent_excluded():
    lib = [T(i) for i in range(30)]
    a = build_mix(lib, [], feats({}), seed=5)
    b = build_mix(lib, [], feats({}), seed=5)
    assert [p.track.title for p in a] == [p.track.title for p in b]
    recent = {(f"artist{i}", f"song{i}") for i in range(10)}
    c = build_mix(lib, [], feats({}), seed=5, recent=recent)
    assert not {p.track.title.casefold() for p in c} & {k[1] for k in recent}


def test_slack_filled_when_too_few_recs():
    lib = [T(i) for i in range(30)]
    cands = [Candidate("Artist0", "Song0", 1.0, {"similar"})]
    plan = build_mix(lib, cands, feats({}), minutes=60, seed=1, rec_ratio=0.5)
    assert sum(p.track.duration for p in plan) >= 3400  # not stuck at 50% budget


# ---- audio energy ----
def click_train(bpm, seconds=60, amp=0.8):
    x = np.zeros(int(energy.SR * seconds), dtype=np.float32)
    step = int(energy.SR * 60 / bpm)
    for i in range(0, len(x) - 200, step):
        x[i : i + 200] = amp * np.random.default_rng(i).uniform(-1, 1, 200)
    return x


def test_tempo_estimate_and_pulse_on_click_train():
    env = energy.onset_envelope(click_train(120))
    bpm, pulse = energy.estimate_bpm(env)
    assert abs(bpm - 120) < 3 and pulse > 0.5


def test_quiet_sparse_audio_scores_lower_than_loud_driving():
    rng = np.random.default_rng(0)
    t = np.arange(energy.SR * 60) / energy.SR
    pad = (0.02 * np.sin(2 * np.pi * 220 * t) + 0.002 * rng.standard_normal(len(t))).astype(np.float32)
    driving = np.clip(click_train(130, amp=0.9) + 0.2 * rng.standard_normal(len(t)).astype(np.float32), -1, 1)
    assert energy.analyse_samples(pad).energy < 0.35 < 0.5 < energy.analyse_samples(driving).energy


def test_tag_bpm_overrides_estimate():
    f = energy.analyse_samples(click_train(120), tag_bpm=90.0)
    assert f.bpm == 90.0


def test_feature_cache_hits_until_file_changes(tmp_path):
    db = history.connect(tmp_path / "h.db")
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    track = Track(f, "A", "T", "", 100)
    calls = []

    def fake(path, dur, bpm):
        calls.append(1)
        return Features(100, -8, 150, 0.5, 0.6)

    energy.cached_features(db, track, fake)
    energy.cached_features(db, track, fake)
    assert len(calls) == 1
    import os

    os.utime(f, (1, 1))
    energy.cached_features(db, track, fake)
    assert len(calls) == 2


def test_buffer_filled_from_recent_when_pool_small():
    lib = [T(i) for i in range(20)]  # 20 x 200s
    recent = {(f"artist{i}", f"song{i}") for i in range(10)}
    plan = build_mix(lib, [], feats({}), minutes=120, seed=1, recent=recent)
    assert sum(p.track.duration for p in plan) == 4000  # fresh 2000s + recent topped up to the whole library
    fresh = [p for p in plan if (p.track.artist.casefold(), p.track.title.casefold()) not in recent]
    assert len(fresh) == 10  # every fresh track is used before any recent one repeats
