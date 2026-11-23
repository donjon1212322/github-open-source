"""Owner 23.11: interest is the *rate*, not the star total.

The old score was additive with caps: ``min(30, 6.2*log10(stars))`` for
quality and ``min(20, ...)`` for momentum. On the live queue that collapsed
into 80.28..87.40 across 27 candidates - every project looked the same, and a
two-day-old project with 600 fresh stars could not outrank a two-year-old
project with 20 000 old ones.

The replacement is a ten-signal model (see ``rank.WEIGHTS``) with pool
percentiles, so the score uses 0..100 and "best today" is decided by
comparison. Growth is the largest weight (26) and it comes from our own
daily snapshots whenever we have them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ghos_v6 import rank
from ghos_v6.state import StateStore


def _repo(days_old: float, stars: int, **overrides):
    now = datetime.now(timezone.utc)
    payload = {
        "full_name": f"owner/repo-{days_old}-{stars}",
        "name": "repo",
        "description": "A desktop app for photos, self-hosted",
        "topics": ["desktop", "photos", "self-hosted"],
        "language": "Rust",
        "stars": stars,
        "forks": max(1, stars // 25),
        "contributors_count": 12,
        "open_issues": 3,
        "readme_len": 4000,
        "homepage": "https://example.com",
        "media_urls": ["https://example.com/a.png"],
        "license": {"spdx_id": "MIT"},
        "created_at": (now - timedelta(days=days_old)).isoformat(),
        "pushed_at": (now - timedelta(days=1)).isoformat(),
        "sources": ["github_search"],
    }
    payload.update(overrides)
    return payload


# --- the owner's rule --------------------------------------------------------


def test_a_fresh_burst_outranks_a_big_old_repository():
    young = _repo(2, 600)
    old = _repo(1000, 20000)
    ranked = rank.rank_candidates([old, young])
    assert ranked[0]["full_name"] == young["full_name"]
    assert ranked[0]["score"] > ranked[1]["score"]


def test_absolute_popularity_cannot_dominate_the_score():
    """Popularity is 5 of 100 points: stars alone must not win the day."""
    assert rank.WEIGHTS["popularity"] == 5.0
    assert rank.WEIGHTS["growth"] == 24.0
    popular = _repo(3000, 50000, readme_len=200, topics=[], homepage="", media_urls=[])
    steady = _repo(30, 800, forks=120, contributors_count=40)
    ranked = rank.rank_candidates([popular, steady])
    assert ranked[0]["full_name"] == steady["full_name"]


def test_pool_percentiles_spread_the_signals():
    """Signals that actually differ must span the whole 0..1 band.

    Dimensions where the whole pool is identical stay at 0.5 by design (no
    comparison, no claim), so the score is centred on 50 with the real
    differences on top of it - unlike the old formula, where a 27-candidate
    pool spanned 7 points.
    """
    pool = [
        _repo(2, 900, readme_len=9000, homepage="https://a.dev", media_urls=["https://a.dev/x.png"]),
        _repo(180, 4000, readme_len=4000),
        _repo(900, 9000, readme_len=200, topics=[], homepage="", media_urls=[]),
        _repo(2000, 15000, readme_len=100, topics=[], homepage="", media_urls=[], contributors_count=1),
    ]
    ranked = rank.rank_candidates(pool)
    growth = [item["score_parts"]["growth"] for item in ranked]
    assert max(growth) == 1.0 and min(growth) == 0.0
    scores = [item["score"] for item in ranked]
    assert max(scores) - min(scores) > 15.0
    assert ranked[0]["score"] > 55.0


def test_ties_share_the_middle_percentile():
    now = datetime.now(timezone.utc)
    stamp = (now - timedelta(days=1)).isoformat()
    created = (now - timedelta(days=100)).isoformat()
    first = _repo(100, 1000, full_name="a/same", pushed_at=stamp, created_at=created)
    second = _repo(100, 1000, full_name="b/same", pushed_at=stamp, created_at=created)
    ranked = rank.rank_candidates([first, second])
    assert ranked[0]["score"] == ranked[1]["score"]
    assert ranked[0]["score_parts"]["growth"] == 0.5


def test_a_single_candidate_makes_no_claim():
    ranked = rank.rank_candidates([_repo(10, 500)])
    assert ranked[0]["score"] == 50.0


# --- real growth from our own snapshots -------------------------------------


def test_measured_growth_beats_guessing_from_age():
    now = datetime.now(timezone.utc)
    three_days_ago = (now - timedelta(days=3)).date().isoformat()
    today = now.date().isoformat()
    candidate = _repo(700, 800, star_history=[[three_days_ago, 200], [today, 800]])
    parts = rank.score_components(candidate)
    assert parts["growth_measured"] == 1
    # 600 stars over ~3 days, not 800/700 from the repo age.
    assert 150 <= parts["stars_per_day"] <= 250


def test_growth_falls_back_to_velocity_without_history():
    parts = rank.score_components(_repo(4, 400))
    assert parts["growth_measured"] == 0
    assert 90 <= parts["stars_per_day"] <= 110


def test_state_records_one_snapshot_per_day(tmp_path):
    store = StateStore(tmp_path)
    store.data = {}
    repo = _repo(10, 500)
    store.mark_seen(repo, "queued")
    history = store.data["seen_repos"][repo["full_name"]]["star_history"]
    assert len(history) == 1 and history[0][1] == 500

    # Same day, new number: updated in place, not appended.
    store.mark_seen(_repo(10, 540, full_name=repo["full_name"]), "queued")
    history = store.data["seen_repos"][repo["full_name"]]["star_history"]
    assert len(history) == 1 and history[0][1] == 540


def test_snapshot_history_is_bounded(tmp_path):
    store = StateStore(tmp_path)
    old_history = [[f"2026-10-{day:02d}", 100 + day] for day in range(1, 21)]
    store.data = {
        "seen_repos": {"owner/repo": {"full_name": "owner/repo", "star_history": old_history}}
    }
    store.mark_seen(_repo(10, 900, full_name="owner/repo"), "queued")
    history = store.data["seen_repos"]["owner/repo"]["star_history"]
    assert len(history) <= store.STAR_HISTORY_POINTS
    assert history[-1][1] == 900


def test_rank_candidates_takes_history_from_state():
    now = datetime.now(timezone.utc)
    three_days_ago = (now - timedelta(days=3)).date().isoformat()
    today = now.date().isoformat()
    candidate = _repo(700, 800, full_name="owner/snapshotted")
    seen = {"owner/snapshotted": {"star_history": [[three_days_ago, 200], [today, 800]]}}
    ranked = rank.rank_candidates([candidate, _repo(900, 5000)], seen=seen)
    assert ranked[0]["score_parts"]["growth_measured"] == 1


# --- hygiene inside the model ------------------------------------------------


def test_repo_farm_loses_reliability_points():
    farm = _repo(10, 900, owner_repo_count=12)
    clean = _repo(10, 900, full_name="clean/repo")
    assert rank.score_components(farm)["reliability"] < rank.score_components(clean)["reliability"]


def test_zero_star_repository_scores_at_the_bottom():
    ghost = _repo(30, 0, forks=0, contributors_count=0, open_issues=0)
    assert rank.score_components(ghost)["reliability"] == 0.0


def test_score_is_bounded_and_auditable():
    ranked = rank.rank_candidates([_repo(5, 700), _repo(400, 9000)])
    for item in ranked:
        assert 0.0 <= item["score"] <= 100.0
        assert set(item["score_parts"]) >= set(rank.WEIGHTS)
        assert 0.0 <= item["score_parts"]["growth"] <= 1.0


def test_weights_add_up_to_one_hundred():
    assert round(sum(rank.WEIGHTS.values()), 6) == 100.0


def test_collect_passes_star_snapshots_into_the_ranking():
    """Contract: the snapshot-based rate is worthless if collect ignores it."""
    from pathlib import Path

    body = Path("src/ghos_v6/pipeline.py").read_text(encoding="utf-8")
    assert 'rank_candidates(candidates, seen=store.data.get("seen_repos"))' in body
    assert 'rank_candidates(passing, seen=store.data.get("seen_repos"))' in body


# --- 23.11, second pass: acceleration and forks per day ----------------------


def _history(*pairs):
    return [[day, stars] for day, stars in pairs]


def test_acceleration_rewards_a_take_off():
    now = datetime.now(timezone.utc)
    day = lambda n: (now - timedelta(days=n)).date().isoformat()  # noqa: E731
    launching = _repo(30, 700, star_history=_history((day(6), 100), (day(3), 200), (day(0), 700)))
    plateau = _repo(30, 700, full_name="p/plateau",
                    star_history=_history((day(6), 400), (day(3), 650), (day(0), 700)))
    parts_launch = rank.score_components(launching)
    parts_plateau = rank.score_components(plateau)
    assert parts_launch["acceleration_known"] == 1
    assert parts_launch["acceleration_ratio"] > 3.0
    assert parts_launch["acceleration"] > parts_plateau["acceleration"]


def test_acceleration_needs_real_history_and_is_neutral_when_unknown():
    guessless = rank.score_components(_repo(10, 800))
    assert guessless["acceleration_known"] == 0
    ranked = rank.rank_candidates([_repo(10, 900), _repo(20, 400)])
    for item in ranked:
        assert item["score_parts"]["acceleration"] == 0.5


def test_forks_per_day_separates_installed_from_starred():
    fast = _repo(10, 2000, forks=400)
    slow = _repo(2000, 2000, forks=400, full_name="s/slow")
    assert rank.score_components(fast)["forks_per_day"] > rank.score_components(slow)["forks_per_day"]
    ranked = rank.rank_candidates([fast, slow])
    assert ranked[0]["full_name"] == fast["full_name"]


def test_momentum_signals_outweigh_the_star_total():
    momentum = rank.WEIGHTS["growth"] + rank.WEIGHTS["acceleration"] + rank.WEIGHTS["forks_rate"] + rank.WEIGHTS["burst"]
    assert momentum == 50.0
    assert rank.WEIGHTS["popularity"] == 5.0
    assert momentum > 5 * rank.WEIGHTS["popularity"]


def test_state_snapshots_carry_forks_too(tmp_path):
    store = StateStore(tmp_path)
    store.data = {}
    repo = _repo(10, 500)
    repo["forks"] = 120
    store.mark_seen(repo, "queued")
    history = store.data["seen_repos"][repo["full_name"]]["star_history"]
    assert history[0][2] == 120


def test_acceleration_handles_a_flat_past_without_dividing_by_zero():
    now = datetime.now(timezone.utc)
    day = lambda n: (now - timedelta(days=n)).date().isoformat()  # noqa: E731
    latched = _repo(20, 400, star_history=_history((day(6), 100), (day(3), 100), (day(0), 400)))
    parts = rank.score_components(latched)
    assert parts["acceleration_known"] == 1
    assert parts["acceleration_ratio"] == 2.0


def test_every_new_signal_keeps_its_raw_value_in_score_parts():
    """Owner 23.11: a score must be explainable after the fact."""
    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    takeoff = _repo(8, 11000, forks=2200, star_history=[
        ["2026-11-16", 1000, 100], ["2026-11-18", 2000, 300],
        ["2026-11-20", 5000, 900], ["2026-11-22", 11000, 2200],
    ])
    ranked = rank.rank_candidates([takeoff], now=now)
    parts = ranked[0]["score_parts"]
    assert parts["acceleration_known"] == 1
    assert parts["acceleration_ratio"] > 2.0
    assert parts["forks_measured"] == 1
    assert parts["forks_per_day"] > 0


def test_an_unmeasured_signal_says_so_instead_of_pretending():
    """One snapshot is not a trend: the flag must read 0, not 1."""
    fresh = _repo(1, 200, star_history=[["2026-11-23", 200, 4]], forks=4)
    parts = rank.rank_candidates([fresh])[0]["score_parts"]
    assert parts["acceleration_known"] == 0
    assert parts["acceleration_ratio"] == 0.0
    assert parts["acceleration"] == 0.5  # neutral percentile, not a punishment
