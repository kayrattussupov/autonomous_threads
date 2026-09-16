import random

import pytest

from src.content.topic_planner import (
    Assignment,
    PlannerInputs,
    choose_assignment,
    compute_category_weights,
    compute_sector_weights,
    describe_sectors,
    normalize_sector,
    performance_weight,
    plan_next_post,
    recency_weight,
)
from src.db.models import Post, Sector

CFG = {
    "seed_sectors": ["Производство", "HoReCa"],
    "category_mix": {"utp_cta": 0.5, "educational": 0.3, "personal": 0.1, "news": 0.1},
    "window_posts": 12,
    "max_sector_share": 0.5,
    "prior_strength": 3,
    "new_sector_prob": 0.0,
}


def _inputs(sectors, window=(), scores=None, median=1.0) -> PlannerInputs:
    return PlannerInputs(sectors=list(sectors), window=list(window), scores_by_sector=scores or {}, overall_median=median)


class _StubRng:
    """random() returns a fixed value; choices() picks the highest weight."""
    def __init__(self, random_value: float):
        self._random_value = random_value

    def random(self):
        return self._random_value

    def choices(self, population, weights, k):
        return [max(zip(population, weights), key=lambda pair: pair[1])[0]]


def test_normalize_sector_collapses_whitespace_and_lowercases():
    assert normalize_sector("  HoReCa   и  Кафе ") == "horeca и кафе"


def test_assignment_to_json_keeps_cyrillic():
    assert Assignment(sector="производство", category="utp_cta").to_json() == (
        '{"sector": "производство", "category": "utp_cta", "is_new_sector": false}'
    )


def test_recency_weight_bounds():
    assert recency_weight(1, 12) == pytest.approx(0.5 + 1 / 12)
    assert recency_weight(12, 12) == pytest.approx(1.5)
    assert recency_weight(50, 12) == pytest.approx(1.5)


def test_performance_weight_shrinks_single_outlier_toward_overall_median():
    weight = performance_weight([125.0], overall_median=1.73, prior_strength=3)
    assert weight == pytest.approx((125 + 3 * 1.73) / 4 / 1.73)
    assert weight < 125 / 1.73


def test_performance_weight_unknown_is_neutral_consistent_is_high_poor_is_floored():
    assert performance_weight([], overall_median=2.0, prior_strength=3) == pytest.approx(1.0)
    assert performance_weight([20.0] * 10, overall_median=2.0, prior_strength=3) == pytest.approx((200 + 6) / 13 / 2)
    assert performance_weight([0.0] * 30, overall_median=2.0, prior_strength=3) == pytest.approx(0.2)


def test_sector_at_or_above_max_share_gets_zero_weight():
    window = [("производство", "utp_cta")] * 6 + [("логистика", "utp_cta")] * 6
    weights = compute_sector_weights(_inputs(["производство", "логистика", "клиники"], window), CFG)
    assert weights["производство"] == 0.0
    assert weights["логистика"] == 0.0
    assert weights["клиники"] > 0


def test_sector_never_written_gets_max_recency_and_recent_one_gets_min():
    window = [("производство", "utp_cta")]
    weights = compute_sector_weights(_inputs(["производство", "клиники"], window), CFG)
    assert weights["клиники"] == pytest.approx(1.5)
    assert weights["производство"] == pytest.approx(0.5 + 1 / 12)


def test_category_under_target_gets_more_weight():
    window = [(None, "utp_cta")] * 12
    weights = compute_category_weights(CFG["category_mix"], window)
    assert weights["utp_cta"] == pytest.approx(0.5)
    assert weights["educational"] == pytest.approx(0.3 / 0.05)


def test_category_weights_on_empty_window_follow_target_mix():
    weights = compute_category_weights(CFG["category_mix"], [])
    assert weights["utp_cta"] / weights["educational"] == pytest.approx(0.5 / 0.3)


def test_choose_assignment_returns_new_sector_slot_when_probability_hits():
    cfg = {**CFG, "new_sector_prob": 0.1}
    assignment = choose_assignment(_inputs(["производство"]), cfg, _StubRng(0.05))
    assert assignment == Assignment(sector=None, category="utp_cta", is_new_sector=True)


def test_choose_assignment_falls_back_to_least_recent_sector_when_all_capped():
    cfg = {**CFG, "max_sector_share": 0.05}
    window = [("производство", "utp_cta"), ("логистика", "utp_cta")]
    assignment = choose_assignment(_inputs(["производство", "логистика"], window), cfg, _StubRng(0.99))
    assert assignment.sector == "логистика"
    assert assignment.is_new_sector is False


def test_simulation_keeps_leader_frequent_but_bounded_and_covers_all_sectors():
    sectors = ["производство"] + [f"сфера {i}" for i in range(9)]
    inputs = _inputs(sectors, scores={"производство": [125.0, 13.9, 1.8, 1.9]}, median=1.73)
    rng = random.Random(42)
    picks = []
    for _ in range(300):
        assignment = choose_assignment(inputs, CFG, rng)
        picks.append(assignment.sector)
        inputs.window = [(assignment.sector, assignment.category)] + inputs.window[: CFG["window_posts"] - 1]

    leader_share = picks.count("производство") / len(picks)
    assert 0.15 <= leader_share <= 0.51
    assert set(picks) == set(sectors)


def test_plan_next_post_syncs_seed_sectors_idempotently(db_session):
    first = plan_next_post(db_session, rng=random.Random(1), settings=CFG)
    plan_next_post(db_session, rng=random.Random(2), settings=CFG)
    db_session.commit()

    sectors = db_session.query(Sector).order_by(Sector.id).all()
    assert [s.name for s in sectors] == ["производство", "horeca"]
    assert all(s.source == "seed" for s in sectors)
    assert first.sector in {"производство", "horeca"}
    assert first.category in CFG["category_mix"]


def test_plan_next_post_does_not_reactivate_deactivated_seed_sector(db_session):
    db_session.add(Sector(name="horeca", source="seed", active=False))
    db_session.commit()

    for seed in range(20):
        assert plan_next_post(db_session, rng=random.Random(seed), settings=CFG).sector == "производство"


def test_plan_next_post_caps_sector_that_fills_half_the_window(db_session):
    for i in range(6):
        db_session.add(Post(text=f"p{i}", category="utp_cta", status="published", sector="производство", score=100))
    db_session.commit()

    assert plan_next_post(db_session, rng=random.Random(0), settings=CFG).sector == "horeca"


def test_describe_sectors_reports_stats_and_probabilities(db_session):
    db_session.add_all([
        Sector(name="производство", source="seed"),
        Sector(name="horeca", source="seed"),
        Sector(name="архив", source="llm", active=False),
        Post(text="a", category="utp_cta", status="published", sector="производство", score=10),
        Post(text="b", category="utp_cta", status="published", sector="производство", score=20),
    ])
    db_session.commit()

    rows = {row["name"]: row for row in describe_sectors(db_session, settings=CFG)}

    assert rows["производство"]["published_n"] == 2
    assert rows["производство"]["mean_score"] == pytest.approx(15.0)
    assert rows["производство"]["median_score"] == pytest.approx(15.0)
    assert rows["производство"]["last_post_at"] is not None
    assert rows["horeca"]["published_n"] == 0
    assert rows["horeca"]["mean_score"] is None
    assert rows["horeca"]["weight"] > 0
    assert rows["архив"]["weight"] is None
    assert rows["архив"]["probability"] is None
    assert sum(r["probability"] or 0 for r in rows.values()) == pytest.approx(1.0)
