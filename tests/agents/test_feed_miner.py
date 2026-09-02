from unittest.mock import MagicMock

import pytest

from src.agents.feed_miner import run_feed_miner
from src.db.models import AgentRun, SwipeFilePost
from src.llm.client import BudgetExceeded, LLMResponse
from src.threads.read_client import AuthError, DailyViewCapExceeded


class _FakeReadClient:
    def __init__(self, results_by_keyword: dict):
        self._results = results_by_keyword
        self.calls = []

    def search_keyword(self, keyword, scroll_times=5):
        self.calls.append(keyword)
        result = self._results[keyword]
        if isinstance(result, Exception):
            raise result
        return result


class _FakeLLMClient:
    def __init__(self, topic: str = "автоматизация"):
        self._topic = topic
        self.calls = []

    def complete(self, role, messages, run_id=None, step_no=None):
        self.calls.append(role)
        return LLMResponse(text=self._topic, tokens_in=10, tokens_out=2, cost_usd=0.0, model="glm-4.7-flash", finish_reason="stop")


def test_run_feed_miner_collects_classifies_and_dedups(db_session, monkeypatch):
    monkeypatch.setattr(
        "src.agents.feed_miner.load_settings",
        lambda: {"search_groups": [{"name": "g1", "keywords": ["n8n"]}]},
    )
    read_client = _FakeReadClient({
        "n8n": [
            {"keyword": "n8n", "text": "Пост 1", "url": "https://threads.net/post/aaa/"},
            {"keyword": "n8n", "text": "Пост 2", "url": "https://threads.net/post/bbb/"},
        ]
    })
    llm_client = _FakeLLMClient(topic="автоматизация")

    result = run_feed_miner(trigger="manual", read_client=read_client, llm_client=llm_client)

    assert result == {"collected": 2, "skipped_dupes": 0, "status": "ok"}
    assert llm_client.calls == ["classifier", "classifier"]

    rows = db_session.query(SwipeFilePost).order_by(SwipeFilePost.threads_post_id).all()
    assert [r.threads_post_id for r in rows] == ["aaa", "bbb"]
    assert all(r.topic == "автоматизация" for r in rows)

    run = db_session.query(AgentRun).filter_by(agent="feed_miner").one()
    assert run.status == "ok"
    assert run.trigger == "manual"
    assert run.tokens_in == 20
    assert run.tokens_out == 4
    assert run.cost_usd == 0


def test_run_feed_miner_skips_already_seen_posts(db_session, monkeypatch):
    monkeypatch.setattr(
        "src.agents.feed_miner.load_settings",
        lambda: {"search_groups": [{"name": "g1", "keywords": ["n8n"]}]},
    )
    from src.db.repo import insert_swipe_file_post
    insert_swipe_file_post(db_session, threads_post_id="aaa", text="уже видели", topic="старое")
    db_session.commit()

    read_client = _FakeReadClient({
        "n8n": [{"keyword": "n8n", "text": "Пост 1", "url": "https://threads.net/post/aaa/"}]
    })
    llm_client = _FakeLLMClient()

    result = run_feed_miner(trigger="manual", read_client=read_client, llm_client=llm_client)

    assert result == {"collected": 0, "skipped_dupes": 1, "status": "ok"}
    assert llm_client.calls == []  # never classify a dupe — don't waste budget


def test_run_feed_miner_stops_on_auth_error_and_alerts(db_session, monkeypatch):
    monkeypatch.setattr(
        "src.agents.feed_miner.load_settings",
        lambda: {"search_groups": [{"name": "g1", "keywords": ["n8n", "маркетинг"]}]},
    )
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.feed_miner.send_telegram_alert", alert_mock)

    read_client = _FakeReadClient({"n8n": AuthError("session expired")})
    llm_client = _FakeLLMClient()

    result = run_feed_miner(trigger="manual", read_client=read_client, llm_client=llm_client)

    assert result["status"] == "failed"
    assert read_client.calls == ["n8n"]  # never attempts the 2nd keyword — no retries
    alert_mock.assert_called_once()
    assert "session expired" in alert_mock.call_args[0][0]

    run = db_session.query(AgentRun).filter_by(agent="feed_miner").one()
    assert run.status == "failed"


def test_run_feed_miner_stops_on_daily_cap_and_alerts(db_session, monkeypatch):
    monkeypatch.setattr(
        "src.agents.feed_miner.load_settings",
        lambda: {"search_groups": [{"name": "g1", "keywords": ["n8n"]}]},
    )
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.feed_miner.send_telegram_alert", alert_mock)

    read_client = _FakeReadClient({"n8n": DailyViewCapExceeded("cap hit")})
    llm_client = _FakeLLMClient()

    result = run_feed_miner(trigger="manual", read_client=read_client, llm_client=llm_client)

    assert result["status"] == "failed"
    alert_mock.assert_called_once()


def test_run_feed_miner_finishes_run_on_budget_exceeded(db_session, monkeypatch):
    monkeypatch.setattr(
        "src.agents.feed_miner.load_settings",
        lambda: {"search_groups": [{"name": "g1", "keywords": ["n8n"]}]},
    )
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.feed_miner.send_telegram_alert", alert_mock)

    read_client = _FakeReadClient({
        "n8n": [{"keyword": "n8n", "text": "Пост 1", "url": "https://threads.net/post/aaa/"}]
    })

    class _BudgetBustingLLMClient:
        def complete(self, role, messages, run_id=None, step_no=None):
            raise BudgetExceeded("month-to-date spend exceeded hard stop")

    result = run_feed_miner(trigger="manual", read_client=read_client, llm_client=_BudgetBustingLLMClient())

    assert result["status"] == "budget_stop"

    run = db_session.query(AgentRun).filter_by(agent="feed_miner").one()
    assert run.status == "budget_stop"
    assert run.finished_at is not None

    alert_mock.assert_called_once()
    assert "budget" in alert_mock.call_args[0][0].lower()


def test_run_feed_miner_alerts_and_fails_cleanly_on_unexpected_error(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.feed_miner.load_settings", lambda: {})  # missing "search_groups"
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.feed_miner.send_telegram_alert", alert_mock)

    read_client = _FakeReadClient({})
    llm_client = _FakeLLMClient()

    result = run_feed_miner(trigger="manual", read_client=read_client, llm_client=llm_client)

    assert result["status"] == "failed"
    alert_mock.assert_called_once()
    assert "unexpected error" in alert_mock.call_args[0][0].lower()

    run = db_session.query(AgentRun).filter_by(agent="feed_miner").one()
    assert run.status == "failed"
    assert run.finished_at is not None


def test_run_feed_miner_skips_malformed_post_without_aborting(db_session, monkeypatch):
    monkeypatch.setattr(
        "src.agents.feed_miner.load_settings",
        lambda: {"search_groups": [{"name": "g1", "keywords": ["n8n"]}]},
    )
    read_client = _FakeReadClient({
        "n8n": [
            {"keyword": "n8n", "url": "https://threads.net/post/aaa/"},  # missing "text"
            {"keyword": "n8n", "text": "Пост 2", "url": "https://threads.net/post/bbb/"},
        ]
    })
    llm_client = _FakeLLMClient(topic="автоматизация")

    result = run_feed_miner(trigger="manual", read_client=read_client, llm_client=llm_client)

    assert result == {"collected": 1, "skipped_dupes": 0, "status": "ok"}
    assert llm_client.calls == ["classifier"]

    rows = db_session.query(SwipeFilePost).all()
    assert [r.threads_post_id for r in rows] == ["bbb"]
