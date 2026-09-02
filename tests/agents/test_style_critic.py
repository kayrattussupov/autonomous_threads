import json
from unittest.mock import MagicMock

from src.agents.style_critic import run_style_critic
from src.llm.client import LLMResponse


class _FakeLLMClient:
    def __init__(self, issues: list[str]):
        self._issues = issues
        self.calls = []

    def complete(self, role, messages, run_id=None, step_no=None):
        self.calls.append(role)
        return LLMResponse(
            text=json.dumps({"issues": self._issues}),
            tokens_in=50, tokens_out=10, cost_usd=0.0001,
            model="glm-4.7-flash", finish_reason="stop",
        )


class _FencedLLMClient:
    """Wraps the JSON response in a ```json fence, as real LLMs commonly do."""
    def __init__(self, issues: list[str]):
        self._issues = issues
        self.calls = []

    def complete(self, role, messages, run_id=None, step_no=None):
        self.calls.append(role)
        return LLMResponse(
            text="```json\n" + json.dumps({"issues": self._issues}) + "\n```",
            tokens_in=50, tokens_out=10, cost_usd=0.0001,
            model="glm-4.7-flash", finish_reason="stop",
        )


def test_pass_when_no_issues(monkeypatch):
    monkeypatch.setattr(
        "src.agents.style_critic.load_settings",
        lambda: {"post_length": {"min_chars": 200, "max_chars": 400, "hard_max_chars": 500}},
    )
    text = "х" * 250
    llm_client = _FakeLLMClient(issues=[])

    result = run_style_critic(
        text=text, category="educational", source_url=None, genome="genome text",
        recent_post_texts=["другой пост"], llm_client=llm_client,
    )

    assert result["pass"] is True
    assert result["issues"] == []
    assert result["tokens_in"] == 50
    assert llm_client.calls == ["style_critic"]


def test_fails_on_hard_length_limit(monkeypatch):
    monkeypatch.setattr(
        "src.agents.style_critic.load_settings",
        lambda: {"post_length": {"min_chars": 200, "max_chars": 400, "hard_max_chars": 500}},
    )
    text = "х" * 501
    llm_client = _FakeLLMClient(issues=[])

    result = run_style_critic(
        text=text, category="educational", source_url=None, genome="g",
        recent_post_texts=[], llm_client=llm_client,
    )

    assert result["pass"] is False
    assert any("500" in issue for issue in result["issues"])


def test_fails_outside_target_range_but_under_hard_limit(monkeypatch):
    monkeypatch.setattr(
        "src.agents.style_critic.load_settings",
        lambda: {"post_length": {"min_chars": 200, "max_chars": 400, "hard_max_chars": 500}},
    )
    text = "х" * 450  # over 400, under hard 500
    llm_client = _FakeLLMClient(issues=[])

    result = run_style_critic(
        text=text, category="educational", source_url=None, genome="g",
        recent_post_texts=[], llm_client=llm_client,
    )

    assert result["pass"] is False
    assert any("400" in issue for issue in result["issues"])


def test_fails_when_news_category_missing_source_url(monkeypatch):
    monkeypatch.setattr(
        "src.agents.style_critic.load_settings",
        lambda: {"post_length": {"min_chars": 200, "max_chars": 400, "hard_max_chars": 500}},
    )
    text = "х" * 250
    llm_client = _FakeLLMClient(issues=[])

    result = run_style_critic(
        text=text, category="news", source_url=None, genome="g",
        recent_post_texts=[], llm_client=llm_client,
    )

    assert result["pass"] is False
    assert any("source_url" in issue for issue in result["issues"])


def test_fails_on_exact_repeat_of_recent_post(monkeypatch):
    monkeypatch.setattr(
        "src.agents.style_critic.load_settings",
        lambda: {"post_length": {"min_chars": 200, "max_chars": 400, "hard_max_chars": 500}},
    )
    text = "х" * 250
    llm_client = _FakeLLMClient(issues=[])

    result = run_style_critic(
        text=text, category="educational", source_url=None, genome="g",
        recent_post_texts=[text], llm_client=llm_client,
    )

    assert result["pass"] is False
    assert any("повтор" in issue for issue in result["issues"])


def test_parses_markdown_fenced_llm_response(monkeypatch):
    monkeypatch.setattr(
        "src.agents.style_critic.load_settings",
        lambda: {"post_length": {"min_chars": 200, "max_chars": 400, "hard_max_chars": 500}},
    )
    text = "х" * 250
    llm_client = _FencedLLMClient(issues=["не соответствует геному"])

    result = run_style_critic(
        text=text, category="educational", source_url=None, genome="g",
        recent_post_texts=[], llm_client=llm_client,
    )

    assert result["pass"] is False
    assert result["issues"] == ["не соответствует геному"]


def test_combines_deterministic_and_llm_issues(monkeypatch):
    monkeypatch.setattr(
        "src.agents.style_critic.load_settings",
        lambda: {"post_length": {"min_chars": 200, "max_chars": 400, "hard_max_chars": 500}},
    )
    text = "х" * 501  # deterministic failure too
    llm_client = _FakeLLMClient(issues=["не соответствует геному: слишком пафосно"])

    result = run_style_critic(
        text=text, category="educational", source_url=None, genome="g",
        recent_post_texts=[], llm_client=llm_client,
    )

    assert result["pass"] is False
    assert len(result["issues"]) == 2
