from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.agents.publisher import publish_scheduled_posts
from src.db.models import AgentRun, Post
from src.db.repo import insert_post
from src.threads.write_client import PublishingLimitExceeded, ThreadsAPIError


class _FakeWriteClient:
    def __init__(self, media_id="media-123", raises=None):
        self._media_id = media_id
        self._raises = raises
        self.published_texts = []

    def publish_text_post(self, text):
        if self._raises:
            raise self._raises
        self.published_texts.append(text)
        return self._media_id


def test_publish_scheduled_posts_publishes_due_posts(db_session):
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="due now", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=1))
    insert_post(db_session, text="future", category="educational", status="scheduled", scheduled_at=now + timedelta(hours=1))
    db_session.commit()

    write_client = _FakeWriteClient(media_id="abc123")
    result = publish_scheduled_posts(trigger="manual", write_client=write_client)

    assert result == {"published": 1, "failed": 0}
    assert write_client.published_texts == ["due now"]

    published = db_session.query(Post).filter_by(text="due now").one()
    assert published.status == "published"
    assert published.threads_media_id == "abc123"
    assert published.posted_at is not None

    future = db_session.query(Post).filter_by(text="future").one()
    assert future.status == "scheduled"


def test_publish_scheduled_posts_marks_failed_and_alerts_on_api_error(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="will fail", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=1))
    db_session.commit()

    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.publisher.send_telegram_alert", alert_mock)
    write_client = _FakeWriteClient(raises=ThreadsAPIError("publish failed"))

    result = publish_scheduled_posts(trigger="manual", write_client=write_client)

    assert result == {"published": 0, "failed": 1}
    failed_post = db_session.query(Post).filter_by(text="will fail").one()
    assert failed_post.status == "failed"
    alert_mock.assert_called_once()


def test_publish_scheduled_posts_stops_on_publishing_limit_exceeded(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="post 1", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=2))
    insert_post(db_session, text="post 2", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=1))
    db_session.commit()

    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.publisher.send_telegram_alert", alert_mock)
    write_client = _FakeWriteClient(raises=PublishingLimitExceeded("250/day limit hit"))

    result = publish_scheduled_posts(trigger="manual", write_client=write_client)

    # Only the post that actually raised PublishingLimitExceeded is "failed".
    # PublishingLimitExceeded is a temporary daily-quota condition, not a
    # permanent failure — the other due post was never attempted and must
    # stay "scheduled" so the next publisher_every_10_min run retries it
    # (Block 4's dashboard reads `status` as real signal, so a never-attempted
    # post must not be mislabeled "failed").
    assert result == {"published": 0, "failed": 1}
    failed_post = db_session.query(Post).filter_by(text="post 1").one()
    assert failed_post.status == "failed"
    untouched_post = db_session.query(Post).filter_by(text="post 2").one()
    assert untouched_post.status == "scheduled"
    alert_mock.assert_called_once()


def test_publish_scheduled_posts_traces_to_agent_runs(db_session):
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="traced post", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=1))
    db_session.commit()

    publish_scheduled_posts(trigger="manual", write_client=_FakeWriteClient())

    run = db_session.query(AgentRun).filter_by(agent="content_publisher").one()
    assert run.status == "ok"
    assert run.finished_at is not None
