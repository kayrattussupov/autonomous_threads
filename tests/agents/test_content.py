import json
from unittest.mock import MagicMock

from src.agents.content import ContentAgent
from src.db.models import AgentRun, Post, StyleVariant
from src.llm.client import LLMResponse


class _ScriptedLLMClient:
    """Returns each item in `script` in order, one per call to complete()."""
    def __init__(self, script: list[str]):
        self._script = list(script)
        self.calls = []

    def complete(self, role, messages, run_id=None, step_no=None):
        self.calls.append(role)
        text = self._script.pop(0)
        return LLMResponse(text=text, tokens_in=20, tokens_out=5, cost_usd=0.0002, model="glm-4.7", finish_reason="stop")


def _tool_call_json(tool_name: str, tool_args: dict, thought: str = "t") -> str:
    return json.dumps({"thought": thought, "tool_name": tool_name, "tool_args": tool_args})


def _seed_active_style(db_session) -> StyleVariant:
    variant = StyleVariant(name="v1", genome="GENOME_TEXT", status="active", created_by="human", posts_n=0)
    db_session.add(variant)
    db_session.commit()
    return variant


def test_content_agent_saves_draft_when_critic_passes(db_session, monkeypatch):
    variant = _seed_active_style(db_session)
    monkeypatch.setattr(
        "src.agents.content.load_settings",
        lambda: {
            "post_length": {"min_chars": 5, "max_chars": 400, "hard_max_chars": 500},
            "publish_times": ["09:00", "14:00", "20:00"],
            "publish_timezone": "Asia/Almaty",
            "agent_limits": {"max_steps": 8, "max_tokens": 40000, "max_seconds": 120},
        },
    )
    monkeypatch.setattr("src.agents.content.run_style_critic", lambda **kwargs: {
        "pass": True, "issues": [], "tokens_in": 10, "tokens_out": 2, "cost_usd": 0.0,
    })

    good_post = "Короткий пост про автоматизацию для СМБ."
    script = [_tool_call_json("save_draft", {
        "text": good_post, "category": "educational",
    })]
    agent = ContentAgent(llm_client=_ScriptedLLMClient(script))

    run = agent.run(trigger="manual")

    assert run.status == "ok"
    post = db_session.query(Post).filter_by(text=good_post).one()
    assert post.status == "scheduled"
    assert post.style_variant_id == variant.id
    assert post.scheduled_at is not None

    db_session.refresh(variant)
    assert variant.posts_n == 1


def test_content_agent_allows_one_regeneration_then_needs_review(db_session, monkeypatch):
    _seed_active_style(db_session)
    monkeypatch.setattr(
        "src.agents.content.load_settings",
        lambda: {
            "post_length": {"min_chars": 5, "max_chars": 400, "hard_max_chars": 500},
            "publish_times": ["09:00"],
            "publish_timezone": "Asia/Almaty",
            "agent_limits": {"max_steps": 8, "max_tokens": 40000, "max_seconds": 120},
        },
    )
    critic_results = iter([
        {"pass": False, "issues": ["слишком пафосно"], "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0},
        {"pass": False, "issues": ["всё ещё пафосно"], "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0},
    ])
    monkeypatch.setattr("src.agents.content.run_style_critic", lambda **kwargs: next(critic_results))
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.content.send_telegram_alert", alert_mock)

    script = [
        _tool_call_json("save_draft", {"text": "первая попытка", "category": "educational"}),
        _tool_call_json("save_draft", {"text": "вторая попытка", "category": "educational"}),
    ]
    agent = ContentAgent(llm_client=_ScriptedLLMClient(script))

    run = agent.run(trigger="manual")

    assert run.status == "ok"
    post = db_session.query(Post).filter_by(text="вторая попытка").one()
    assert post.status == "needs_review"
    alert_mock.assert_called_once()
    assert db_session.query(Post).filter_by(text="первая попытка").count() == 0  # rejected draft never persisted


def test_content_agent_parses_markdown_fenced_json_tool_call(db_session, monkeypatch):
    """Real LLMs routinely wrap JSON responses in ```json ... ``` fences.
    decide_next_action must tolerate this rather than treating it as invalid
    JSON (src.llm.json_extract.extract_json handles the stripping)."""
    _seed_active_style(db_session)
    monkeypatch.setattr(
        "src.agents.content.load_settings",
        lambda: {
            "post_length": {"min_chars": 5, "max_chars": 400, "hard_max_chars": 500},
            "publish_times": ["09:00"],
            "publish_timezone": "Asia/Almaty",
            "agent_limits": {"max_steps": 8, "max_tokens": 40000, "max_seconds": 120},
        },
    )
    monkeypatch.setattr("src.agents.content.run_style_critic", lambda **kwargs: {
        "pass": True, "issues": [], "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0,
    })

    fenced_text = "поддержка markdown-ограды"
    fenced = "```json\n" + _tool_call_json("save_draft", {"text": fenced_text, "category": "educational"}) + "\n```"
    script = [fenced]
    agent = ContentAgent(llm_client=_ScriptedLLMClient(script))

    run = agent.run(trigger="manual")

    assert run.status == "ok"
    post = db_session.query(Post).filter_by(text=fenced_text).one()
    assert post.status == "scheduled"


def test_content_agent_invalid_json_response_recorded_as_failed_step_and_retried(db_session, monkeypatch):
    _seed_active_style(db_session)
    monkeypatch.setattr(
        "src.agents.content.load_settings",
        lambda: {
            "post_length": {"min_chars": 5, "max_chars": 400, "hard_max_chars": 500},
            "publish_times": ["09:00"],
            "publish_timezone": "Asia/Almaty",
            "agent_limits": {"max_steps": 8, "max_tokens": 40000, "max_seconds": 120},
        },
    )
    monkeypatch.setattr("src.agents.content.run_style_critic", lambda **kwargs: {
        "pass": True, "issues": [], "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0,
    })

    script = [
        "this is not json at all",
        _tool_call_json("save_draft", {"text": "восстановился после ошибки", "category": "educational"}),
    ]
    agent = ContentAgent(llm_client=_ScriptedLLMClient(script))

    run = agent.run(trigger="manual")

    assert run.status == "ok"
    assert db_session.query(Post).filter_by(text="восстановился после ошибки").one().status == "scheduled"

    run_row = db_session.query(AgentRun).filter_by(id=run.id).one()
    steps = run_row.steps
    assert len(steps) == 2
    assert steps[0].tool_ok is False
    assert steps[1].tool_ok is True


def test_content_agent_records_llm_usage_from_both_drafting_and_critic_calls(db_session, monkeypatch):
    _seed_active_style(db_session)
    monkeypatch.setattr(
        "src.agents.content.load_settings",
        lambda: {
            "post_length": {"min_chars": 5, "max_chars": 400, "hard_max_chars": 500},
            "publish_times": ["09:00"],
            "publish_timezone": "Asia/Almaty",
            "agent_limits": {"max_steps": 8, "max_tokens": 40000, "max_seconds": 120},
        },
    )
    monkeypatch.setattr("src.agents.content.run_style_critic", lambda **kwargs: {
        "pass": True, "issues": [], "tokens_in": 100, "tokens_out": 20, "cost_usd": 0.01,
    })

    script = [_tool_call_json("save_draft", {"text": "пост для учёта расходов", "category": "educational"})]
    agent = ContentAgent(llm_client=_ScriptedLLMClient(script))

    run = agent.run(trigger="manual")

    # 20 (drafting call tokens_in) + 100 (critic call tokens_in) = 120
    assert run.tokens_in == 120
    assert run.tokens_out == 25
    assert float(run.cost_usd) == 0.01 + 0.0002


def test_content_agent_threads_source_url_through_to_persisted_post(db_session, monkeypatch):
    """category='news' requires source_url per SPEC.md §6.1 (Task 6's run_style_critic
    enforces this). This proves ContentAgent's save_draft tool actually accepts a
    source_url tool_arg and threads it through to run_style_critic AND the persisted
    Post row, rather than hardcoding source_url=None (which would make it impossible
    for any category='news' post to ever pass style_critic)."""
    variant = _seed_active_style(db_session)
    monkeypatch.setattr(
        "src.agents.content.load_settings",
        lambda: {
            "post_length": {"min_chars": 5, "max_chars": 400, "hard_max_chars": 500},
            "publish_times": ["09:00"],
            "publish_timezone": "Asia/Almaty",
            "agent_limits": {"max_steps": 8, "max_tokens": 40000, "max_seconds": 120},
        },
    )
    # verify_source() now actually runs (finding #2's fix) — mock it so this
    # test doesn't depend on a real network call to example.com.
    monkeypatch.setattr("src.agents.content.verify_source", lambda url: True)
    captured_kwargs = {}

    def _fake_run_style_critic(**kwargs):
        captured_kwargs.update(kwargs)
        return {"pass": True, "issues": [], "tokens_in": 10, "tokens_out": 2, "cost_usd": 0.0}

    monkeypatch.setattr("src.agents.content.run_style_critic", _fake_run_style_critic)

    news_text = "Свежая новость про ИИ-агентов для бизнеса."
    source_url = "https://example.com/news/ai-agents"
    script = [_tool_call_json("save_draft", {
        "text": news_text, "category": "news", "source_url": source_url,
    })]
    agent = ContentAgent(llm_client=_ScriptedLLMClient(script))

    run = agent.run(trigger="manual")

    assert run.status == "ok"
    assert captured_kwargs["source_url"] == source_url

    post = db_session.query(Post).filter_by(text=news_text).one()
    assert post.status == "scheduled"
    assert post.source_url == source_url
    assert post.style_variant_id == variant.id


def test_content_agent_drops_unverified_source_url_for_news(db_session, monkeypatch):
    """A category='news' draft whose source_url fails verify_source() must be
    treated the same as a missing source_url: not persisted, and style_critic's
    news-needs-source_url check should fail on it (no free pass for a
    hallucinated/dead URL string)."""
    _seed_active_style(db_session)
    monkeypatch.setattr(
        "src.agents.content.load_settings",
        lambda: {
            "post_length": {"min_chars": 5, "max_chars": 400, "hard_max_chars": 500},
            "publish_times": ["09:00"],
            "publish_timezone": "Asia/Almaty",
            "agent_limits": {"max_steps": 8, "max_tokens": 40000, "max_seconds": 120},
        },
    )
    monkeypatch.setattr("src.agents.content.verify_source", lambda url: False)
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.content.send_telegram_alert", alert_mock)

    calls = []

    def _fake_run_style_critic(**kwargs):
        calls.append(kwargs)
        # Mirror the real style_critic's news-needs-source_url rule (only
        # applies to category='news').
        needs_url = kwargs["category"] == "news" and not kwargs["source_url"]
        issues = ["category='news' требует проверенный source_url"] if needs_url else []
        return {"pass": len(issues) == 0, "issues": issues, "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0}

    monkeypatch.setattr("src.agents.content.run_style_critic", _fake_run_style_critic)

    news_text = "Новость с поддельной ссылкой на источник."
    fake_source_url = "https://fake-not-real.example.com/news/x"
    good_text = "Другой пост без ссылки, обычная категория."
    script = [
        _tool_call_json("save_draft", {"text": news_text, "category": "news", "source_url": fake_source_url}),
        _tool_call_json("save_draft", {"text": good_text, "category": "educational"}),
    ]
    agent = ContentAgent(llm_client=_ScriptedLLMClient(script))

    run = agent.run(trigger="manual")

    assert run.status == "ok"
    # verify_source() returned False, so source_url must have been nulled out
    # before reaching run_style_critic — same as if it were never supplied.
    assert calls[0]["source_url"] is None
    assert db_session.query(Post).filter_by(text=news_text).count() == 0  # never persisted with a fake url
    assert db_session.query(Post).filter_by(text=good_text).one().status == "scheduled"
