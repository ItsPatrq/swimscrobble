import random
from collections import Counter
from pathlib import Path

import numpy as np

from swimscrobble import energy, history
from swimscrobble.energy import Features
from swimscrobble.generator import Candidate
from swimscrobble.library import Track
from swimscrobble.mix import DEFAULT_QUOTAS, Item, build_mix, order_by_flow


def T(i, artist=None, dur=200):
    return Track(Path(f"t{i}.mp3"), artist or f"Artist{i}", f"Song{i}", "", dur)


def feats(table):
    return lambda t: Features(120, -7, 150, 0.6, table.get(t.title, 0.8))


def cand(i, bucket, score=1.0):
    return Candidate(f"Artist{i}", f"Song{i}", score, {bucket})


def pool(n_per=12):
    """5 buckets x n_per tracks, every track its own artist."""
    lib, cands = [], []
    for b_i, b in enumerate(DEFAULT_QUOTAS):
        for j in range(n_per):
            i = b_i * 100 + j
            lib.append(T(i))
            cands.append(cand(i, b))
    return lib, cands


def bucket_secs(plan):
    out = Counter()
    for p in plan:
        out[p.source] += p.track.duration
    return out


def test_slow_tracks_are_dropped():
    lib = [T(i) for i in range(30)]
    table = {f"Song{i}": 0.2 for i in range(10)}  # ballads
    plan = build_mix(lib, [], feats(table), minutes=60, seed=1, min_energy=0.45)
    assert plan and all(p.energy >= 0.45 for p in plan)
    assert not {f"Song{i}" for i in range(10)} & {p.track.title for p in plan}


def test_session_is_about_two_hours_and_uses_every_bucket():
    lib, cands = pool()
    plan = build_mix(lib, cands, feats({}), minutes=120, seed=1)
    assert 7000 <= sum(p.track.duration for p in plan) <= 7200
    secs = bucket_secs(plan)
    assert set(secs) == set(DEFAULT_QUOTAS)
    total = sum(secs.values())
    for b, w in DEFAULT_QUOTAS.items():  # shares follow the quotas, within one or two tracks
        assert abs(secs[b] / total - w) < 0.08, (b, secs[b] / total)


def test_quotas_are_configurable():
    lib, cands = pool(40)
    plan = build_mix(lib, cands, feats({}), minutes=120, seed=1, quotas={"lost": 1.0})
    assert bucket_secs(plan)["lost"] >= 0.9 * sum(p.track.duration for p in plan)


def test_empty_bucket_hands_its_time_to_the_others():
    lib, cands = pool()
    cands = [c for c in cands if "lost" not in c.sources]  # no lost gems available
    plan = build_mix(lib, cands, feats({}), minutes=120, seed=1)
    assert "lost" not in bucket_secs(plan) and sum(p.track.duration for p in plan) >= 7000


def test_max_per_artist_cap_then_relaxed_only_when_needed():
    lib = [Track(Path(f"a{i}.mp3"), "Same Artist", f"S{i}", "", 200) for i in range(20)]
    lib += [T(100 + i) for i in range(30)]
    cands = [Candidate(t.artist, t.title, 1.0, {"obsession"}) for t in lib]
    plan = build_mix(lib, cands, feats({}), minutes=60, seed=1, max_per_artist=3)
    assert Counter(p.track.artist for p in plan)["Same Artist"] == 3  # 30 other artists fill the rest
    only = [x for x in lib if x.artist == "Same Artist"]
    plan = build_mix(only, [Candidate(t.artist, t.title, 1.0, {"obsession"}) for t in only], feats({}), minutes=60, seed=1)
    assert sum(p.track.duration for p in plan) >= 3400  # cap relaxed when nothing else is left


def test_different_seeds_give_different_playlists_and_same_seed_repeats():
    lib, cands = pool(20)
    runs = [tuple(p.track.title for p in build_mix(lib, cands, feats({}), minutes=120, seed=s)) for s in range(6)]
    assert len(set(runs)) == 6
    assert runs[0] == tuple(p.track.title for p in build_mix(lib, cands, feats({}), minutes=120, seed=0))
    sets = [set(r) for r in runs]
    overlap = len(sets[0] & sets[1]) / len(sets[0])
    assert overlap < 0.85  # not the same songs reshuffled


def test_no_long_runs_of_one_bucket():
    lib, cands = pool(20)
    worst = 0
    for seed in range(15):
        plan = build_mix(lib, cands, feats({}), minutes=120, seed=seed)
        run = best = 1
        for a, b in zip(plan, plan[1:], strict=False):
            run = run + 1 if a.source == b.source else 1
            best = max(best, run)
        worst = max(worst, best)
    assert worst <= 5


def test_no_adjacent_same_artist_when_avoidable():
    items = [Item(T(i, artist=f"A{i % 4}"), "obsession", 0.8, 1) for i in range(12)]
    bad = 0
    for seed in range(30):
        out = order_by_flow(items, random.Random(seed))
        bad += sum(1 for a, b in zip(out, out[1:], strict=False) if a.track.artist == b.track.artist)
    assert bad / 30 < 1.5  # random order would give ~2.75 per run


def test_energy_flow_avoids_big_jumps():
    items = [Item(T(i), "obsession", 0.45 if i % 2 else 0.95, 1) for i in range(20)]
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


def test_recent_tracks_excluded_unless_needed_for_the_buffer():
    lib = [T(i) for i in range(30)]
    recent = {(f"artist{i}", f"song{i}") for i in range(10)}
    c = build_mix(lib, [], feats({}), minutes=60, seed=5, recent=recent)
    assert not {p.track.title.casefold() for p in c} & {k[1] for k in recent}
    small = [T(i) for i in range(20)]  # 20 x 200s = 4000s, 10 of them recent
    plan = build_mix(small, [], feats({}), minutes=120, seed=1, recent={(f"artist{i}", f"song{i}") for i in range(10)})
    assert sum(p.track.duration for p in plan) == 4000
    fresh = [p for p in plan if int(p.track.title[4:]) >= 10]
    assert len(fresh) == 10


def test_with_taste_pool_unlisted_tracks_are_not_eligible():
    lib = [T(i) for i in range(30)]
    cands = [cand(i, "obsession") for i in range(10)]
    analysed = []
    plan = build_mix(lib, cands, lambda t: (analysed.append(t) or Features(120, -7, 150, 0.6, 0.8)), minutes=600, seed=1)
    assert {p.track.title for p in plan} == {f"Song{i}" for i in range(10)}
    assert len(analysed) == 10  # no audio analysis wasted on tracks that can never be picked


def test_legacy_source_names_map_to_buckets():
    lib = [T(0), T(1)]
    cands = [Candidate("Artist0", "Song0", 1, {"playlist", "loved"}), Candidate("Artist1", "Song1", 1, {"similar"})]
    plan = build_mix(lib, cands, feats({}), minutes=600, seed=1)
    assert {p.track.title: p.source for p in plan} == {"Song0": "obsession", "Song1": "rec"}


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
