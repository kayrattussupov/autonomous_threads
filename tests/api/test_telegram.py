from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import TelegramAlert

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_telegram_alerts_requires_bearer_token():
    response = client.get("/telegram-alerts")
    assert response.status_code == 401


def test_get_telegram_alerts_returns_newest_first(db_session):
    older = TelegramAlert(
        source="feed_miner", text="older", success=True, retry_count=1,
        sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    newer = TelegramAlert(
        source="analyst", text="newer", success=False, error_detail="HTTP 400: Bad Request",
        retry_count=1, sent_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([older, newer])
    db_session.commit()

    response = client.get("/telegram-alerts", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["id"] == newer.id
    assert body[0]["success"] is False
    assert body[0]["error_detail"] == "HTTP 400: Bad Request"
    assert body[1]["id"] == older.id


def test_get_telegram_alerts_respects_limit(db_session):
    for i in range(3):
        db_session.add(TelegramAlert(
            source="publisher", text=f"msg {i}", success=True, retry_count=1,
            sent_at=datetime(2026, 1, i + 1, tzinfo=timezone.utc),
        ))
    db_session.commit()

    response = client.get("/telegram-alerts?limit=2", headers=AUTH)
    assert response.status_code == 200
    assert len(response.json()) == 2
