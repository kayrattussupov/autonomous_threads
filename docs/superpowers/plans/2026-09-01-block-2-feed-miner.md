# Block 2: feed_miner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `feed_miner` — the deterministic (non-ReAct) daily collector that mines the Threads niche via keyword search, classifies each post's topic, dedups against `swipe_file`, and writes new posts in — SPEC.md's Block 2 (T2.1), the highest-priority new agent per D10 (own posts are too few to learn from; the swipe file is the dense signal).

**Architecture:** A single deterministic function (`run_feed_miner`) loops over `config/settings.yaml`'s `search_groups`, calls the existing `ThreadsReadClient.search_keyword()` per keyword, derives a stable post id from the URL (or a text hash fallback), skips posts already in `swipe_file`, classifies new ones via `LLMClient` (role=`classifier`), and inserts them. Every run is traced into `agent_runs`/`agent_steps` exactly like the ReAct agents, so the future dashboard (Block 4) doesn't need a special case for non-ReAct agents. A new minimal `src/alerts.py` gives it (and future blocks) a real Telegram alert channel — SPEC.md §4/§9 require "auth error → alert in Telegram," and nothing in the codebase sends one yet. `src/scheduler.py` (currently a no-op loop) is wired up with APScheduler to run it twice a day.

**Tech Stack:** Same as Block 0/1 — Python 3.12, SQLAlchemy 2.x, pytest, `requests` (for the Telegram Bot API), `apscheduler` (already in `requirements.txt`, unused until now).

**Spec:** [SPEC.md](../../../SPEC.md) — §4 (auth-error alerting), §6.2 (`feed_miner`'s exact logic), §8 (`swipe_file` DDL), §9 (safety table), §11 Block 2 (T2.1).

## Global Constraints

- `feed_miner` is explicitly **not** a `ReActAgent` — SPEC.md §12: "ReAct в `feed_miner`... — Пайплайны жёсткие. Агентность там — стоимость без свободы решения." It's a plain function, not a subclass of `src/agents/base.py::ReActAgent`.
- Dedup key is `swipe_file.threads_post_id` (unique, not null) — never insert a duplicate.
- Any `AuthError`/`DailyViewCapExceeded` from `ThreadsReadClient` must stop the run immediately, with **no retries** (SPEC.md §4) — and now, for the first time in this codebase, actually send a Telegram alert, not just raise.
- Runs twice daily ("утро и вечер" — SPEC.md §6.2); no exact times are specified in the spec, so 08:00 and 20:00 `Asia/Almaty` are used (same timezone SPEC.md §6.5 already uses for `analyst_agent`'s cron).
- `agent_runs.agent` value for this component is `"feed_miner"` (matches the `TEXT` column's documented enum in SPEC.md §8: `content | analyst | feed_miner | reply_triage`).
- Classification uses the `classifier` role from `config/models.yaml` (already `glm-4.7-flash`, free tier) — never a different role, never a hardcoded model name.
- `ThreadsReadClient`'s own daily view cap and jitter (already built, Block 1) are not duplicated here — `feed_miner` just calls `search_keyword()` and handles what it raises.

---

## File Structure

```
autonomous_threads/
├── src/
│   ├── alerts.py              # NEW — send_telegram_alert()
│   ├── agents/
│   │   └── feed_miner.py      # NEW — run_feed_miner(), not a ReActAgent
│   ├── db/
│   │   └── repo.py            # MODIFY — append swipe_file helpers
│   └── scheduler.py           # MODIFY — replace no-op loop with APScheduler, 2 daily jobs
└── tests/
    ├── test_alerts.py         # NEW
    ├── agents/
    │   └── test_feed_miner.py # NEW
    └── test_scheduler.py      # NEW
```

**Why this split:** `alerts.py` is a tiny, single-purpose, side-effecting module (network call to Telegram) kept separate so it can be mocked trivially and reused by later blocks (T5.2's lead alerts, budget-stop alerts) without pulling in `feed_miner`'s logic. `feed_miner.py` lives under `src/agents/` (matching SPEC.md §3's repo layout, which lists it alongside `base.py`/`content.py`/etc.) even though it isn't a `ReActAgent` — it's still "an agent" in the domain sense, just a deterministic one, per §6.2's own heading ("`feed_miner` — не ReAct, детерминированный").

---

### Task 1: `src/alerts.py` — Telegram alert helper

**Files:**
- Create: `src/alerts.py`
- Test: `tests/test_alerts.py`

**Interfaces:**
- Consumes: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` env vars (already declared in `.env.example` since Task 1 of Block 0/1, previously unused).
- Produces: `send_telegram_alert(text: str) -> bool` — consumed by Task 3 (`feed_miner`) and, in future blocks, by budget-stop and lead alerts. Never raises — a failed alert must not crash the caller (an agent that can't reach Telegram should still finish its run and record its own status).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alerts.py
from unittest.mock import MagicMock, patch

from src.alerts import send_telegram_alert


def test_send_telegram_alert_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("src.alerts.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        result = send_telegram_alert("Auth error in feed_miner")

    assert result is True
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.telegram.org/bottest-token/sendMessage"
    assert kwargs["json"] == {"chat_id": "12345", "text": "Auth error in feed_miner"}


def test_send_telegram_alert_returns_false_on_non_200(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("src.alerts.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
        result = send_telegram_alert("test")

    assert result is False


def test_send_telegram_alert_returns_false_on_network_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("src.alerts.requests.post", side_effect=ConnectionError("no network")):
        result = send_telegram_alert("test")

    assert result is False


def test_send_telegram_alert_returns_false_when_not_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    result = send_telegram_alert("test")

    assert result is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_alerts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.alerts'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/alerts.py
import logging
import os

import requests

logger = logging.getLogger(__name__)


def send_telegram_alert(text: str) -> bool:
    """Best-effort Telegram alert. Never raises — a broken alert channel
    must not crash the agent that's trying to report a problem."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Telegram alert skipped (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set): %s", text)
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except requests.RequestException:
        logger.exception("Telegram alert failed to send: %s", text)
        return False

    if resp.status_code != 200:
        logger.error("Telegram alert rejected (HTTP %s): %s", resp.status_code, text)
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_alerts.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add src/alerts.py tests/test_alerts.py
git commit -m "feat: add Telegram alert helper"
```

---

### Task 2: `swipe_file` repo helpers

**Files:**
- Modify: `src/db/repo.py`
- Test: `tests/db/test_repo_swipe_file.py`

**Interfaces:**
- Consumes: `SwipeFilePost` model (`src/db/models.py`, already exists: `id, threads_post_id (unique), text, author, views, likes, replies, topic, collected_at`).
- Produces: `swipe_file_post_exists(session, threads_post_id: str) -> bool` and `insert_swipe_file_post(session, **fields) -> SwipeFilePost` — consumed by Task 3.

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_repo_swipe_file.py
from src.db.repo import insert_swipe_file_post, swipe_file_post_exists


def test_swipe_file_post_exists_false_then_true(db_session):
    assert swipe_file_post_exists(db_session, "abc123") is False

    insert_swipe_file_post(db_session, threads_post_id="abc123", text="Пример поста", topic="автоматизация")
    db_session.commit()

    assert swipe_file_post_exists(db_session, "abc123") is True
    assert swipe_file_post_exists(db_session, "does-not-exist") is False


def test_insert_swipe_file_post_returns_row_with_id(db_session):
    post = insert_swipe_file_post(db_session, threads_post_id="xyz789", text="Другой пост", topic="маркетинг")
    db_session.commit()

    assert post.id is not None
    assert post.threads_post_id == "xyz789"
    assert post.topic == "маркетинг"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_repo_swipe_file.py -v`
Expected: FAIL with `ImportError: cannot import name 'insert_swipe_file_post'`

- [ ] **Step 3: Append to `src/db/repo.py`**

Add `SwipeFilePost` to the existing `from src.db.models import ...` line at the top of the file (append it to the existing import, don't duplicate the import statement), then add these two functions at the end of the file:

```python
def swipe_file_post_exists(session: Session, threads_post_id: str) -> bool:
    return session.execute(
        select(SwipeFilePost.id).where(SwipeFilePost.threads_post_id == threads_post_id)
    ).scalar_one_or_none() is not None


def insert_swipe_file_post(session: Session, **fields) -> SwipeFilePost:
    post = SwipeFilePost(**fields)
    session.add(post)
    session.flush()
    return post
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/db/test_repo_swipe_file.py -v`
Expected: PASS (2/2)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all previously-passing tests still pass, plus the 2 new ones (18 total).

- [ ] **Step 6: Commit**

```bash
git add src/db/repo.py tests/db/test_repo_swipe_file.py
git commit -m "feat: add swipe_file dedup/insert repo helpers"
```

---

### Task 3: `src/agents/feed_miner.py` — deterministic collector

**Files:**
- Create: `src/agents/feed_miner.py`
- Test: `tests/agents/test_feed_miner.py`

**Interfaces:**
- Consumes: `ThreadsReadClient.search_keyword(keyword: str, scroll_times: int = 5) -> list[dict]` (each dict: `{"keyword", "text", "url"}`) raising `AuthError`/`DailyViewCapExceeded` (`src/threads/read_client.py`, Block 1); `LLMClient.complete(role: str, messages: list[dict], run_id=None, step_no=None) -> LLMResponse` with `.text` (`src/llm/client.py`, Block 1); `start_agent_run`/`add_agent_step`/`finish_agent_run` (`src/db/repo.py`, Block 1); `swipe_file_post_exists`/`insert_swipe_file_post` (Task 2); `send_telegram_alert` (Task 1); `load_settings()` (`src/config.py`, Block 1) for `search_groups`.
- Produces: `run_feed_miner(trigger: str = "cron", read_client: "ThreadsReadClient | None" = None, llm_client: "LLMClient | None" = None) -> dict` returning `{"collected": int, "skipped_dupes": int, "status": str}` — consumed by Task 4 (scheduler wiring). The `read_client`/`llm_client` params default to real instances when omitted; tests inject fakes.

- [ ] **Step 1: Write the failing test**

```python
# tests/agents/test_feed_miner.py
from unittest.mock import MagicMock

import pytest

from src.agents.feed_miner import run_feed_miner
from src.db.models import AgentRun, SwipeFilePost
from src.llm.client import LLMResponse
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_feed_miner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agents.feed_miner'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agents/feed_miner.py
import hashlib
import re

from src.alerts import send_telegram_alert
from src.config import load_settings
from src.db.engine import session_scope
from src.db.repo import (
    add_agent_step,
    finish_agent_run,
    insert_swipe_file_post,
    start_agent_run,
    swipe_file_post_exists,
)
from src.llm.client import LLMClient
from src.threads.read_client import AuthError, DailyViewCapExceeded, ThreadsReadClient

_POST_URL_ID_RE = re.compile(r"/post/([A-Za-z0-9_-]+)")

CLASSIFIER_PROMPT = (
    "Классифицируй тему поста в Threads одним-двумя словами на русском "
    "(например: автоматизация, маркетинг, найм, продажи, личный_бренд, другое). "
    "Ответь только темой, без пояснений.\n\nПост:\n{text}"
)


def _derive_post_id(url: str, text: str) -> str:
    """Stable id for dedup: prefer the id embedded in the post URL, fall
    back to a text hash when the URL doesn't contain one. Deliberately
    reimplemented here rather than importing threads_app's own
    make_post_id, to keep src/threads/read_client.py's lazy-import
    boundary the only place this codebase touches threads_app internals."""
    match = _POST_URL_ID_RE.search(url or "")
    if match:
        return match.group(1)
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _classify_topic(llm_client: LLMClient, text: str, run_id: int, step_no: int) -> str:
    response = llm_client.complete(
        role="classifier",
        messages=[{"role": "user", "content": CLASSIFIER_PROMPT.format(text=text)}],
        run_id=run_id,
        step_no=step_no,
    )
    return response.text.strip()


def run_feed_miner(
    trigger: str = "cron",
    read_client: ThreadsReadClient | None = None,
    llm_client: LLMClient | None = None,
) -> dict:
    """Deterministic collector — NOT a ReActAgent (SPEC.md §12: pipelines
    here are rigid, agency would be cost without decision freedom). Still
    traced into agent_runs/agent_steps like the ReAct agents so the
    dashboard doesn't need a special case."""
    read_client = read_client or ThreadsReadClient()
    llm_client = llm_client or LLMClient()

    with session_scope() as session:
        run = start_agent_run(session, agent="feed_miner", trigger=trigger)
        run_id = run.id

    collected = 0
    skipped_dupes = 0
    step_no = 0
    status = "ok"
    error = None

    search_groups = load_settings()["search_groups"]

    for group in search_groups:
        if status == "failed":
            break
        for keyword in group["keywords"]:
            step_no += 1
            tool_ok = True
            tool_result = None
            try:
                posts = read_client.search_keyword(keyword)
                for post in posts:
                    post_id = _derive_post_id(post["url"], post["text"])
                    with session_scope() as session:
                        already_seen = swipe_file_post_exists(session, post_id)
                    if already_seen:
                        skipped_dupes += 1
                        continue
                    topic = _classify_topic(llm_client, post["text"], run_id, step_no)
                    with session_scope() as session:
                        insert_swipe_file_post(
                            session,
                            threads_post_id=post_id,
                            text=post["text"],
                            topic=topic,
                        )
                    collected += 1
                tool_result = {"keyword": keyword, "posts_found": len(posts)}
            except (AuthError, DailyViewCapExceeded) as exc:
                tool_ok = False
                tool_result = str(exc)
                status = "failed"
                error = str(exc)
                send_telegram_alert(f"feed_miner stopped: {exc}")

            with session_scope() as session:
                add_agent_step(
                    session,
                    run_id=run_id,
                    step_no=step_no,
                    tool_name="search_keyword",
                    tool_args={"keyword": keyword},
                    tool_result=tool_result,
                    tool_ok=tool_ok,
                )

            if status == "failed":
                break

    with session_scope() as session:
        finish_agent_run(
            session,
            run_id,
            status=status,
            steps_count=step_no,
            error=error,
            output_ref=f"collected={collected} skipped_dupes={skipped_dupes}",
        )

    return {"collected": collected, "skipped_dupes": skipped_dupes, "status": status}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_feed_miner.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all tests pass (22 total: 16 from Block 0/1 + 2 from Task 2 + 4 from this task).

- [ ] **Step 6: Commit**

```bash
git add src/agents/feed_miner.py tests/agents/test_feed_miner.py
git commit -m "feat: add feed_miner deterministic collector with dedup, classification, alerting"
```

---

### Task 4: Wire `feed_miner` into the scheduler (2 runs/day)

**Files:**
- Modify: `src/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `run_feed_miner` (Task 3).
- Produces: `build_scheduler() -> BackgroundScheduler` — a constructor function separated from `main()` specifically so tests can inspect the configured jobs without starting the blocking loop.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler.py
from src.scheduler import build_scheduler


def test_build_scheduler_registers_two_daily_feed_miner_jobs():
    scheduler = build_scheduler()
    jobs = scheduler.get_jobs()

    feed_miner_jobs = [j for j in jobs if j.id.startswith("feed_miner")]
    assert len(feed_miner_jobs) == 2

    hours = sorted(trigger_hour(job) for job in feed_miner_jobs)
    assert hours == [8, 20]

    for job in feed_miner_jobs:
        assert job.func.__name__ == "run_feed_miner"


def trigger_hour(job) -> int:
    # APScheduler CronTrigger stores its fields as a list; find the "hour" field.
    for field in job.trigger.fields:
        if field.name == "hour":
            return int(str(field))
    raise AssertionError(f"no hour field on trigger {job.trigger}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_scheduler'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/scheduler.py
import time

from apscheduler.schedulers.background import BackgroundScheduler

from src.agents.feed_miner import run_feed_miner

TIMEZONE = "Asia/Almaty"


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        run_feed_miner,
        trigger="cron",
        hour=8,
        minute=0,
        id="feed_miner_morning",
        kwargs={"trigger": "cron"},
    )
    scheduler.add_job(
        run_feed_miner,
        trigger="cron",
        hour=20,
        minute=0,
        id="feed_miner_evening",
        kwargs={"trigger": "cron"},
    )
    return scheduler


def main():
    scheduler = build_scheduler()
    scheduler.start()
    print(f"worker started — feed_miner scheduled 08:00/20:00 {TIMEZONE}")
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scheduler.py -v`
Expected: PASS (1/1)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all tests pass (23 total).

- [ ] **Step 6: Verify the worker container still starts cleanly**

```bash
docker compose up -d --build worker
docker compose logs worker
```

Expected: log line `worker started — feed_miner scheduled 08:00/20:00 Asia/Almaty`, no crash (confirms `apscheduler` is actually installed in the image — it's already in `requirements.txt` from Block 0/1's Task 1, so this should just work, but the previous block's final review found one Docker-import gap already, so don't skip this check).

- [ ] **Step 7: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: schedule feed_miner twice daily via APScheduler"
```

---

## Self-Review Notes

- **Spec coverage:** T2.1's full description (§6.2) is covered: ThreadsReadClient-based collection (Task 3), classifier-role topic classification (Task 3), dedup by `threads_post_id` (Task 2+3), `search_groups` from `settings.yaml` (Task 3), 2 runs/day (Task 4). The auth-error-alerts-to-Telegram requirement from §4/§9 — previously declared in `.env.example` but never implemented anywhere in the codebase — is now real (Task 1), closing a gap the Block 0/1 final review flagged as unconsumed.
- **Acceptance criterion needing live credentials:** "два прогона в сутки дают 60–100 уникальных постов, дублей нет" can only be confirmed by actually running against the real Threads app and observing `swipe_file` row counts over 24h — not verifiable in a sandboxed environment without `THREADS_APP_PATH` pointing at a working, logged-in `threads_app` checkout. This is the same class of manual follow-up as Block 0/1's T0.x scripts and live smoke tests; note it in the final report rather than treating it as a task failure.
- **No placeholders:** every step has runnable code.
- **Type consistency checked:** `run_feed_miner`'s return shape (`{"collected", "skipped_dupes", "status"}`) is used consistently in all 4 tests; `_classify_topic`'s signature matches how Task 3's main loop calls it; `send_telegram_alert`'s signature (`text: str) -> bool`) matches both its own tests and Task 3's usage.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-01-block-2-feed-miner.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
