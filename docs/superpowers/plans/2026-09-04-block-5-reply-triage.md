# Block 5: reply_triage (T5.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `reply_triage` — the deterministic (non-ReAct) pipeline that collects new replies under the bot's own published posts, classifies each into `question | objection | praise | spam | lead`, drafts a response for `question`/`objection`, and immediately alerts Telegram on `lead` — SPEC.md's Block 5 (T5.1), the highest-priority remaining agent per D11 (replying to people who already showed interest beats generating more cold posts).

**Architecture:** A single deterministic function (`run_reply_triage`) selects recently-published posts with a `threads_media_id`, calls a new `ThreadsWriteClient.get_replies(media_id)` per post, dedups against `replies.threads_reply_id`, classifies each new reply via `LLMClient` (role=`classifier`), and — depending on the label — drafts a response (role=`commenter`), creates a `leads` row, or just records the reply as `ignored`. It mirrors `run_feed_miner`'s and `publish_scheduled_posts`'s shape exactly: same trace-into-`agent_runs`/`agent_steps` pattern, same short `session_scope()`-per-step style, same dependency-injected clients for testability. `src/scheduler.py` gets a third interval job (every 3 hours).

**Tech Stack:** Same as Blocks 0–4 — Python 3.12, SQLAlchemy 2.x, pytest, `apscheduler`, existing `ThreadsWriteClient`/`LLMClient`/`src.alerts`.

**Spec:** [docs/superpowers/specs/2026-09-04-block-5-reply-triage-design.md](../specs/2026-09-04-block-5-reply-triage-design.md), which itself implements [SPEC.md](../../../SPEC.md) §6.4, §9, §11 Block 5 (T5.1).

## Global Constraints

- `reply_triage` is explicitly **not** a `ReActAgent` (SPEC.md §12, design doc §2) — a plain function, not `src/agents/base.py::ReActAgent`.
- Runs every 3 hours via an APScheduler **interval** trigger (SPEC.md §6.4: "каждые 3 часа") — unlike `feed_miner`'s two fixed cron times.
- `agent_runs.agent` value is `"reply_triage"` (matches SPEC.md §8's documented enum comment).
- Classification uses role `classifier`; draft generation uses role `commenter` — both already defined in `config/models.yaml`; `commenter` is currently unused anywhere else in the codebase.
- Dedup key is `replies.threads_reply_id` (unique, not null) — never insert a duplicate.
- Post selection: `status='published'`, non-null `threads_media_id`, `posted_at >= now - reply_triage_lookback_days days` — new `config/settings.yaml` key, default `30` (design doc §4.1).
- `get_replies` failing with an auth/permission signal (HTTP 401/403) stops the **whole run** immediately, no retries, Telegram alert (design doc §5, mirrors `feed_miner`'s `AuthError` handling). Any other failure of `get_replies` for one post is skipped — that step is marked `tool_ok=False`, the run continues to the next post.
- A classifier label outside `{question, objection, praise, spam, lead}` falls back to `spam` (safe default — never published, never alerts), and the step is marked `tool_ok=False`; the run continues (design doc §4.4).
- `question`/`objection` → `commenter` drafts a response; `Reply.draft_response` set, `Reply.status='pending_approval'`. `praise`/`spam` → `Reply.status='ignored'`, no draft, no alert. `lead` → `Reply.status='new'`, plus a new `leads` row (`status='scored'`, `score`/`score_reason` left null — that's `lead_scorer`'s job, Block 7, not started) and an immediate Telegram alert with the reply's text, author, and a link (design doc §4.5).
- The `commenter` prompt uses only `niche`/`tone_seed`/`never` from `get_knowledge_base(session)` directly — **not** the full 4-layer `assemble_system_prompt` (design doc §4.5, §7 — explicitly out of scope here).
- No DB schema changes — `replies` and `leads` already exist (design doc §7).
- `ThreadsWriteClient.get_replies` reuses the existing `_request()` retry/backoff on 429 — no new retry logic.

---

## File Structure

```
autonomous_threads/
├── config/
│   └── settings.yaml          # MODIFY — add reply_triage_lookback_days: 30
├── src/
│   ├── agents/
│   │   └── reply_triage.py    # NEW — run_reply_triage(), not a ReActAgent
│   ├── db/
│   │   └── repo.py            # MODIFY — append reply/lead helpers + post selector
│   ├── threads/
│   │   └── write_client.py    # MODIFY — add get_replies(media_id)
│   └── scheduler.py            # MODIFY — add the 3h interval job
└── tests/
    ├── db/
    │   └── test_repo_replies.py    # NEW
    ├── threads/
    │   └── test_write_client.py    # MODIFY — append get_replies tests
    ├── agents/
    │   └── test_reply_triage.py    # NEW
    └── test_scheduler.py            # MODIFY — append reply_triage job test
```

**Why this split:** `repo.py` and `write_client.py` get small, independently-testable additions first (Tasks 1–2) because `reply_triage.py` (Task 3) depends on both and is the one file complex enough to need the full happy/error-path test matrix from the design doc's §6. Scheduler wiring (Task 4) is last because it depends on `run_reply_triage` existing.

---

### Task 1: `repo.py` — reply/lead helpers + post selector

**Files:**
- Modify: `src/db/repo.py`
- Test: `tests/db/test_repo_replies.py`

**Interfaces:**
- Consumes: `Reply` model (`id, threads_reply_id (unique), post_id, author_username, text, kind, draft_response, status, received_at, responded_at`), `Lead` model (`id, threads_username, source_url, score, score_reason, status, created_at`), `Post` model (`status, threads_media_id, posted_at`) — all already imported at the top of `repo.py`; `insert_post` (already exists, used by tests).
- Produces: `reply_exists(session, threads_reply_id: str) -> bool`, `insert_reply(session, **fields) -> Reply`, `insert_lead(session, **fields) -> Lead`, `get_posts_for_reply_triage(session, since: datetime) -> list[Post]` — all consumed by Task 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_repo_replies.py
from datetime import datetime, timedelta, timezone

from src.db.repo import (
    get_posts_for_reply_triage,
    insert_lead,
    insert_post,
    insert_reply,
    reply_exists,
)


def test_reply_exists_false_then_true(db_session):
    assert reply_exists(db_session, "r1") is False

    insert_reply(db_session, threads_reply_id="r1", text="Как это работает?", kind="question", status="pending_approval")
    db_session.commit()

    assert reply_exists(db_session, "r1") is True
    assert reply_exists(db_session, "does-not-exist") is False


def test_insert_reply_returns_row_with_id(db_session):
    reply = insert_reply(db_session, threads_reply_id="r2", text="Спасибо, полезно!", kind="praise", status="ignored")
    db_session.commit()

    assert reply.id is not None
    assert reply.threads_reply_id == "r2"
    assert reply.kind == "praise"


def test_insert_lead_returns_row_with_id(db_session):
    lead = insert_lead(db_session, threads_username="user1", source_url="https://www.threads.net/@user1", status="scored")
    db_session.commit()

    assert lead.id is not None
    assert lead.threads_username == "user1"
    assert lead.status == "scored"
    assert lead.score is None


def test_get_posts_for_reply_triage_filters_by_status_media_id_and_recency(db_session):
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=30)

    in_range = insert_post(db_session, text="in range", category="educational", status="published", threads_media_id="m1", posted_at=now - timedelta(days=1))
    insert_post(db_session, text="too old", category="educational", status="published", threads_media_id="m2", posted_at=now - timedelta(days=60))
    insert_post(db_session, text="draft", category="educational", status="draft", threads_media_id="m3", posted_at=now - timedelta(days=1))
    insert_post(db_session, text="no media id", category="educational", status="published", posted_at=now - timedelta(days=1))
    db_session.commit()

    posts = get_posts_for_reply_triage(db_session, since)

    assert [p.id for p in posts] == [in_range.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_repo_replies.py -v`
Expected: FAIL with `ImportError: cannot import name 'reply_exists'`

- [ ] **Step 3: Append to `src/db/repo.py`**

`Reply`, `Lead`, `Post`, and `datetime`/`timezone` are already imported at the top of the file — no import changes needed. Add these four functions at the end of the file:

```python
def reply_exists(session: Session, threads_reply_id: str) -> bool:
    return session.execute(
        select(Reply.id).where(Reply.threads_reply_id == threads_reply_id)
    ).scalar_one_or_none() is not None


def insert_reply(session: Session, **fields) -> Reply:
    reply = Reply(**fields)
    session.add(reply)
    session.flush()
    return reply


def insert_lead(session: Session, **fields) -> Lead:
    lead = Lead(**fields)
    session.add(lead)
    session.flush()
    return lead


def get_posts_for_reply_triage(session: Session, since: datetime) -> list[Post]:
    return list(session.execute(
        select(Post)
        .where(Post.status == "published", Post.threads_media_id.is_not(None), Post.posted_at >= since)
        .order_by(Post.posted_at.desc())
    ).scalars().all())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/db/test_repo_replies.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all previously-passing tests still pass, plus the 4 new ones.

- [ ] **Step 6: Commit**

```bash
git add src/db/repo.py tests/db/test_repo_replies.py
git commit -m "feat: add reply/lead repo helpers and reply_triage post selector"
```

---

### Task 2: `ThreadsWriteClient.get_replies`

**Files:**
- Modify: `src/threads/write_client.py`
- Test: `tests/threads/test_write_client.py`

**Interfaces:**
- Consumes: `self._request(method, path, **kwargs) -> dict` (existing, handles retry/backoff on 429 and raises `ThreadsAPIError` on 4xx/5xx after retries).
- Produces: `get_replies(self, media_id: str) -> list[dict]` — each dict has (at least) `id`, `text`, `username`, `timestamp`; `permalink` is also requested (not in the design doc's literal 4-field example, but a real Graph API field for reply objects) so Task 3 can build a lead's `source_url` without a second call — falls back to a profile-URL guess if the API omits it. Consumed by Task 3.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/threads/test_write_client.py

def test_get_replies_returns_data_list(client):
    with patch("src.threads.write_client.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [
                {"id": "r1", "text": "Как это работает?", "username": "u1", "timestamp": "2026-09-01T10:00:00+0000"},
            ]},
        )
        replies = client.get_replies("media-1")

    assert replies == [{"id": "r1", "text": "Как это работает?", "username": "u1", "timestamp": "2026-09-01T10:00:00+0000"}]
    args, kwargs = mock_get.call_args
    assert args[0] == "https://graph.threads.net/v1.0/media-1/replies"
    assert kwargs["params"]["fields"] == "id,text,username,timestamp,permalink"


def test_get_replies_returns_empty_list_when_no_data_key(client):
    with patch("src.threads.write_client.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {})
        assert client.get_replies("media-1") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/threads/test_write_client.py -v`
Expected: FAIL with `AttributeError: 'ThreadsWriteClient' object has no attribute 'get_replies'`

- [ ] **Step 3: Add to `src/threads/write_client.py`**

Add this method to `ThreadsWriteClient`, e.g. right after `get_media_insights`:

```python
    def get_replies(self, media_id: str) -> list[dict]:
        """GET /{media_id}/replies — replies under the caller's own post.
        Official API (SPEC.md §4: publish/replies/insights go through the
        API, not the browser). The exact response shape isn't verified
        against live data anywhere in this codebase (same caveat as
        check_publishing_limit(kind="replies")) — confirm field names
        during a live smoke test and adjust here if they differ."""
        data = self._request(
            "get", f"{media_id}/replies",
            params={"fields": "id,text,username,timestamp,permalink"},
        )
        return data.get("data", [])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/threads/test_write_client.py -v`
Expected: PASS (6/6)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/threads/write_client.py tests/threads/test_write_client.py
git commit -m "feat: add ThreadsWriteClient.get_replies"
```

---

### Task 3: `src/agents/reply_triage.py` — deterministic triage pipeline

**Files:**
- Create: `src/agents/reply_triage.py`
- Modify: `config/settings.yaml` (add `reply_triage_lookback_days: 30`)
- Test: `tests/agents/test_reply_triage.py`

**Interfaces:**
- Consumes: `ThreadsWriteClient.get_replies(media_id) -> list[dict]` raising `ThreadsAPIError` (Task 2); `LLMClient.complete(role, messages, run_id=None, step_no=None) -> LLMResponse` raising `BudgetExceeded` (`src/llm/client.py`, existing); `reply_exists`/`insert_reply`/`insert_lead`/`get_posts_for_reply_triage` (Task 1); `get_knowledge_base(session) -> dict` (existing, `src/db/repo.py`); `start_agent_run`/`add_agent_step`/`finish_agent_run` (existing); `send_telegram_alert` (existing, `src/alerts.py`); `load_settings()["reply_triage_lookback_days"]` (`src/config.py`, this task adds the key).
- Produces: `run_reply_triage(trigger: str = "cron", write_client: ThreadsWriteClient | None = None, llm_client: LLMClient | None = None) -> dict` returning `{"processed": int, "skipped_dupes": int, "leads_found": int, "status": str}` — consumed by Task 4 (scheduler wiring). `write_client`/`llm_client` default to real instances when omitted (mirrors `publish_scheduled_posts`'s `os.environ["THREADS_ACCESS_TOKEN"]`/`os.environ["THREADS_USER_ID"]` construction); tests inject fakes.

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_reply_triage.py
from unittest.mock import MagicMock

from src.agents.reply_triage import run_reply_triage
from src.db.models import AgentRun, Lead, Reply
from src.db.repo import insert_post, insert_reply
from src.llm.client import BudgetExceeded, LLMResponse
from src.threads.write_client import ThreadsAPIError


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
    fields = dict(text="Мой пост про автоматизацию", category="educational", status="published", threads_media_id="m1")
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

    assert result == {"processed": 1, "skipped_dupes": 0, "leads_found": 0, "status": "ok"}
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

    assert result == {"processed": 0, "skipped_dupes": 1, "leads_found": 0, "status": "ok"}
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

    assert result == {"processed": 1, "skipped_dupes": 0, "leads_found": 0, "status": "ok"}
    rows = db_session.query(Reply).all()
    assert [r.threads_reply_id for r in rows] == ["r2"]


def test_run_reply_triage_local_get_replies_failure_skips_post_and_continues(db_session, monkeypatch):
    monkeypatch.setattr("src.agents.reply_triage.load_settings", lambda: {"reply_triage_lookback_days": 30})
    _published_post(db_session, text="первый пост", threads_media_id="m1", posted_at=None)
    _published_post(db_session, text="второй пост", threads_media_id="m2", posted_at=None)
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
    _published_post(db_session, text="первый", threads_media_id="m1", posted_at=None)
    _published_post(db_session, text="второй", threads_media_id="m2", posted_at=None)
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
    _published_post(db_session, text="первый", threads_media_id="m1", posted_at=None)
    _published_post(db_session, text="второй", threads_media_id="m2", posted_at=None)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_reply_triage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agents.reply_triage'`

- [ ] **Step 3: Write minimal implementation**

First, add the lookback-days key to `config/settings.yaml` (anywhere at the top level, e.g. right after `max_post_age_days: 3`):

```yaml
reply_triage_lookback_days: 30
```

Then create `src/agents/reply_triage.py`:

```python
import os
from datetime import datetime, timedelta, timezone

from src.alerts import send_telegram_alert
from src.config import load_settings
from src.db.engine import session_scope
from src.db.repo import (
    add_agent_step,
    finish_agent_run,
    get_knowledge_base,
    get_posts_for_reply_triage,
    insert_lead,
    insert_reply,
    reply_exists,
    start_agent_run,
)
from src.llm.client import BudgetExceeded, LLMClient
from src.threads.write_client import ThreadsAPIError, ThreadsWriteClient

VALID_KINDS = {"question", "objection", "praise", "spam", "lead"}
DRAFT_KINDS = {"question", "objection"}

CLASSIFIER_PROMPT = (
    "Классифицируй комментарий под постом в Threads ровно одной меткой: "
    "question, objection, praise, spam или lead.\n"
    "question — задаёт вопрос по теме поста.\n"
    "objection — возражение или сомнение по теме поста.\n"
    "praise — похвала без вопроса и без возражения.\n"
    "spam — реклама, оффтоп, боты.\n"
    "lead — явный интерес к продукту или услуге, готовность обсудить сотрудничество.\n"
    "Ответь только меткой, без пояснений.\n\n"
    "Пост:\n{post_text}\n\nКомментарий:\n{reply_text}"
)

COMMENTER_PROMPT = (
    "Ниша: {niche}. Тон: {tone_seed}. Никогда: {never}.\n"
    "Напиши короткий черновик ответа на комментарий под своим постом в Threads. "
    "Без приветствий, сразу по делу, на русском языке.\n\n"
    "Свой пост:\n{post_text}\n\nКомментарий (автор {author}):\n{reply_text}"
)


def _is_auth_error(exc: ThreadsAPIError) -> bool:
    message = str(exc)
    return "HTTP 401" in message or "HTTP 403" in message


def _parse_timestamp(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _classify(llm_client: LLMClient, post_text: str, reply_text: str, run_id: int, step_no: int):
    response = llm_client.complete(
        role="classifier",
        messages=[{
            "role": "user",
            "content": CLASSIFIER_PROMPT.format(post_text=post_text, reply_text=reply_text),
        }],
        run_id=run_id,
        step_no=step_no,
    )
    raw_kind = response.text.strip().lower()
    was_valid = raw_kind in VALID_KINDS
    return (raw_kind if was_valid else "spam"), was_valid, response


def _draft_response(llm_client: LLMClient, kb: dict, post_text: str, reply_text: str, author: str, run_id: int, step_no: int):
    prompt = COMMENTER_PROMPT.format(
        niche=kb.get("niche", ""),
        tone_seed=kb.get("tone_seed", ""),
        never=kb.get("never", ""),
        post_text=post_text,
        reply_text=reply_text,
        author=author,
    )
    return llm_client.complete(
        role="commenter",
        messages=[{"role": "user", "content": prompt}],
        run_id=run_id,
        step_no=step_no,
    )


def run_reply_triage(
    trigger: str = "cron",
    write_client: ThreadsWriteClient | None = None,
    llm_client: LLMClient | None = None,
) -> dict:
    """Deterministic pipeline — NOT a ReActAgent (SPEC.md §12, mirrors
    feed_miner/publisher). Traced into agent_runs/agent_steps like the
    ReAct agents so the dashboard doesn't need a special case."""
    write_client = write_client or ThreadsWriteClient(os.environ["THREADS_ACCESS_TOKEN"], os.environ["THREADS_USER_ID"])
    llm_client = llm_client or LLMClient()

    with session_scope() as session:
        run = start_agent_run(session, agent="reply_triage", trigger=trigger)
        run_id = run.id

    processed = 0
    skipped_dupes = 0
    leads_found = 0
    step_no = 0
    status = "ok"
    error = None
    tokens_in = 0
    tokens_out = 0
    cost_usd = 0.0

    try:
        lookback_days = load_settings()["reply_triage_lookback_days"]
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        with session_scope() as session:
            posts = [(p.id, p.threads_media_id, p.text) for p in get_posts_for_reply_triage(session, since)]

        for post_id, media_id, post_text in posts:
            step_no += 1
            tool_ok = True
            tool_result = None
            try:
                raw_replies = write_client.get_replies(media_id)
                for raw in raw_replies:
                    text = raw.get("text")
                    author = raw.get("username")
                    threads_reply_id = raw.get("id")
                    if not text or not author or not threads_reply_id:
                        continue  # malformed reply — skip it, don't abort the run

                    with session_scope() as session:
                        already_seen = reply_exists(session, threads_reply_id)
                    if already_seen:
                        skipped_dupes += 1
                        continue

                    kind, was_valid, classification = _classify(llm_client, post_text, text, run_id, step_no)
                    if not was_valid:
                        tool_ok = False
                    tokens_in += classification.tokens_in
                    tokens_out += classification.tokens_out
                    cost_usd += classification.cost_usd

                    draft_response = None
                    reply_status = "ignored"
                    if kind in DRAFT_KINDS:
                        with session_scope() as session:
                            kb = get_knowledge_base(session)
                        draft = _draft_response(llm_client, kb, post_text, text, author, run_id, step_no)
                        tokens_in += draft.tokens_in
                        tokens_out += draft.tokens_out
                        cost_usd += draft.cost_usd
                        draft_response = draft.text
                        reply_status = "pending_approval"
                    elif kind == "lead":
                        reply_status = "new"

                    with session_scope() as session:
                        insert_reply(
                            session,
                            threads_reply_id=threads_reply_id,
                            post_id=post_id,
                            author_username=author,
                            text=text,
                            kind=kind,
                            draft_response=draft_response,
                            status=reply_status,
                            received_at=_parse_timestamp(raw.get("timestamp")),
                        )

                    if kind == "lead":
                        source_url = raw.get("permalink") or f"https://www.threads.net/@{author}"
                        with session_scope() as session:
                            insert_lead(session, threads_username=author, source_url=source_url, status="scored")
                        leads_found += 1
                        send_telegram_alert(f"reply_triage: новый лид от @{author} — {text}\n{source_url}")

                    processed += 1
                tool_result = {"media_id": media_id, "replies_found": len(raw_replies)}
            except ThreadsAPIError as exc:
                tool_ok = False
                tool_result = str(exc)
                if _is_auth_error(exc):
                    status = "failed"
                    error = str(exc)
                    send_telegram_alert(f"reply_triage stopped: {exc}")

            with session_scope() as session:
                add_agent_step(
                    session,
                    run_id=run_id,
                    step_no=step_no,
                    tool_name="get_replies",
                    tool_args={"media_id": media_id},
                    tool_result=tool_result,
                    tool_ok=tool_ok,
                )

            if status == "failed":
                break
    except BudgetExceeded as exc:
        status = "budget_stop"
        error = str(exc)
        send_telegram_alert(f"reply_triage stopped (budget exceeded): {exc}")
    except Exception as exc:  # noqa: BLE001 — recorded, not swallowed silently
        status = "failed"
        error = str(exc)
        send_telegram_alert(f"reply_triage stopped (unexpected error): {exc}")

    with session_scope() as session:
        finish_agent_run(
            session,
            run_id,
            status=status,
            steps_count=step_no,
            error=error,
            output_ref=f"processed={processed} skipped_dupes={skipped_dupes} leads_found={leads_found}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )

    return {"processed": processed, "skipped_dupes": skipped_dupes, "leads_found": leads_found, "status": status}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_reply_triage.py -v`
Expected: PASS (10/10)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add config/settings.yaml src/agents/reply_triage.py tests/agents/test_reply_triage.py
git commit -m "feat: add reply_triage deterministic pipeline with classification, drafts, and lead alerting"
```

---

### Task 4: Wire `reply_triage` into the scheduler (every 3 hours)

**Files:**
- Modify: `src/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `run_reply_triage` (Task 3).
- Produces: nothing new — extends `build_scheduler()`'s existing job list.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_scheduler.py

def test_build_scheduler_registers_reply_triage_job():
    scheduler = build_scheduler()
    jobs = {j.id: j for j in scheduler.get_jobs()}

    assert "reply_triage_every_3h" in jobs
    job = jobs["reply_triage_every_3h"]
    assert job.func.__name__ == "run_reply_triage"
    assert job.trigger.interval.total_seconds() == 3 * 3600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL with `AssertionError: 'reply_triage_every_3h' not in jobs` (or `KeyError`)

- [ ] **Step 3: Modify `src/scheduler.py`**

Add the import next to the existing agent imports:

```python
from src.agents.reply_triage import run_reply_triage
```

Add the job inside `build_scheduler()`, after the `publisher_every_10_min` job:

```python
    scheduler.add_job(
        run_reply_triage, trigger="interval", hours=3,
        id="reply_triage_every_3h", kwargs={"trigger": "cron"},
    )
```

Update the startup print in `main()` to mention it:

```python
    print(f"worker started — feed_miner 08:00/20:00, content_agent hourly, publisher every 10min, reply_triage every 3h ({TIMEZONE})")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scheduler.py -v`
Expected: PASS (all scheduler tests, including the new one)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: schedule reply_triage every 3 hours via APScheduler"
```

---

## Self-Review Notes

- **Spec coverage:** design doc §2 (architecture, function signature) → Task 3. §3 (`get_replies`) → Task 2. §4.1 (post selection) → Task 1's `get_posts_for_reply_triage` + Task 3's `reply_triage_lookback_days` read. §4.2–4.3 (fetch + dedup) → Task 3's main loop + Task 1's `reply_exists`. §4.4 (classification + fallback) → Task 3's `_classify`. §4.5 (draft/ignore/lead branching) → Task 3's main loop. §4.6 (`insert_reply`) → Task 1 + Task 3. §5 (error table) → Task 3's `ThreadsAPIError`/`_is_auth_error`/`BudgetExceeded`/generic-exception handling, each with a dedicated test. §6 (test list) → every case is a test function in Task 3's test file. §7 (out of scope: T5.2, full prompt assembler, `lead_scorer`, schema changes) → nothing in this plan touches any of them.
- **Acceptance criterion needing live credentials:** the design doc's own caveat about `get_replies`'s unverified response shape (§3) can't be resolved without a real Graph API call against a live app with `threads_manage_replies` scope — same class of manual follow-up as `check_publishing_limit(kind="replies")`'s existing caveat. Flag it for a live smoke test once T0.1's scopes are approved; not a task failure here.
- **No placeholders:** every step has runnable code.
- **Type consistency checked:** `run_reply_triage`'s return shape (`{"processed", "skipped_dupes", "leads_found", "status"}`) is used consistently across all 10 tests in Task 3. `get_replies`'s return shape (list of dicts with `id`/`text`/`username`/`timestamp`/`permalink`) matches what Task 3's fakes produce and what the real implementation requests. `reply_exists`/`insert_reply`/`insert_lead`/`get_posts_for_reply_triage` signatures match between Task 1's definitions, Task 1's tests, and Task 3's usage.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-04-block-5-reply-triage.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
