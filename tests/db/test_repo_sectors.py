from datetime import datetime, timezone

from src.db.models import AgentRun, Post, Sector
from src.db.repo import (
    get_active_sector_names,
    get_last_post_at_by_sector,
    get_or_create_sector,
    get_planner_window,
    get_published_scores,
    get_top_performers,
    list_posts,
    list_sectors,
    median_post_score,
    sector_exists,
    set_agent_run_output_ref,
)


def test_get_or_create_sector_returns_existing_row_without_duplicating(db_session):
    first, created_first = get_or_create_sector(db_session, "производство", source="seed")
    second, created_second = get_or_create_sector(db_session, "производство", source="llm")
    db_session.commit()

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert second.source == "seed"
    assert db_session.query(Sector).count() == 1


def test_get_active_sector_names_excludes_inactive_and_keeps_insertion_order(db_session):
    db_session.add_all([
        Sector(name="производство", source="seed"),
        Sector(name="архив", source="llm", active=False),
        Sector(name="horeca", source="seed"),
    ])
    db_session.commit()

    assert get_active_sector_names(db_session) == ["производство", "horeca"]
    assert [s.name for s in list_sectors(db_session)] == ["производство", "архив", "horeca"]


def test_get_planner_window_filters_statuses_orders_newest_first_and_limits(db_session):
    db_session.add_all([
        Post(text="old", category="utp_cta", status="published", sector="a",
             created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        Post(text="draft", category="news", status="draft", sector="b",
             created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
        Post(text="review", category="personal", status="needs_review", sector="c",
             created_at=datetime(2026, 1, 3, tzinfo=timezone.utc)),
        Post(text="new", category="educational", status="scheduled", sector=None,
             created_at=datetime(2026, 1, 4, tzinfo=timezone.utc)),
    ])
    db_session.commit()

    assert get_planner_window(db_session, n=2) == [(None, "educational"), ("c", "personal")]
    assert get_planner_window(db_session, n=10) == [(None, "educational"), ("c", "personal"), ("a", "utp_cta")]


def test_get_published_scores_only_published_with_score(db_session):
    db_session.add_all([
        Post(text="1", category="utp_cta", status="published", sector="a", score=10),
        Post(text="2", category="utp_cta", status="published", sector=None, score=2.5),
        Post(text="3", category="utp_cta", status="published", sector="a", score=None),
        Post(text="4", category="utp_cta", status="scheduled", sector="a", score=99),
    ])
    db_session.commit()

    assert sorted(get_published_scores(db_session), key=lambda r: r[1]) == [(None, 2.5), ("a", 10.0)]


def test_get_top_performers_can_filter_by_sector(db_session):
    db_session.add_all([
        Post(text="prod", category="utp_cta", status="published", sector="производство", score=125),
        Post(text="log", category="utp_cta", status="published", sector="логистика", score=5),
    ])
    db_session.commit()

    assert [p.text for p in get_top_performers(db_session, n=5)] == ["prod", "log"]
    assert [p.text for p in get_top_performers(db_session, n=5, sector="логистика")] == ["log"]


def test_get_last_post_at_by_sector(db_session):
    db_session.add_all([
        Post(text="1", category="utp_cta", status="published", sector="a",
             created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        Post(text="2", category="utp_cta", status="published", sector="a",
             created_at=datetime(2026, 1, 5, tzinfo=timezone.utc)),
        Post(text="3", category="utp_cta", status="published", sector=None,
             created_at=datetime(2026, 1, 9, tzinfo=timezone.utc)),
    ])
    db_session.commit()

    assert get_last_post_at_by_sector(db_session) == {"a": datetime(2026, 1, 5, tzinfo=timezone.utc)}


def test_list_posts_and_median_filter_by_sector(db_session):
    db_session.add_all([
        Post(text="1", category="utp_cta", status="published", sector="a", score=10),
        Post(text="2", category="utp_cta", status="published", sector="a", score=20),
        Post(text="3", category="utp_cta", status="published", sector="b", score=1000),
    ])
    db_session.commit()

    items, total = list_posts(db_session, sector="a")
    assert total == 2
    assert {p.text for p in items} == {"1", "2"}
    assert median_post_score(db_session, sector="a") == 15.0


def test_sector_exists_true_for_active_and_inactive_false_otherwise(db_session):
    db_session.add_all([
        Sector(name="производство", source="seed"),
        Sector(name="архив", source="llm", active=False),
    ])
    db_session.commit()

    assert sector_exists(db_session, "производство") is True
    assert sector_exists(db_session, "архив") is True
    assert sector_exists(db_session, "неизвестная") is False


def test_set_agent_run_output_ref(db_session):
    run = AgentRun(agent="content", trigger="manual", started_at=datetime.now(timezone.utc), status="running")
    db_session.add(run)
    db_session.commit()

    set_agent_run_output_ref(db_session, run.id, '{"sector": "a"}')
    db_session.commit()
    db_session.refresh(run)

    assert run.output_ref == '{"sector": "a"}'
