from datetime import datetime, timezone

from src.db.models import TelegramAlert
from src.db.repo import list_telegram_alerts, save_telegram_alert


def test_save_telegram_alert_persists_all_fields(db_session):
    save_telegram_alert(
        db_session,
        source="publisher",
        text="content_publisher: остановлен",
        success=True,
        error_detail=None,
        chat_id="12345",
        message_id=987,
        retry_count=1,
    )
    db_session.commit()

    row = db_session.query(TelegramAlert).one()
    assert row.source == "publisher"
    assert row.text == "content_publisher: остановлен"
    assert row.success is True
    assert row.error_detail is None
    assert row.chat_id == "12345"
    assert row.message_id == 987
    assert row.retry_count == 1
    assert row.sent_at is not None


def test_list_telegram_alerts_returns_newest_first(db_session):
    older = TelegramAlert(
        source="feed_miner", text="older", success=True, retry_count=1,
        sent_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    newer = TelegramAlert(
        source="analyst", text="newer", success=False, retry_count=1,
        sent_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    db_session.add_all([older, newer])
    db_session.commit()

    alerts = list_telegram_alerts(db_session, limit=50)

    assert [a.id for a in alerts] == [newer.id, older.id]


def test_list_telegram_alerts_respects_limit(db_session):
    for i in range(3):
        db_session.add(TelegramAlert(
            source="publisher", text=f"msg {i}", success=True, retry_count=1,
            sent_at=datetime(2026, 1, i + 1, tzinfo=timezone.utc),
        ))
    db_session.commit()

    alerts = list_telegram_alerts(db_session, limit=2)

    assert len(alerts) == 2
