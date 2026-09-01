from unittest.mock import MagicMock, patch

import pytest
import requests

from src.threads.write_client import (
    PublishingLimitExceeded,
    ThreadsAPIError,
    ThreadsWriteClient,
)


@pytest.fixture()
def client():
    return ThreadsWriteClient(access_token="tok", user_id="123")


def test_publish_text_post_happy_path(client):
    with patch("src.threads.write_client.requests.post") as mock_post, \
         patch("src.threads.write_client.requests.get") as mock_get:
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {"id": "container-1"}),
            MagicMock(status_code=200, json=lambda: {"id": "media-1"}),
        ]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"status": "FINISHED"})

        media_id = client.publish_text_post("Тестовый пост")

        assert media_id == "media-1"
        assert mock_post.call_count == 2


def test_check_publishing_limit_raises_when_exhausted(client):
    with patch("src.threads.write_client.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [{"quota_usage": 250, "config": {"quota_total": 250}}]},
        )
        with pytest.raises(PublishingLimitExceeded):
            client.check_publishing_limit()


def test_backoff_on_429_then_success(client, monkeypatch):
    monkeypatch.setattr("src.threads.write_client.time.sleep", lambda s: None)
    responses = [
        MagicMock(status_code=429, headers={}, json=lambda: {"error": "rate limited"}),
        MagicMock(status_code=200, json=lambda: {"id": "container-1"}),
    ]
    with patch("src.threads.write_client.requests.post", side_effect=responses) as mock_post:
        container_id = client.create_container("Пост")
        assert container_id == "container-1"
        assert mock_post.call_count == 2


def test_raises_threads_api_error_on_persistent_failure(client, monkeypatch):
    monkeypatch.setattr("src.threads.write_client.time.sleep", lambda s: None)
    with patch("src.threads.write_client.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=500, json=lambda: {"error": "server error"})
        with pytest.raises(ThreadsAPIError):
            client.create_container("Пост")
