from unittest.mock import MagicMock, patch

import pytest

from src.threads.read_client import AuthError, DailyViewCapExceeded, ThreadsReadClient


@pytest.fixture()
def client():
    return ThreadsReadClient(daily_view_cap=200)


def test_search_keyword_happy_path(client, db_session, monkeypatch):
    monkeypatch.setattr("src.threads.read_client.random.uniform", lambda a, b: 0)
    monkeypatch.setattr("src.threads.read_client.time.sleep", lambda s: None)

    fake_driver = MagicMock()
    fake_build_driver = MagicMock(return_value=fake_driver)
    fake_login = MagicMock(return_value=True)
    fake_scrape_keyword = MagicMock(return_value=[{"keyword": "n8n", "text": "post", "url": "https://threads.net/post/1"}])
    with patch(
        "src.threads.read_client._get_threads_app_modules",
        return_value=(fake_build_driver, fake_login, fake_scrape_keyword),
    ):
        results = client.search_keyword("n8n")

    assert results == [{"keyword": "n8n", "text": "post", "url": "https://threads.net/post/1"}]
    fake_driver.quit.assert_called_once()


def test_search_keyword_raises_auth_error_without_retry(client, monkeypatch):
    monkeypatch.setattr("src.threads.read_client.random.uniform", lambda a, b: 0)
    monkeypatch.setattr("src.threads.read_client.time.sleep", lambda s: None)

    fake_driver = MagicMock()
    fake_build_driver = MagicMock(return_value=fake_driver)
    fake_login = MagicMock(return_value=False)
    with patch(
        "src.threads.read_client._get_threads_app_modules",
        return_value=(fake_build_driver, fake_login, MagicMock()),
    ):
        with pytest.raises(AuthError):
            client.search_keyword("n8n")

    assert fake_login.call_count == 1  # no retries
    fake_driver.quit.assert_called_once()


def test_daily_view_cap_enforced(client, db_session):
    from src.db.repo import increment_daily_limit
    increment_daily_limit(db_session, "feed_views", by=200)
    db_session.commit()

    with pytest.raises(DailyViewCapExceeded):
        client.search_keyword("n8n")
