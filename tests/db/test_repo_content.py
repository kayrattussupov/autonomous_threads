from datetime import datetime, timedelta, timezone

from src.db.repo import (
    get_active_playbook_rules,
    get_active_style,
    get_knowledge_base,
    get_posts_due_for_publish,
    get_recent_posts,
    get_swipe_examples,
    get_top_performers,
    increment_style_variant_posts_n,
    insert_post,
    insert_swipe_file_post,
)
from src.db.models import KnowledgeBaseEntry, PlaybookRule, Post, StyleVariant


def test_get_knowledge_base_returns_key_value_dict(db_session):
    db_session.add(KnowledgeBaseEntry(key="niche", value="automation"))
    db_session.add(KnowledgeBaseEntry(key="audience", value="SMB owners"))
    db_session.commit()

    kb = get_knowledge_base(db_session)

    assert kb == {"niche": "automation", "audience": "SMB owners"}


def test_get_active_style_returns_lowest_posts_n(db_session):
    db_session.add(StyleVariant(name="v1", genome="g1", status="active", created_by="human", posts_n=10))
    db_session.add(StyleVariant(name="v2", genome="g2", status="active", created_by="analyst", posts_n=3))
    db_session.add(StyleVariant(name="v3_retired", genome="g3", status="retired", created_by="human", posts_n=0))
    db_session.commit()

    active = get_active_style(db_session)

    assert active.name == "v2"


def test_get_active_style_returns_none_when_no_active_variant(db_session):
    assert get_active_style(db_session) is None


def test_increment_style_variant_posts_n(db_session):
    variant = StyleVariant(name="v1", genome="g", status="active", created_by="human", posts_n=5)
    db_session.add(variant)
    db_session.commit()

    increment_style_variant_posts_n(db_session, variant.id)
    db_session.commit()

    db_session.refresh(variant)
    assert variant.posts_n == 6


def test_get_active_playbook_rules_filters_by_status(db_session):
    db_session.add(PlaybookRule(rule_text="r1", status="testing", version=1))
    db_session.add(PlaybookRule(rule_text="r2", status="confirmed", version=1))
    db_session.add(PlaybookRule(rule_text="r3", status="rejected", version=1))
    db_session.add(PlaybookRule(rule_text="r4", status="proposed", version=1))
    db_session.commit()

    active = get_active_playbook_rules(db_session)

    assert {r.rule_text for r in active} == {"r1", "r2"}


def test_get_recent_posts_orders_by_created_at_desc(db_session):
    insert_post(db_session, text="old", category="educational", status="published")
    db_session.commit()
    insert_post(db_session, text="new", category="educational", status="published")
    db_session.commit()

    recent = get_recent_posts(db_session, n=30)

    assert [p.text for p in recent] == ["new", "old"]


def test_get_top_performers_orders_by_score_desc_published_only(db_session):
    insert_post(db_session, text="low", category="educational", status="published", score=1.0)
    insert_post(db_session, text="high", category="educational", status="published", score=99.0)
    insert_post(db_session, text="draft_high_score", category="educational", status="draft", score=1000.0)
    db_session.commit()

    top = get_top_performers(db_session, n=5)

    assert [p.text for p in top] == ["high", "low"]


def test_get_swipe_examples_filters_by_topic(db_session):
    insert_swipe_file_post(db_session, threads_post_id="a", text="on topic", topic="automation")
    insert_swipe_file_post(db_session, threads_post_id="b", text="off topic", topic="marketing")
    db_session.commit()

    examples = get_swipe_examples(db_session, n=8, topic="automation")

    assert [e.text for e in examples] == ["on topic"]


def test_get_posts_due_for_publish(db_session):
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="due", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=5))
    insert_post(db_session, text="future", category="educational", status="scheduled", scheduled_at=now + timedelta(hours=1))
    insert_post(db_session, text="already_published", category="educational", status="published", scheduled_at=now - timedelta(minutes=5))
    db_session.commit()

    due = get_posts_due_for_publish(db_session, now=now)

    assert [p.text for p in due] == ["due"]
