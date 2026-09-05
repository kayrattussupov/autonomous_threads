from datetime import datetime, timedelta, timezone

from src.db.repo import (
    get_posts_for_reply_triage,
    insert_lead,
    insert_post,
    insert_reply,
    reply_exists,
)


def test_reply_exists_false_then_true(db_session):
    assert reply_exists(db_session, "r1") is False

    insert_reply(db_session, threads_reply_id="r1", text="Как это работает?", kind="question", status="pending_approval")
    db_session.commit()

    assert reply_exists(db_session, "r1") is True
    assert reply_exists(db_session, "does-not-exist") is False


def test_insert_reply_returns_row_with_id(db_session):
    reply = insert_reply(db_session, threads_reply_id="r2", text="Спасибо, полезно!", kind="praise", status="ignored")
    db_session.commit()

    assert reply.id is not None
    assert reply.threads_reply_id == "r2"
    assert reply.kind == "praise"


def test_insert_lead_returns_row_with_id(db_session):
    lead = insert_lead(db_session, threads_username="user1", source_url="https://www.threads.net/@user1", status="scored")
    db_session.commit()

    assert lead.id is not None
    assert lead.threads_username == "user1"
    assert lead.status == "scored"
    assert lead.score is None


def test_get_posts_for_reply_triage_filters_by_status_media_id_and_recency(db_session):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=30)

    in_range = insert_post(db_session, text="in range", category="educational", status="published", threads_media_id="m1", posted_at=now - timedelta(days=1))
    insert_post(db_session, text="too old", category="educational", status="published", threads_media_id="m2", posted_at=now - timedelta(days=60))
    insert_post(db_session, text="draft", category="educational", status="draft", threads_media_id="m3", posted_at=now - timedelta(days=1))
    insert_post(db_session, text="no media id", category="educational", status="published", posted_at=now - timedelta(days=1))
    insert_post(db_session, text="null posted_at", category="educational", status="published", threads_media_id="m4")
    db_session.commit()

    posts = get_posts_for_reply_triage(db_session, since)

    assert [p.id for p in posts] == [in_range.id]
