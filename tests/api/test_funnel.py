from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import Lead, Post, Reply
from src.db.repo import _months_ago_start

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_funnel_requires_bearer_token():
    response = client.get("/funnel")
    assert response.status_code == 401


def test_get_funnel_aggregates_by_month(db_session):
    posted = datetime(2026, 3, 15, tzinfo=timezone.utc)
    post = Post(text="p1", category="news", status="published", posted_at=posted, views=100, replies_count=2)
    db_session.add(post)
    db_session.flush()

    db_session.add(Reply(
        threads_reply_id="r1", post_id=post.id, kind="question",
        received_at=posted, responded_at=posted, status="sent",
    ))
    db_session.add(Reply(
        threads_reply_id="r2", post_id=post.id, kind="spam",
        received_at=posted, responded_at=None, status="ignored",
    ))
    db_session.add(Lead(threads_username="u1", created_at=posted))
    db_session.commit()

    response = client.get("/funnel", params={"months": 12}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    march = next(m for m in body if m["month"] == "2026-03")
    assert march["posts"] == 1
    assert march["views"] == 100
    assert march["replies"] == 2
    assert march["conversations"] == 1  # only the responded question counts, not the unresponded spam
    assert march["leads"] == 1


def test_months_ago_start_anchors_to_month_boundary():
    assert _months_ago_start(6, today=date(2026, 9, 2)) == datetime(2026, 4, 1, tzinfo=timezone.utc)
    assert _months_ago_start(1, today=date(2026, 9, 2)) == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert _months_ago_start(3, today=date(2026, 1, 15)) == datetime(2025, 11, 1, tzinfo=timezone.utc)
