from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.agents.reply_triage import run_reply_triage
from src.db.models import AgentRun, Lead, Reply
from src.db.repo import insert_post, insert_reply
from src.llm.client import BudgetExceeded, LLMResponse
from src.threads.write_client import ThreadsAPIError

_post_count = 0


def _get_post_timestamp():
    global _post_count
    _post_count += 1
    # Return decreasing timestamps so earlier posts in the test have later timestamps (processed first)
    return datetime.now(timezone.utc) - timedelta(seconds=_post_count)


class _FakeWriteClient:
    def __init__(self, results_by_media_id: dict):
        self._results = results_by_media_id
        self.calls = []

    def get_replies(self, media_id):
        self.calls.append(media_id)
        result = self._results[media_id]
        if isinstance(result, Exception):
            raise result
        return result


class _FakeLLMClient:
    def __init__(self, classify_results="question", draft_text="Спасибо за вопрос, отвечу тут."):
        self._classify_results = classify_results if isinstance(classify_results, list) else [classify_results]
        self._classify_idx = 0
        self._draft_text = draft_text
        self.calls = []

    def complete(self, role, messages, run_id=None, step_no=None):
        self.calls.append(role)
        if role == "commenter":
            text = self._draft_text
        else:
            idx = min(self._classify_idx, len(self._classify_results) - 1)
            text = self._classify_results[idx]
            self._classify_idx += 1
        return LLMResponse(text=text, tokens_in=10, tokens_out=5, cost_usd=0.001, model="kimi-k2.6", finish_reason="stop")


def _published_post(db_session, **overrides):
    fields = dict(
        text="Мой пост про автоматизацию",
        category="educational",
        status="published",
        threads_media_id="m1",
        posted_at=_get_post_timestamp(),
    )
    fields.update(overrides)
    post = insert_post(db_session, **fields)
    db_session.commit()
    return post


def test_run_reply_triage_question_generates_draft_and_persists(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    post = _published_post(db_session)
    write_client = _FakeWriteClient({
        "m1": [{"id": "r1", "text": "Как это работает?", "username": "u1", "timestamp": "2026-09-01T10:00:00+0000"}],
    })
    llm_client = _FakeLLMClient(classify_results="question", draft_text="Черновик ответа.")

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=llm_client)

    assert result == {"processed": 1, "skipped_dupes": 0, "skipped_malformed": 0, "leads_found": 0, "status": "ok"}
    assert llm_client.calls == ["classifier", "commenter"]

    reply = db_session.query(Reply).filter_by(threads_reply_id="r1").one()
    assert reply.post_id == post.id
    assert reply.kind == "question"
    assert reply.status == "pending_approval"
    assert reply.draft_response == "Черновик ответа."

    run = db_session.query(AgentRun).filter_by(agent="reply_triage").one()
    assert run.status == "ok"
    assert run.trigger == "manual"


def test_run_reply_triage_skips_already_seen_replies(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    _published_post(db_session)
    insert_reply(db_session, threads_reply_id="r1", text="уже видели", kind="question", status="pending_approval")
    db_session.commit()

    write_client = _FakeWriteClient({
        "m1": [{"id": "r1", "text": "Как это работает?", "username": "u1", "timestamp": "2026-09-01T10:00:00+0000"}],
    })
    llm_client = _FakeLLMClient()

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=llm_client)

    assert result == {"processed": 0, "skipped_dupes": 1, "skipped_malformed": 0, "leads_found": 0, "status": "ok"}
    assert llm_client.calls == []  # never classify a dupe — don't waste budget


def test_run_reply_triage_skips_malformed_reply_without_aborting(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    _published_post(db_session)
    write_client = _FakeWriteClient({
        "m1": [
            {"text": "без id", "username": "u1", "timestamp": "2026-09-01T10:00:00+0000"},  # missing "id"
            {"id": "r2", "text": "Второй, валидный", "username": "u2", "timestamp": "2026-09-01T10:00:00+0000"},
        ],
    })
    llm_client = _FakeLLMClient(classify_results="praise")

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=llm_client)

    assert result == {"processed": 1, "skipped_dupes": 0, "skipped_malformed": 1, "leads_found": 0, "status": "ok"}
    rows = db_session.query(Reply).all()
    assert [r.threads_reply_id for r in rows] == ["r2"]


def test_run_reply_triage_local_get_replies_failure_skips_post_and_continues(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    _published_post(db_session, text="первый пост", threads_media_id="m1")
    _published_post(db_session, text="второй пост", threads_media_id="m2")
    write_client = _FakeWriteClient({
        "m1": ThreadsAPIError("get media_id/replies failed: HTTP 500 — server error"),
        "m2": [{"id": "r2", "text": "Вопрос", "username": "u2", "timestamp": "2026-09-01T10:00:00+0000"}],
    })
    llm_client = _FakeLLMClient(classify_results="question")

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=llm_client)

    assert result["status"] == "ok"
    assert sorted(write_client.calls) == ["m1", "m2"]  # both attempted — local failure doesn't abort the run
    assert result["processed"] == 1


def test_run_reply_triage_stops_on_auth_error_and_alerts(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.reply_triage.send_telegram_alert", alert_mock)
    _published_post(db_session, text="первый", threads_media_id="m1")
    _published_post(db_session, text="второй", threads_media_id="m2")
    write_client = _FakeWriteClient({
        "m1": ThreadsAPIError("get m1/replies failed: HTTP 403 — permission denied"),
        "m2": [{"id": "r2", "text": "never reached", "username": "u2", "timestamp": "2026-09-01T10:00:00+0000"}],
    })
    llm_client = _FakeLLMClient()

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=llm_client)

    assert result["status"] == "failed"
    assert len(write_client.calls) == 1  # never attempts the 2nd post — no retries
    alert_mock.assert_called_once()
    assert "403" in alert_mock.call_args[0][0]

    run = db_session.query(AgentRun).filter_by(agent="reply_triage").one()
    assert run.status == "failed"


def test_run_reply_triage_finishes_run_on_budget_exceeded(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.reply_triage.send_telegram_alert", alert_mock)
    _published_post(db_session)
    write_client = _FakeWriteClient({
        "m1": [{"id": "r1", "text": "Вопрос", "username": "u1", "timestamp": "2026-09-01T10:00:00+0000"}],
    })

    class _BudgetBustingLLMClient:
        def complete(self, role, messages, run_id=None, step_no=None):
            raise BudgetExceeded("month-to-date spend exceeded hard stop")

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=_BudgetBustingLLMClient())

    assert result["status"] == "budget_stop"
    run = db_session.query(AgentRun).filter_by(agent="reply_triage").one()
    assert run.status == "budget_stop"
    assert run.finished_at is not None
    alert_mock.assert_called_once()
    assert "budget" in alert_mock.call_args[0][0].lower()


def test_run_reply_triage_alerts_and_fails_cleanly_on_unexpected_error(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {})  # missing "reply_triage_lookback_days"
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.reply_triage.send_telegram_alert", alert_mock)
    _published_post(db_session)
    write_client = _FakeWriteClient({})
    llm_client = _FakeLLMClient()

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=llm_client)

    assert result["status"] == "failed"
    alert_mock.assert_called_once()
    assert "unexpected error" in alert_mock.call_args[0][0].lower()

    run = db_session.query(AgentRun).filter_by(agent="reply_triage").one()
    assert run.status == "failed"
    assert run.finished_at is not None


def test_run_reply_triage_lead_creates_reply_and_lead_and_alerts(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.reply_triage.send_telegram_alert", alert_mock)
    _published_post(db_session)
    write_client = _FakeWriteClient({
        "m1": [{"id": "r1", "text": "Хочу обсудить внедрение у нас", "username": "lead_user", "timestamp": "2026-09-01T10:00:00+0000", "permalink": "https://www.threads.net/@lead_user/post/r1"}],
    })
    llm_client = _FakeLLMClient(classify_results="lead")

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=llm_client)

    assert result["leads_found"] == 1
    reply = db_session.query(Reply).filter_by(threads_reply_id="r1").one()
    assert reply.kind == "lead"
    assert reply.status == "new"

    lead = db_session.query(Lead).filter_by(threads_username="lead_user").one()
    assert lead.status == "scored"
    assert lead.score is None

    alert_mock.assert_called_once()
    alert_text = alert_mock.call_args[0][0]
    assert "lead_user" in alert_text
    assert "Хочу обсудить внедрение у нас" in alert_text
    assert "https://www.threads.net/@lead_user/post/r1" in alert_text


def test_run_reply_triage_praise_and_spam_are_ignored_without_draft_or_alert(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.reply_triage.send_telegram_alert", alert_mock)
    _published_post(db_session, text="первый", threads_media_id="m1")
    _published_post(db_session, text="второй", threads_media_id="m2")
    write_client = _FakeWriteClient({
        "m1": [{"id": "r1", "text": "Огонь пост!", "username": "u1", "timestamp": "2026-09-01T10:00:00+0000"}],
        "m2": [{"id": "r2", "text": "buy followers now", "username": "u2", "timestamp": "2026-09-01T10:00:00+0000"}],
    })
    llm_client = _FakeLLMClient(classify_results=["praise", "spam"])

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=llm_client)

    assert result["processed"] == 2
    assert result["leads_found"] == 0
    assert llm_client.calls == ["classifier", "classifier"]  # commenter never called
    alert_mock.assert_not_called()

    rows = {r.threads_reply_id: r for r in db_session.query(Reply).all()}
    assert rows["r1"].status == "ignored" and rows["r1"].draft_response is None
    assert rows["r2"].status == "ignored" and rows["r2"].draft_response is None
    assert db_session.query(Lead).count() == 0


def test_run_reply_triage_classifier_label_with_trailing_text_is_still_recognized(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    _published_post(db_session)
    write_client = _FakeWriteClient({
        "m1": [{"id": "r1", "text": "Хочу обсудить внедрение", "username": "u1", "timestamp": "2026-09-01T10:00:00+0000"}],
    })
    llm_client = _FakeLLMClient(classify_results="это lead, отвечать нужно")

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=llm_client)

    assert result["leads_found"] == 1
    reply = db_session.query(Reply).filter_by(threads_reply_id="r1").one()
    assert reply.kind == "lead"


def test_run_reply_triage_unknown_classifier_label_falls_back_to_spam(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    _published_post(db_session)
    write_client = _FakeWriteClient({
        "m1": [{"id": "r1", "text": "???", "username": "u1", "timestamp": "2026-09-01T10:00:00+0000"}],
    })
    llm_client = _FakeLLMClient(classify_results="not_a_real_label")

    result = run_reply_triage(trigger="manual", write_client=write_client, llm_client=llm_client)

    assert result["status"] == "ok"  # the run itself still completes
    reply = db_session.query(Reply).filter_by(threads_reply_id="r1").one()
    assert reply.kind == "spam"
    assert reply.status == "ignored"
