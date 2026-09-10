from unittest.mock import MagicMock, patch

import requests

from src.alerts import send_telegram_alert
from src.db.models import TelegramAlert


def test_send_telegram_alert_success(monkeypatch, db_session):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("src.alerts.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"result": {"message_id": 42}})
        result = send_telegram_alert("Auth error in feed_miner", source="feed_miner")

    assert result is True
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.telegram.org/bottest-token/sendMessage"
    assert kwargs["json"] == {"chat_id": "12345", "text": "Auth error in feed_miner"}

    row = db_session.query(TelegramAlert).one()
    assert row.source == "feed_miner"
    assert row.text == "Auth error in feed_miner"
    assert row.success is True
    assert row.error_detail is None
    assert row.chat_id == "12345"
    assert row.message_id == 42
    assert row.retry_count == 1


def test_send_telegram_alert_returns_false_on_non_200(monkeypatch, db_session):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("src.alerts.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
        result = send_telegram_alert("test", source="publisher")

    assert result is False

    row = db_session.query(TelegramAlert).one()
    assert row.success is False
    assert "400" in row.error_detail
    assert "Bad Request" in row.error_detail
    assert row.message_id is None


def test_send_telegram_alert_returns_false_on_network_error(monkeypatch, db_session):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("src.alerts.requests.post", side_effect=requests.exceptions.ConnectionError("no network")):
        result = send_telegram_alert("test", source="analyst")

    assert result is False

    row = db_session.query(TelegramAlert).one()
    assert row.success is False
    assert "no network" in row.error_detail


def test_send_telegram_alert_returns_false_when_not_configured(monkeypatch, db_session):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    result = send_telegram_alert("test", source="reply_triage")

    assert result is False

    row = db_session.query(TelegramAlert).one()
    assert row.source == "reply_triage"
    assert row.success is False
    assert "not configured" in row.error_detail


def test_send_telegram_alert_defaults_source_when_omitted(monkeypatch, db_session):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("src.alerts.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"result": {"message_id": 1}})
        send_telegram_alert("no source given")

    row = db_session.query(TelegramAlert).one()
    assert row.source == "unknown"


def test_send_telegram_alert_survives_db_write_failure(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(
        "src.alerts.save_telegram_alert",
        MagicMock(side_effect=RuntimeError("db unavailable")),
    )

    with patch("src.alerts.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"result": {"message_id": 1}})
        result = send_telegram_alert("test", source="publisher")

    assert result is True
