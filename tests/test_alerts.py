from unittest.mock import MagicMock, patch

import pytest
import requests

from src.alerts import send_telegram_alert

pytestmark = pytest.mark.no_db


def test_send_telegram_alert_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("src.alerts.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        result = send_telegram_alert("Auth error in feed_miner")

    assert result is True
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.telegram.org/bottest-token/sendMessage"
    assert kwargs["json"] == {"chat_id": "12345", "text": "Auth error in feed_miner"}


def test_send_telegram_alert_returns_false_on_non_200(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("src.alerts.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
        result = send_telegram_alert("test")

    assert result is False


def test_send_telegram_alert_returns_false_on_network_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("src.alerts.requests.post", side_effect=requests.exceptions.ConnectionError("no network")):
        result = send_telegram_alert("test")

    assert result is False


def test_send_telegram_alert_returns_false_when_not_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    result = send_telegram_alert("test")

    assert result is False
