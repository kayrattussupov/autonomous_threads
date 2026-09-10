from unittest.mock import MagicMock, patch

import pytest
import requests

from src.tools.web_search import verify_source, web_search


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    """Override the root conftest's session-scoped, real-Postgres-backed
    fixture of the same name for this module only — these tests only mock
    `requests` and never touch a database. A directory-level tests/tools/
    conftest.py can't do this override any more because test_safe_sql.py in
    the same directory genuinely needs the real schema fixture; overriding
    at module scope here keeps both files' needs satisfied without a
    directory-wide conftest.py (see git history of tests/tools/conftest.py)."""
    yield


def test_web_search_returns_title_url_content(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    with patch("src.tools.web_search.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [
                {"title": "Article 1", "url": "https://example.com/1", "content": "snippet 1"},
                {"title": "Article 2", "url": "https://example.com/2", "content": "snippet 2"},
            ]},
        )
        results = web_search("n8n automation news")

    assert results == [
        {"title": "Article 1", "url": "https://example.com/1", "content": "snippet 1"},
        {"title": "Article 2", "url": "https://example.com/2", "content": "snippet 2"},
    ]
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.tavily.com/search"
    assert kwargs["json"]["api_key"] == "test-key"
    assert kwargs["json"]["query"] == "n8n automation news"


def test_web_search_returns_empty_list_on_api_error(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    with patch("src.tools.web_search.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=500, text="server error")
        results = web_search("query")

    assert results == []


def test_verify_source_true_on_200_with_title():
    with patch("src.tools.web_search.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text="<html><head><title>Real Article</title></head></html>")
        assert verify_source("https://example.com/article") is True


def test_verify_source_false_on_non_200():
    with patch("src.tools.web_search.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=404, text="")
        assert verify_source("https://example.com/missing") is False


def test_verify_source_false_on_empty_title():
    with patch("src.tools.web_search.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text="<html><head><title></title></head></html>")
        assert verify_source("https://example.com/blank-title") is False


def test_verify_source_false_on_network_error():
    with patch("src.tools.web_search.requests.get", side_effect=requests.exceptions.ConnectionError("timeout")):
        assert verify_source("https://example.com/down") is False
