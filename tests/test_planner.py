from datetime import datetime, timedelta
from pathlib import Path

from swimscrobble.library import Track
from swimscrobble.planner import plan_session, total_seconds
from swimscrobble.scrobble import build_scrobbles


def make(n, dur=240):
    return [Track(Path(f"t{i}.mp3"), f"A{i % 3}", f"T{i}", "", dur) for i in range(n)]


def test_fits_budget():
    plan = plan_session(make(40), minutes=60, seed=1)
    assert total_seconds(plan) <= 3600
    assert len(plan) == 15


def test_seed_reproducible():
    a = [p.track.title for p in plan_session(make(40), seed=7)]
    b = [p.track.title for p in plan_session(make(40), seed=7)]
    assert a == b


def test_staged_names_sort_in_play_order():
    plan = plan_session(make(40), seed=3)
    names = [p.staged_name for p in plan]
    assert names == sorted(names)


def test_scrobble_timestamps_sequential():
    plan = plan_session(make(10), minutes=60, seed=1)
    s = build_scrobbles(plan, datetime(2026, 10, 25, 7, 0))
    assert s[1].timestamp - s[0].timestamp == timedelta(seconds=240)
