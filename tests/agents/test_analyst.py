import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.agents.analyst import AnalystAgent, recompute_nightly_metrics
from src.db.models import AgentRun, PlaybookRule, StyleVariant
from src.db.repo import insert_post
from src.llm.client import LLMResponse
from src.threads.write_client import ThreadsAPIError


class _FakeWriteClient:
    def __init__(self, insights_by_media_id: dict):
        self._insights = insights_by_media_id
        self.calls = []

    def get_media_insights(self, media_id):
        self.calls.append(media_id)
        result = self._insights[media_id]
        if isinstance(result, Exception):
            raise result
        return result


def test_recompute_nightly_metrics_refreshes_insights_and_scores_published_posts_in_window(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.analyst.load_settings", lambda: {"metrics_refresh_window_days": 90})
    now = datetime.now(timezone.utc)
    post = insert_post(
        db_session, text="p1", category="educational", status="published",
        threads_media_id="m1", posted_at=now - timedelta(days=1),
    )
    db_session.commit()

    write_client = _FakeWriteClient({"m1": {"views": 1000, "likes": 5, "replies": 2, "quotes": 1, "reposts": 0, "shares": 0}})

    result = recompute_nightly_metrics(trigger="manual", write_client=write_client)

    assert result["status"] == "ok"
    assert result["refreshed"] == 1
    assert result["refresh_failures"] == 0

    db_session.refresh(post)
    assert post.views == 1000
    assert post.replies_count == 2
    assert post.metrics_updated_at is not None
    assert float(post.score) == 0.01 * 1000 + 1 * 2  # no leads/conversations replies seeded -> 12.0

    run = db_session.query(AgentRun).filter_by(agent="analyst", trigger="manual").one()
    assert run.status == "ok"


def test_recompute_nightly_metrics_skips_post_on_local_api_failure_and_continues(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.analyst.load_settings", lambda: {"metrics_refresh_window_days": 90})
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="fails", category="educational", status="published", threads_media_id="bad", posted_at=now - timedelta(days=1))
    insert_post(db_session, text="ok", category="educational", status="published", threads_media_id="good", posted_at=now - timedelta(days=1))
    db_session.commit()

    write_client = _FakeWriteClient({
        "bad": ThreadsAPIError("HTTP 500"),
        "good": {"views": 10, "likes": 0, "replies": 0, "quotes": 0, "reposts": 0, "shares": 0},
    })

    result = recompute_nightly_metrics(trigger="manual", write_client=write_client)

    assert result["status"] == "ok"
    assert result["refreshed"] == 1
    assert result["refresh_failures"] == 1


def test_recompute_nightly_metrics_ignores_posts_outside_refresh_window(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.analyst.load_settings", lambda: {"metrics_refresh_window_days": 90})
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="too old", category="educational", status="published", threads_media_id="old", posted_at=now - timedelta(days=200))
    db_session.commit()

    write_client = _FakeWriteClient({})

    result = recompute_nightly_metrics(trigger="manual", write_client=write_client)

    assert result["refreshed"] == 0
    assert write_client.calls == []


def test_recompute_nightly_metrics_alerts_and_fails_cleanly_on_unexpected_error(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.analyst.load_settings", lambda: {})  # missing key -> KeyError
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.analyst.send_telegram_alert", alert_mock)

    result = recompute_nightly_metrics(trigger="manual", write_client=_FakeWriteClient({}))

    assert result["status"] == "failed"
    alert_mock.assert_called_once()


class _ScriptedLLMClient:
    def __init__(self, script: list[str]):
        self._script = list(script)
        self.calls = []

    def complete(self, role, messages, run_id=None, step_no=None):
        self.calls.append(role)
        text = self._script.pop(0)
        return LLMResponse(text=text, tokens_in=20, tokens_out=5, cost_usd=0.0002, model="kimi-k2.6", finish_reason="stop")


def _tool_call_json(tool_name: str, tool_args: dict, thought: str = "t") -> str:
    return json.dumps({"thought": thought, "tool_name": tool_name, "tool_args": tool_args})


def test_analyst_agent_proposes_style_variant_and_finishes(db_session, monkeypatch):
    parent = StyleVariant(name="v1", genome="old genome", status="active", created_by="human")
    db_session.add(parent)
    db_session.commit()

    script = [
        _tool_call_json("propose_style_variant", {
            "name": "v2", "genome": "NEW GENOME " * 20, "rationale": "радикальный сдвиг", "parent_id": parent.id,
        }),
        _tool_call_json("finish", {"summary": "предложен новый стиль"}),
    ]
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.analyst.send_telegram_alert", alert_mock)

    agent = AnalystAgent(llm_client=_ScriptedLLMClient(script))
    run = agent.run(trigger="manual")

    assert run.status == "ok"
    variant = db_session.query(StyleVariant).filter_by(name="v2").one()
    assert variant.status == "draft"
    assert variant.created_by == "analyst"
    alert_mock.assert_called_once()
    assert "v2" in alert_mock.call_args[0][0]


def test_analyst_agent_proposes_playbook_diff(db_session, monkeypatch):
    old_rule = PlaybookRule(rule_text="stale rule", status="testing", version=1)
    db_session.add(old_rule)
    db_session.commit()

    script = [
        _tool_call_json("propose_playbook_diff", {
            "add": [{"rule_text": "post more news", "hypothesis": "h", "target_metric": "leads"}],
            "remove": [old_rule.id],
            "rationale": "news работает лучше",
        }),
        _tool_call_json("finish", {"summary": "готово"}),
    ]
    monkeypatch.setattr("src.agents.analyst.send_telegram_alert", MagicMock(return_value=True))

    agent = AnalystAgent(llm_client=_ScriptedLLMClient(script))
    run = agent.run(trigger="manual")

    assert run.status == "ok"
    new_rule = db_session.query(PlaybookRule).filter_by(rule_text="post more news").one()
    assert new_rule.status == "proposed"
    db_session.refresh(old_rule)
    assert old_rule.status == "proposed_removal"


def test_analyst_agent_sql_tool_rejects_unsafe_query_without_aborting_run(db_session, monkeypatch):
    script = [
        _tool_call_json("sql", {"query": "DROP TABLE posts"}),
        _tool_call_json("finish", {"summary": "закончил без изменений"}),
    ]
    monkeypatch.setattr("src.agents.analyst.send_telegram_alert", MagicMock(return_value=True))

    agent = AnalystAgent(llm_client=_ScriptedLLMClient(script))
    run = agent.run(trigger="manual")

    assert run.status == "ok"
    steps = db_session.query(AgentRun).filter_by(id=run.id).one().steps
    assert steps[0].tool_name == "sql"
    assert "error" in steps[0].tool_result


def test_analyst_agent_finish_without_proposals_alerts_no_proposals(db_session, monkeypatch):
    script = [_tool_call_json("finish", {"summary": "ничего не нашёл"})]
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.analyst.send_telegram_alert", alert_mock)

    agent = AnalystAgent(llm_client=_ScriptedLLMClient(script))
    run = agent.run(trigger="manual")

    assert run.status == "ok"
    assert "новых предложений нет" in alert_mock.call_args[0][0]
