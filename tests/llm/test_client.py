from datetime import date
from unittest import mock

import httpx
import pytest
from openai import APIConnectionError, RateLimitError

from src.db.models import DailySpend
from src.llm.client import BUDGET_HARD_STOP_USD, MAX_LLM_RETRIES, BudgetExceeded, LLMClient
from src.llm.pricing import cost_usd


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://api.moonshot.ai/v1/chat/completions")
    response = httpx.Response(429, request=request, json={"error": {"message": "rate limited"}})
    return RateLimitError("rate limited", response=response, body=None)


def _connection_error() -> APIConnectionError:
    request = httpx.Request("POST", "https://api.moonshot.ai/v1/chat/completions")
    return APIConnectionError(request=request)


def _fake_completion_response(tokens_in: int = 10, tokens_out: int = 5):
    usage = mock.Mock(prompt_tokens=tokens_in, completion_tokens=tokens_out, prompt_tokens_details=None)
    choice = mock.Mock(finish_reason="stop")
    choice.message.content = "hello"
    return mock.Mock(usage=usage, choices=[choice])


def _client_with_fake_provider(side_effect):
    client = LLMClient.__new__(LLMClient)  # skip __init__ — no config file/API key needed
    client._config = {
        "roles": {"post_writer": {"provider": "kimi", "model": "kimi-k2.6", "max_tokens": 100}},
        "providers": {"kimi": {}},
    }
    fake_provider_client = mock.Mock()
    fake_provider_client.chat.completions.create.side_effect = side_effect
    client._clients = {"kimi": fake_provider_client}
    return client, fake_provider_client


def test_cost_usd_glm47():
    cost = cost_usd("glm-4.7", tokens_in=1000, tokens_out=500)
    assert round(cost, 8) == round((1000 * 0.60 + 500 * 2.20) / 1_000_000, 8)


def test_cost_usd_free_flash():
    assert cost_usd("glm-4.7-flash", tokens_in=100_000, tokens_out=50_000) == 0.0


def test_check_budget_raises_at_hard_stop(db_session, monkeypatch):
    db_session.add(DailySpend(date=date.today(), model="glm-4.7", tokens_in=0, tokens_out=0, cost_usd=BUDGET_HARD_STOP_USD))
    db_session.commit()

    client = LLMClient.__new__(LLMClient)  # skip __init__ — no config/keys needed for this check
    client._config = {"roles": {}}
    with pytest.raises(BudgetExceeded):
        client._check_budget(role="post_writer")


def test_check_budget_soft_stop_blocks_other_roles(db_session):
    db_session.add(DailySpend(date=date.today(), model="glm-4.7", tokens_in=0, tokens_out=0, cost_usd=8.5))
    db_session.commit()

    client = LLMClient.__new__(LLMClient)
    client._config = {"roles": {}}
    client._check_budget(role="post_writer")  # allowed
    with pytest.raises(BudgetExceeded):
        client._check_budget(role="analyst")


def test_complete_retries_transient_errors_then_succeeds(db_session, monkeypatch):
    monkeypatch.setattr("src.llm.client.time.sleep", lambda seconds: None)
    client, fake_provider = _client_with_fake_provider(
        [_rate_limit_error(), _connection_error(), _fake_completion_response()]
    )

    response = client.complete(role="post_writer", messages=[{"role": "user", "content": "hi"}])

    assert response.text == "hello"
    assert fake_provider.chat.completions.create.call_count == 3


def test_complete_gives_up_after_max_retries(db_session, monkeypatch):
    sleep_calls = []
    monkeypatch.setattr("src.llm.client.time.sleep", lambda seconds: sleep_calls.append(seconds))
    errors = [_rate_limit_error() for _ in range(MAX_LLM_RETRIES + 1)]
    client, fake_provider = _client_with_fake_provider(errors)

    with pytest.raises(RateLimitError):
        client.complete(role="post_writer", messages=[{"role": "user", "content": "hi"}])

    assert fake_provider.chat.completions.create.call_count == MAX_LLM_RETRIES + 1
    assert len(sleep_calls) == MAX_LLM_RETRIES  # backs off between attempts, not after the last one


def test_complete_does_not_retry_non_retryable_errors(db_session, monkeypatch):
    sleep_calls = []
    monkeypatch.setattr("src.llm.client.time.sleep", lambda seconds: sleep_calls.append(seconds))
    client, fake_provider = _client_with_fake_provider([ValueError("bad request")])

    with pytest.raises(ValueError):
        client.complete(role="post_writer", messages=[{"role": "user", "content": "hi"}])

    assert fake_provider.chat.completions.create.call_count == 1
    assert sleep_calls == []
