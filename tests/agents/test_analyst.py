from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.agents.analyst import recompute_nightly_metrics
from src.db.models import AgentRun
from src.db.repo import insert_post
from src.threads.write_client import ThreadsAPIError


class _FakeWriteClient:
    def __init__(self, insights_by_media_id: dict):
        self._insights = insights_by_media_id
        self.calls = []

    def get_media_insights(self, media_id):
        self.calls.append(media_id)
        result = self._insights[media_id]
        if isinstance(result, Exception):
            raise result
        return result


def test_recompute_nightly_metrics_refreshes_insights_and_scores_published_posts_in_window(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.analyst.load_settings", lambda: {"metrics_refresh_window_days": 90})
    now = datetime.now(timezone.utc)
    post = insert_post(
        db_session, text="p1", category="educational", status="published",
        threads_media_id="m1", posted_at=now - timedelta(days=1),
    )
    db_session.commit()

    write_client = _FakeWriteClient({"m1": {"views": 1000, "likes": 5, "replies": 2, "quotes": 1, "reposts": 0, "shares": 0}})

    result = recompute_nightly_metrics(trigger="manual", write_client=write_client)

    assert result["status"] == "ok"
    assert result["refreshed"] == 1
    assert result["refresh_failures"] == 0

    db_session.refresh(post)
    assert post.views == 1000
    assert post.replies_count == 2
    assert post.metrics_updated_at is not None
    assert float(post.score) == 0.01 * 1000 + 1 * 2  # no leads/conversations replies seeded -> 12.0

    run = db_session.query(AgentRun).filter_by(agent="analyst", trigger="manual").one()
    assert run.status == "ok"


def test_recompute_nightly_metrics_skips_post_on_local_api_failure_and_continues(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.analyst.load_settings", lambda: {"metrics_refresh_window_days": 90})
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="fails", category="educational", status="published", threads_media_id="bad", posted_at=now - timedelta(days=1))
    insert_post(db_session, text="ok", category="educational", status="published", threads_media_id="good", posted_at=now - timedelta(days=1))
    db_session.commit()

    write_client = _FakeWriteClient({
        "bad": ThreadsAPIError("HTTP 500"),
        "good": {"views": 10, "likes": 0, "replies": 0, "quotes": 0, "reposts": 0, "shares": 0},
    })

    result = recompute_nightly_metrics(trigger="manual", write_client=write_client)

    assert result["status"] == "ok"
    assert result["refreshed"] == 1
    assert result["refresh_failures"] == 1


def test_recompute_nightly_metrics_ignores_posts_outside_refresh_window(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.analyst.load_settings", lambda: {"metrics_refresh_window_days": 90})
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="too old", category="educational", status="published", threads_media_id="old", posted_at=now - timedelta(days=200))
    db_session.commit()

    write_client = _FakeWriteClient({})

    result = recompute_nightly_metrics(trigger="manual", write_client=write_client)

    assert result["refreshed"] == 0
    assert write_client.calls == []


def test_recompute_nightly_metrics_alerts_and_fails_cleanly_on_unexpected_error(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.analyst.load_settings", lambda: {})  # missing key -> KeyError
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.analyst.send_telegram_alert", alert_mock)

    result = recompute_nightly_metrics(trigger="manual", write_client=_FakeWriteClient({}))

    assert result["status"] == "failed"
    alert_mock.assert_called_once()
