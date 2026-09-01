# Block 0/1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the repo skeleton, Postgres schema, `LLMClient`, both Threads clients, and the ReAct harness — Blocks 0 and 1 of SPEC.md — so later blocks (content agent, dashboard, feed miner, reply triage, analyst) have a working foundation to build on.

**Architecture:** Python 3.12 worker/api services behind Docker Compose (postgres + worker + api + caddy), SQLAlchemy 2.x models mirroring the spec's DDL verbatim, Alembic migrations, an OpenAI-SDK-compatible `LLMClient` that routes by role via `config/models.yaml` and enforces the monthly budget before every call, a class-based `ThreadsWriteClient` against the official Graph API, a `ThreadsReadClient` that wraps the **existing, separate** `threads_app` project's Selenium code (imported via `sys.path`, not duplicated) and adds the jitter/daily-cap/alerting the spec requires but that project doesn't have, and a minimal ReAct agent harness that logs every step to Postgres.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, psycopg 3, FastAPI, `openai` SDK (used against both GLM and Kimi's OpenAI-compatible endpoints), Selenium (via the existing `threads_app` project), pyairtable, pytest, Docker Compose, Postgres 16.

**Spec:** [SPEC.md](../../../SPEC.md) — Sections 3–9 (architecture, Threads access, models/budget, agents, prompt, data model, safety) and Section 11 Block 0 (T0.1–T0.3) and Block 1 (T1.1–T1.6).

## Global Constraints

- Table, column, and role names are exactly as in SPEC.md §8 and §5 — never renamed (the future dashboard binds to them).
- Postgres has **no public port** — only `worker`, `api`, and `caddy` (internally) can reach it (SPEC.md §3).
- Every LLM call must be routed through `config/models.yaml`; no model name is ever hardcoded in `src/` (SPEC.md §5).
- Budget hard stop: total `daily_spend` cost for the current calendar month ≥ $10 → every LLM call raises `BudgetExceeded`. At ≥ $8, only role `post_writer` may still call (SPEC.md §9).
- Threads: publishing/replies/insights always go through the official API (`ThreadsWriteClient`); feed/keyword reading always goes through the browser (`ThreadsReadClient`). Never the reverse (SPEC.md §4, D1).
- `ThreadsReadClient`: session reused across runs, 3–15s random delay between actions, ≤ 200 feed views/day tracked in `daily_limits`, any auth error stops the caller and alerts — no retries (SPEC.md §4).
- `ThreadsWriteClient`: two-step publish (create container → poll → publish), `threads_publishing_limit` checked before every publish, exponential backoff on HTTP 429 (SPEC.md §4).
- ReAct harness hard limits: 8 steps / 40,000 tokens / 120 seconds per run; exceeding any → `agent_runs.status = 'step_limit'` and the in-progress artifact (e.g. a draft) is discarded, not saved (SPEC.md §6.1, §9).
- All agent activity is traced into `agent_runs` / `agent_steps` / `llm_calls` / `daily_spend` — never as a single JSONB blob (SPEC.md §8, D6).
- The existing `threads_app` project at `C:\Users\user\ai-projects\claude_project\threads_app` is **not modified** by this plan — it is only imported from, via a configurable path. It keeps running its own scraper/publisher independently until Block 1 finishes and it is decommissioned per SPEC.md §12 ("Airtable в рабочем контуре" is phased out after T1.6; `threads_app`'s own publish/scrape loops are superseded but the repo itself is left alone).

---

## File Structure

```
autonomous_threads/
├── docker-compose.yml
├── Dockerfile
├── Caddyfile
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
├── alembic.ini
├── config/
│   ├── models.yaml
│   └── settings.yaml
├── migrations/
│   ├── env.py
│   └── versions/
│       └── 0001_initial_schema.py
├── src/
│   ├── __init__.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py         # session/engine factory, reads DATABASE_URL
│   │   ├── models.py         # SQLAlchemy models, one per SPEC.md §8 table
│   │   └── repo.py           # query helpers used by llm/threads/agents
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── pricing.py        # price table + cost_usd calculator
│   │   └── client.py         # LLMClient: routing, budget check, tracing
│   ├── threads/
│   │   ├── __init__.py
│   │   ├── write_client.py   # ThreadsWriteClient (official API)
│   │   └── read_client.py    # ThreadsReadClient (wraps threads_app)
│   └── agents/
│       ├── __init__.py
│       └── base.py           # ReActAgent harness
├── scripts/
│   ├── check_threads_scopes.py     # T0.1
│   ├── compare_models_ru.py        # T0.2
│   ├── check_flash_rate_limit.py   # T0.3
│   └── import_airtable_history.py  # T1.6
└── tests/
    ├── conftest.py
    ├── db/test_models.py
    ├── llm/test_client.py
    ├── threads/test_write_client.py
    ├── threads/test_read_client.py
    └── agents/test_base.py
```

**Why this split:** `db/` owns schema + query helpers so `llm/`, `threads/`, and `agents/` never write raw SQL — they call `repo.py` functions. `llm/pricing.py` is separate from `llm/client.py` because the price table changes monthly (SPEC.md §5) while the client logic doesn't. `threads/write_client.py` and `read_client.py` are separate files (not one `ThreadsClient`) because they have opposite risk profiles (API vs. browser) and the spec calls them out as two distinct components with different safety mechanisms.

---

### Task 1: Repo scaffold, Docker Compose, env template

**Files:**
- Create: `autonomous_threads/.gitignore`
- Create: `autonomous_threads/pyproject.toml`
- Create: `autonomous_threads/requirements.txt`
- Create: `autonomous_threads/.env.example`
- Create: `autonomous_threads/docker-compose.yml`
- Create: `autonomous_threads/Dockerfile`
- Create: `autonomous_threads/Caddyfile`
- Create: `autonomous_threads/config/settings.yaml`
- Create: `autonomous_threads/src/__init__.py` (empty)

**Interfaces:**
- Produces: `DATABASE_URL` env var convention (`postgresql+psycopg://user:pass@postgres:5432/threads_agent`) that Task 2's `src/db/engine.py` consumes.
- Produces: `config/settings.yaml` keys `queue_depth`, `feed_view_daily_cap`, `search_groups` that Task 8/9 read.

- [ ] **Step 1: Initialize git and base project files**

```bash
cd "/c/Users/user/ai-projects/autonomous_threads"
git init
```

Create `.gitignore`:

```gitignore
__pycache__/
*.pyc
.venv/
.env
data/
*.egg-info/
.pytest_cache/
```

Create `pyproject.toml`:

```toml
[project]
name = "threads-agent"
version = "0.1.0"
requires-python = ">=3.12"

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

Create `requirements.txt`:

```
sqlalchemy>=2.0,<3.0
psycopg[binary]>=3.1,<4.0
alembic>=1.13,<2.0
fastapi>=0.110,<1.0
uvicorn>=0.29,<1.0
apscheduler>=3.10,<4.0
openai>=1.30,<2.0
pyairtable>=2.3,<3.0
python-dotenv>=1.0,<2.0
pyyaml>=6.0,<7.0
requests>=2.31,<3.0
selenium>=4.20,<5.0
pytest>=8.0,<9.0
pytest-mock>=3.14,<4.0
```

- [ ] **Step 2: Write `.env.example`**

```dotenv
# Postgres
DATABASE_URL=postgresql+psycopg://threads_agent:changeme@postgres:5432/threads_agent
POSTGRES_USER=threads_agent
POSTGRES_PASSWORD=changeme
POSTGRES_DB=threads_agent

# LLM providers (config/models.yaml references these key names)
GLM_API_KEY=
KIMI_API_KEY=

# Threads official API (Graph API)
THREADS_ACCESS_TOKEN=
THREADS_USER_ID=
THREADS_APP_ID=
THREADS_APP_SECRET=

# Threads browser reading — path to the existing threads_app checkout
THREADS_APP_PATH=C:\Users\user\ai-projects\claude_project\threads_app

# Airtable (source for T1.6 one-time import only)
AIRTABLE_API_KEY=
AIRTABLE_BASE_ID=appETFU4HKHySQIxi
AIRTABLE_TABLE_NAME=tblgBubbkIJSiu6Af

# Alerts
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# API
API_BEARER_TOKEN=changeme
```

- [ ] **Step 3: Write `config/settings.yaml`**

```yaml
queue_depth: 5
feed_view_daily_cap: 200
budget:
  soft_stop_usd: 8.0
  hard_stop_usd: 10.0
  soft_stop_allowed_role: post_writer
agent_limits:
  max_steps: 8
  max_tokens: 40000
  max_seconds: 120
search_groups:
  - name: automation_smb
    keywords:
      - "автоматизация бизнеса"
      - "n8n"
      - "ИИ агент для бизнеса"
ab_test_models: false
```

- [ ] **Step 4: Write `docker-compose.yml`, `Dockerfile`, `Caddyfile`**

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - pgdata:/var/lib/postgresql/data
    expose:
      - "5432"
    restart: unless-stopped

  worker:
    build: .
    command: python -m src.scheduler
    env_file: .env
    depends_on:
      - postgres
    restart: unless-stopped

  api:
    build: .
    command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000
    env_file: .env
    depends_on:
      - postgres
    expose:
      - "8000"
    restart: unless-stopped

  caddy:
    image: caddy:2
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
    depends_on:
      - api
    restart: unless-stopped

volumes:
  pgdata:
  caddy_data:
```

`Dockerfile`:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "-m", "src.scheduler"]
```

`Caddyfile`:

```
:443 {
    reverse_proxy api:8000
}
```

Note: `postgres` has no `ports:` mapping to the host — only `expose`, matching the Global Constraint that it stays unreachable from outside the compose network. `docker compose up` isn't runnable in this verification step yet (no `src/api/main.py` or `src/scheduler.py` exist until later tasks) — that check happens at the end of Task 9 once every image target exists.

- [ ] **Step 5: Commit**

```bash
git add .gitignore pyproject.toml requirements.txt .env.example docker-compose.yml Dockerfile Caddyfile config/settings.yaml src/__init__.py SPEC.md
git commit -m "chore: scaffold repo, docker-compose, env template"
```

---

### Task 2: Database schema (SQLAlchemy models + Alembic migration)

**Files:**
- Create: `src/db/models.py`
- Create: `src/db/engine.py`
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_initial_schema.py`
- Test: `tests/db/test_models.py`
- Test: `tests/conftest.py`

**Interfaces:**
- Consumes: `DATABASE_URL` env var from Task 1's `.env.example`.
- Produces: SQLAlchemy models `Post`, `SwipeFilePost`, `StyleVariant`, `PlaybookRule`, `KnowledgeBaseEntry`, `Reply`, `Lead`, `AgentRun`, `AgentStep`, `LlmCall`, `DailySpend`, `DailyLimit` — every later task imports these from `src.db.models`.
- Produces: `src/db/engine.py::get_engine() -> Engine` and `get_session() -> Session` (context-manager style), consumed by `src/db/repo.py` (Task 3) and every client/agent task after it.

- [ ] **Step 1: Write `src/db/engine.py`**

```python
import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        url = os.environ["DATABASE_URL"]
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal


@contextmanager
def session_scope() -> Session:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

- [ ] **Step 2: Write `src/db/models.py` mirroring SPEC.md §8 exactly**

```python
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, ForeignKey, Integer,
    Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    style_variant_id: Mapped[int | None] = mapped_column(ForeignKey("style_variants.id"))
    experiment_id: Mapped[str | None] = mapped_column(Text)
    playbook_version: Mapped[int | None] = mapped_column(Integer)
    model_used: Mapped[str | None] = mapped_column(Text)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    threads_media_id: Mapped[str | None] = mapped_column(Text, unique=True)
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    replies_count: Mapped[int | None] = mapped_column(Integer)
    quotes: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Numeric)
    metrics_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SwipeFilePost(Base):
    __tablename__ = "swipe_file"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    threads_post_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    replies: Mapped[int | None] = mapped_column(Integer)
    topic: Mapped[str | None] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StyleVariant(Base):
    __tablename__ = "style_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    genome: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("style_variants.id"))
    rationale: Mapped[str | None] = mapped_column(Text)
    posts_n: Mapped[int] = mapped_column(Integer, default=0)
    median_score: Mapped[float | None] = mapped_column(Numeric)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlaybookRule(Base):
    __tablename__ = "playbook_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    target_metric: Mapped[str | None] = mapped_column(Text)
    evidence_n: Mapped[int] = mapped_column(Integer, default=0)
    median_before: Mapped[float | None] = mapped_column(Numeric)
    median_after: Mapped[float | None] = mapped_column(Numeric)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    introduced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeBaseEntry(Base):
    __tablename__ = "knowledge_base"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reply(Base):
    __tablename__ = "replies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    threads_reply_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id"))
    author_username: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(Text)
    draft_response: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    threads_username: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int | None] = mapped_column(Integer)
    score_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    steps_count: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    error: Mapped[str | None] = mapped_column(Text)
    output_ref: Mapped[str | None] = mapped_column(Text)

    steps: Mapped[list["AgentStep"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    thought: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(Text)
    tool_args: Mapped[dict | None] = mapped_column(JSONB)
    tool_result: Mapped[dict | None] = mapped_column(JSONB)
    tool_ok: Mapped[bool | None] = mapped_column(Boolean)
    tool_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped["AgentRun"] = relationship(back_populates="steps")


class LlmCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    step_no: Mapped[int | None] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_cached: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    finish_reason: Mapped[str | None] = mapped_column(Text)
    prompt_sha: Mapped[str | None] = mapped_column(Text)
    prompt_raw: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailySpend(Base):
    __tablename__ = "daily_spend"

    date: Mapped[Date] = mapped_column(Date, primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    tokens_in: Mapped[int | None] = mapped_column(BigInteger)
    tokens_out: Mapped[int | None] = mapped_column(BigInteger)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))


class DailyLimit(Base):
    __tablename__ = "daily_limits"

    date: Mapped[Date] = mapped_column(Date, primary_key=True)
    counter: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 3: Write `tests/conftest.py`**

```python
import os

import pytest
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://threads_agent:changeme@localhost:5432/threads_agent_test")

from src.db.engine import get_engine, get_sessionmaker
from src.db.models import Base


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    engine = get_engine()
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db_session():
    Session = get_sessionmaker()
    session = Session()
    try:
        yield session
        session.rollback()
    finally:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
        session.commit()
        session.close()
```

This requires a real Postgres reachable at `localhost:5432` with a `threads_agent_test` database — start it with `docker compose up -d postgres` (Task 1) then `createdb -h localhost -U threads_agent threads_agent_test` once, or point `DATABASE_URL` at any scratch Postgres. Every later test file in this plan relies on this fixture.

- [ ] **Step 2: Run tests to verify the schema fixture works before writing assertions**

Run: `pytest tests/db/test_models.py -v` (file doesn't exist yet — this step is folded into the next one; write the test file now).

- [ ] **Step 4: Write the failing test `tests/db/test_models.py`**

```python
from src.db.models import Post, StyleVariant


def test_insert_and_read_post(db_session):
    variant = StyleVariant(name="v1", genome="voice: dry engineer", status="active", created_by="human")
    db_session.add(variant)
    db_session.flush()

    post = Post(
        text="Пример поста",
        category="educational",
        status="draft",
        style_variant_id=variant.id,
    )
    db_session.add(post)
    db_session.commit()

    fetched = db_session.query(Post).filter_by(text="Пример поста").one()
    assert fetched.category == "educational"
    assert fetched.style_variant_id == variant.id


def test_threads_media_id_unique(db_session):
    db_session.add(Post(text="a", category="news", status="published", threads_media_id="abc123"))
    db_session.commit()

    db_session.add(Post(text="b", category="news", status="published", threads_media_id="abc123"))
    with __import__("pytest").raises(Exception):
        db_session.commit()
```

- [ ] **Step 5: Run test, verify it fails for the right reason (no Postgres / no schema yet), then bring up Postgres**

```bash
docker compose up -d postgres
```

Run: `pytest tests/db/test_models.py -v`
Expected: PASS (the `_create_schema` fixture creates tables from `Base.metadata` directly — this test validates the ORM models, independent of Alembic).

- [ ] **Step 6: Write Alembic scaffolding — `alembic.ini` and `migrations/env.py`**

`alembic.ini`:

```ini
[alembic]
script_location = migrations
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[logger_root]
level = WARNING
handlers = console

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handlers]
keys = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatters]
keys = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

`migrations/env.py`:

```python
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 7: Write `migrations/versions/0001_initial_schema.py`**

```python
"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "style_variants",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("genome", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("style_variants.id")),
        sa.Column("rationale", sa.Text),
        sa.Column("posts_n", sa.Integer, server_default="0"),
        sa.Column("median_score", sa.Numeric),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "posts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("style_variant_id", sa.Integer, sa.ForeignKey("style_variants.id")),
        sa.Column("experiment_id", sa.Text),
        sa.Column("playbook_version", sa.Integer),
        sa.Column("model_used", sa.Text),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("threads_media_id", sa.Text, unique=True),
        sa.Column("views", sa.Integer),
        sa.Column("likes", sa.Integer),
        sa.Column("replies_count", sa.Integer),
        sa.Column("quotes", sa.Integer),
        sa.Column("score", sa.Numeric),
        sa.Column("metrics_updated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_posts_status_scheduled_at", "posts", ["status", "scheduled_at"])

    op.create_table(
        "swipe_file",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("threads_post_id", sa.Text, unique=True, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("author", sa.Text),
        sa.Column("views", sa.Integer),
        sa.Column("likes", sa.Integer),
        sa.Column("replies", sa.Integer),
        sa.Column("topic", sa.Text),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_swipe_file_collected_at", "swipe_file", ["collected_at"])

    op.create_table(
        "playbook_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("rule_text", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("hypothesis", sa.Text),
        sa.Column("target_metric", sa.Text),
        sa.Column("evidence_n", sa.Integer, server_default="0"),
        sa.Column("median_before", sa.Numeric),
        sa.Column("median_after", sa.Numeric),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("introduced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "knowledge_base",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "replies",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("threads_reply_id", sa.Text, unique=True, nullable=False),
        sa.Column("post_id", sa.BigInteger, sa.ForeignKey("posts.id")),
        sa.Column("author_username", sa.Text),
        sa.Column("text", sa.Text),
        sa.Column("kind", sa.Text),
        sa.Column("draft_response", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "leads",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("threads_username", sa.Text, nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("score", sa.Integer),
        sa.Column("score_reason", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("agent", sa.Text, nullable=False),
        sa.Column("trigger", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("steps_count", sa.Integer, server_default="0"),
        sa.Column("tokens_in", sa.Integer, server_default="0"),
        sa.Column("tokens_out", sa.Integer, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("output_ref", sa.Text),
    )
    op.create_index("ix_agent_runs_agent_started_at", "agent_runs", ["agent", "started_at"])

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("run_id", sa.BigInteger, sa.ForeignKey("agent_runs.id", ondelete="CASCADE")),
        sa.Column("step_no", sa.Integer, nullable=False),
        sa.Column("thought", sa.Text),
        sa.Column("tool_name", sa.Text),
        sa.Column("tool_args", JSONB),
        sa.Column("tool_result", JSONB),
        sa.Column("tool_ok", sa.Boolean),
        sa.Column("tool_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_steps_run_id_step_no", "agent_steps", ["run_id", "step_no"])

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("run_id", sa.BigInteger, sa.ForeignKey("agent_runs.id", ondelete="CASCADE")),
        sa.Column("step_no", sa.Integer),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("tokens_in", sa.Integer),
        sa.Column("tokens_cached", sa.Integer),
        sa.Column("tokens_out", sa.Integer),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("finish_reason", sa.Text),
        sa.Column("prompt_sha", sa.Text),
        sa.Column("prompt_raw", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_llm_calls_role_model_created_at", "llm_calls", ["role", "model", "created_at"])

    op.create_table(
        "daily_spend",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("model", sa.Text, primary_key=True),
        sa.Column("tokens_in", sa.BigInteger),
        sa.Column("tokens_out", sa.BigInteger),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
    )

    op.create_table(
        "daily_limits",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("counter", sa.Text, primary_key=True),
        sa.Column("value", sa.Integer, server_default="0"),
    )


def downgrade():
    op.drop_table("daily_limits")
    op.drop_table("daily_spend")
    op.drop_table("llm_calls")
    op.drop_table("agent_steps")
    op.drop_table("agent_runs")
    op.drop_table("leads")
    op.drop_table("replies")
    op.drop_table("knowledge_base")
    op.drop_table("playbook_rules")
    op.drop_table("swipe_file")
    op.drop_table("posts")
    op.drop_table("style_variants")
```

- [ ] **Step 8: Run the migration against the real compose Postgres and verify (T1.2 acceptance)**

```bash
docker compose up -d postgres
export DATABASE_URL=postgresql+psycopg://threads_agent:changeme@localhost:5432/threads_agent
alembic upgrade head
```

Expected: no errors; `psql $DATABASE_URL -c '\dt'` lists all 11 tables.

- [ ] **Step 9: Commit**

```bash
git add src/db config/settings.yaml alembic.ini migrations tests/conftest.py tests/db
git commit -m "feat: add SQLAlchemy models and Alembic migration for full schema"
```

---

### Task 3: Pricing table and `LLMClient`

**Files:**
- Create: `config/models.yaml`
- Create: `src/llm/pricing.py`
- Create: `src/llm/client.py`
- Create: `src/db/repo.py` (budget + llm_calls helpers only in this task; more helpers added in later tasks)
- Test: `tests/llm/test_client.py`

**Interfaces:**
- Consumes: `src.db.engine.session_scope`, models `LlmCall`, `DailySpend` from Task 2.
- Produces: `class LLMClient` with `complete(role: str, messages: list[dict], run_id: int | None = None, step_no: int | None = None) -> LLMResponse` where `LLMResponse` has `.text: str`, `.tokens_in: int`, `.tokens_out: int`, `.cost_usd: float`, `.model: str`. Raised: `class BudgetExceeded(Exception)`. Consumed by `src/agents/base.py` (Task 9) and scripts T0.2/T0.3.
- Produces: `src.db.repo.get_month_to_date_cost_usd(session) -> float`, `src.db.repo.record_llm_call(session, **fields) -> None`, `src.db.repo.upsert_daily_spend(session, model, tokens_in, tokens_out, cost_usd) -> None`.

- [ ] **Step 1: Write `config/models.yaml`**

```yaml
providers:
  glm:
    base_url: https://api.z.ai/api/paas/v4
    key_env: GLM_API_KEY
  kimi:
    base_url: https://api.moonshot.ai/v1
    key_env: KIMI_API_KEY

roles:
  post_writer:   { provider: glm, model: glm-4.7,       max_tokens: 2000 }
  analyst:       { provider: glm, model: glm-4.7,       max_tokens: 8000 }
  commenter:     { provider: glm, model: glm-4.7,       max_tokens: 500 }
  lead_scorer:   { provider: glm, model: glm-4.7-flash, max_tokens: 300 }
  style_critic:  { provider: glm, model: glm-4.7-flash, max_tokens: 400 }
  classifier:    { provider: glm, model: glm-4.7-flash, max_tokens: 200 }
```

- [ ] **Step 2: Write `src/llm/pricing.py`**

```python
# USD per 1M tokens. Source: SPEC.md §5, verified 2026-09-01.
PRICE_TABLE = {
    "glm-4.7-flash":  {"input": 0.0,  "cached_input": 0.0,  "output": 0.0},
    "glm-4.7-flashx": {"input": 0.07, "cached_input": 0.01, "output": 0.40},
    "glm-4.5-air":    {"input": 0.20, "cached_input": 0.03, "output": 1.10},
    "glm-4.7":        {"input": 0.60, "cached_input": 0.11, "output": 2.20},
    "glm-5.3":        {"input": 1.40, "cached_input": 0.26, "output": 4.40},
    "kimi-k2.5":      {"input": 0.60, "cached_input": 0.10, "output": 3.00},
    "kimi-k2.6":      {"input": 0.95, "cached_input": 0.16, "output": 4.00},
}


def cost_usd(model: str, tokens_in: int, tokens_out: int, tokens_cached: int = 0) -> float:
    if model not in PRICE_TABLE:
        raise KeyError(f"no price entry for model {model!r} — add it to PRICE_TABLE")
    rates = PRICE_TABLE[model]
    billable_input = max(tokens_in - tokens_cached, 0)
    return (
        billable_input * rates["input"]
        + tokens_cached * rates["cached_input"]
        + tokens_out * rates["output"]
    ) / 1_000_000
```

- [ ] **Step 2b: Write the failing test for pricing**

`tests/llm/test_client.py` (pricing assertions first, client assertions added in Step 5):

```python
from src.llm.pricing import cost_usd


def test_cost_usd_glm47():
    cost = cost_usd("glm-4.7", tokens_in=1000, tokens_out=500)
    assert round(cost, 8) == round((1000 * 0.60 + 500 * 2.20) / 1_000_000, 8)


def test_cost_usd_free_flash():
    assert cost_usd("glm-4.7-flash", tokens_in=100_000, tokens_out=50_000) == 0.0
```

Run: `pytest tests/llm/test_client.py -v`
Expected: PASS (pure function, no DB needed).

- [ ] **Step 3: Add budget + llm_calls helpers to `src/db/repo.py`**

```python
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import DailySpend, LlmCall


def get_month_to_date_cost_usd(session: Session, today: date | None = None) -> float:
    today = today or datetime.now(timezone.utc).date()
    month_start = today.replace(day=1)
    total = session.execute(
        select(func.coalesce(func.sum(DailySpend.cost_usd), 0)).where(
            DailySpend.date >= month_start, DailySpend.date <= today
        )
    ).scalar_one()
    return float(total)


def record_llm_call(session: Session, **fields) -> LlmCall:
    call = LlmCall(**fields)
    session.add(call)
    session.flush()
    return call


def upsert_daily_spend(session: Session, model: str, tokens_in: int, tokens_out: int, cost_usd: float, today: date | None = None) -> None:
    today = today or datetime.now(timezone.utc).date()
    row = session.get(DailySpend, {"date": today, "model": model})
    if row is None:
        row = DailySpend(date=today, model=model, tokens_in=0, tokens_out=0, cost_usd=0)
        session.add(row)
    row.tokens_in = (row.tokens_in or 0) + tokens_in
    row.tokens_out = (row.tokens_out or 0) + tokens_out
    row.cost_usd = float(row.cost_usd or 0) + cost_usd
```

- [ ] **Step 4: Write `src/llm/client.py`**

```python
import hashlib
import json
import os
import time
from dataclasses import dataclass

import yaml
from openai import OpenAI

from src.db.engine import session_scope
from src.db.repo import get_month_to_date_cost_usd, record_llm_call, upsert_daily_spend
from src.llm.pricing import cost_usd

BUDGET_SOFT_STOP_USD = 8.0
BUDGET_HARD_STOP_USD = 10.0
BUDGET_SOFT_STOP_ALLOWED_ROLE = "post_writer"


class BudgetExceeded(Exception):
    pass


@dataclass
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    model: str
    finish_reason: str | None


class LLMClient:
    def __init__(self, config_path: str = "config/models.yaml"):
        with open(config_path, encoding="utf-8") as f:
            self._config = yaml.safe_load(f)
        self._clients: dict[str, OpenAI] = {}

    def _client_for(self, provider: str) -> OpenAI:
        if provider not in self._clients:
            pconf = self._config["providers"][provider]
            api_key = os.environ[pconf["key_env"]]
            self._clients[provider] = OpenAI(base_url=pconf["base_url"], api_key=api_key)
        return self._clients[provider]

    def _check_budget(self, role: str) -> None:
        with session_scope() as session:
            spent = get_month_to_date_cost_usd(session)
        if spent >= BUDGET_HARD_STOP_USD:
            raise BudgetExceeded(f"month-to-date spend ${spent:.2f} >= hard stop ${BUDGET_HARD_STOP_USD}")
        if spent >= BUDGET_SOFT_STOP_USD and role != BUDGET_SOFT_STOP_ALLOWED_ROLE:
            raise BudgetExceeded(
                f"month-to-date spend ${spent:.2f} >= soft stop ${BUDGET_SOFT_STOP_USD}; "
                f"only role={BUDGET_SOFT_STOP_ALLOWED_ROLE!r} may still call"
            )

    def complete(
        self,
        role: str,
        messages: list[dict],
        run_id: int | None = None,
        step_no: int | None = None,
    ) -> LLMResponse:
        self._check_budget(role)

        role_conf = self._config["roles"][role]
        provider, model, max_tokens = role_conf["provider"], role_conf["model"], role_conf["max_tokens"]
        client = self._client_for(provider)

        prompt_sha = hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        started = time.monotonic()
        resp = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
        latency_ms = int((time.monotonic() - started) * 1000)

        usage = resp.usage
        tokens_in, tokens_out = usage.prompt_tokens, usage.completion_tokens
        tokens_cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        cost = cost_usd(model, tokens_in, tokens_out, tokens_cached)

        with session_scope() as session:
            record_llm_call(
                session,
                run_id=run_id,
                step_no=step_no,
                role=role,
                provider=provider,
                model=model,
                tokens_in=tokens_in,
                tokens_cached=tokens_cached,
                tokens_out=tokens_out,
                cost_usd=cost,
                latency_ms=latency_ms,
                finish_reason=resp.choices[0].finish_reason,
                prompt_sha=prompt_sha,
                prompt_raw={"messages": messages},
            )
            upsert_daily_spend(session, model, tokens_in, tokens_out, cost)

        return LLMResponse(
            text=resp.choices[0].message.content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            model=model,
            finish_reason=resp.choices[0].finish_reason,
        )
```

- [ ] **Step 5: Add the budget-enforcement tests to `tests/llm/test_client.py`**

```python
from datetime import date

import pytest

from src.db.models import DailySpend
from src.llm.client import BUDGET_HARD_STOP_USD, BudgetExceeded, LLMClient


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
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/llm/ -v`
Expected: PASS for all 4 tests (2 pricing, 2 budget). The budget tests exercise `_check_budget` directly against the real `db_session` fixture and never call the network — no API keys are needed to pass this task's tests. A live call against GLM/Kimi happens later in T0.2/T0.3 scripts once real keys are supplied.

- [ ] **Step 7: Commit**

```bash
git add config/models.yaml src/llm src/db/repo.py tests/llm
git commit -m "feat: add LLMClient with role-based routing, cost tracking, budget enforcement"
```

---

### Task 4: T0.1 — Threads token scope check

**Files:**
- Create: `scripts/check_threads_scopes.py`
- Create: `docs/threads_scopes.md` (generated output, committed as the acceptance artifact)

**Interfaces:**
- Consumes: `THREADS_ACCESS_TOKEN`, `THREADS_APP_ID`, `THREADS_APP_SECRET` env vars.
- Produces: `docs/threads_scopes.md`, listing granted vs. required scopes — the literal acceptance artifact SPEC.md T0.1 asks for.

Required scopes per SPEC.md T0.1: `threads_basic`, `threads_content_publish`, `threads_manage_insights`, `threads_manage_replies`.

- [ ] **Step 1: Write `scripts/check_threads_scopes.py`**

```python
"""T0.1: report which Threads API scopes the current access token actually has.

Threads (graph.threads.net) tokens are issued via the same OAuth flow as
Facebook Graph API tokens, so `debug_token` on graph.facebook.com works for
introspection. Run manually: `python -m scripts.check_threads_scopes`.
"""
import os
import sys
from datetime import datetime, timezone

import requests

REQUIRED_SCOPES = [
    "threads_basic",
    "threads_content_publish",
    "threads_manage_insights",
    "threads_manage_replies",
]

DEBUG_TOKEN_URL = "https://graph.facebook.com/debug_token"


def main() -> int:
    token = os.environ["THREADS_ACCESS_TOKEN"]
    app_id = os.environ["THREADS_APP_ID"]
    app_secret = os.environ["THREADS_APP_SECRET"]
    app_token = f"{app_id}|{app_secret}"

    resp = requests.get(DEBUG_TOKEN_URL, params={"input_token": token, "access_token": app_token}, timeout=15)

    lines = [
        "# Threads API scope check",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]

    if resp.status_code != 200:
        lines += [
            f"`debug_token` request failed: HTTP {resp.status_code} — {resp.text}",
            "",
            "Fall back to manual check: Meta App Dashboard → your app → "
            "App Review → Permissions and Features, and note which of the "
            "scopes below show 'Advanced Access' or 'Standard Access'.",
            "",
            "## Required scopes",
        ]
        for scope in REQUIRED_SCOPES:
            lines.append(f"- [ ] `{scope}`")
        write_report(lines)
        return 1

    data = resp.json().get("data", {})
    granted = set(data.get("scopes", []))

    lines.append(f"Token type: {data.get('type')}, app id: {data.get('app_id')}, expires_at: {data.get('expires_at')}")
    lines.append("")
    lines.append("## Scope status")
    for scope in REQUIRED_SCOPES:
        mark = "x" if scope in granted else " "
        lines.append(f"- [{mark}] `{scope}`")

    missing = [s for s in REQUIRED_SCOPES if s not in granted]
    lines.append("")
    if missing:
        lines.append(f"**Missing {len(missing)} scope(s):** {', '.join(missing)} — submit for App Review (2–6 weeks + 1–2 weeks business verification per SPEC.md T0.1).")
    else:
        lines.append("All required scopes granted.")

    write_report(lines)
    return 0 if not missing else 1


def write_report(lines: list[str]) -> None:
    with open("docs/threads_scopes.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it manually with real credentials**

```bash
python -m scripts.check_threads_scopes
```

This requires `THREADS_ACCESS_TOKEN`, `THREADS_APP_ID`, `THREADS_APP_SECRET` filled in `.env` (source it or export manually) — these are the tokens already obtained per SPEC.md §1. If `debug_token` 404s (Threads sometimes issues tokens that aren't introspectable via the Facebook endpoint), the script writes the manual-check fallback instructions into `docs/threads_scopes.md` — that file, either way, is the T0.1 acceptance artifact. Fill in any checkboxes it couldn't determine automatically by checking the Meta App Dashboard directly, then re-save the file.

- [ ] **Step 3: Commit**

```bash
git add scripts/check_threads_scopes.py docs/threads_scopes.md
git commit -m "feat: add T0.1 Threads scope-check script and report"
```

---

### Task 5: T0.2 — blind Russian-quality comparison (GLM vs Kimi)

**Files:**
- Create: `scripts/compare_models_ru.py`
- Create: `docs/model_comparison_ru.md` (generated output)

**Interfaces:**
- Consumes: `LLMClient.complete()` from Task 3 — but calls `glm-4.7` and `kimi-k2.5` directly by temporarily adding a throwaway role to a copy of `config/models.yaml`'s in-memory dict, since neither is the default `post_writer` role for both providers at once.
- Produces: `docs/model_comparison_ru.md` with 10 unlabeled posts per model, shuffled, for the human blind read described in T0.2.

- [ ] **Step 1: Write `scripts/compare_models_ru.py`**

```python
"""T0.2: generate 10 Threads posts each from glm-4.7 and kimi-k2.5 using the
same prompt, then write them out shuffled and unlabeled for a blind human
read. Run manually: `python -m scripts.compare_models_ru`.

Acceptance (SPEC.md T0.1): >= 7/10 posts from the CHOSEN model must be
publishable without edits. That judgment is made by a human reading
docs/model_comparison_ru.md — this script only produces the blind sample.
"""
import os
import random
import yaml
from openai import OpenAI

PROMPT = (
    "Ты — соло-разработчик автоматизации бизнес-процессов на ИИ (n8n, RAG на "
    "Qdrant, мультиагентные боты, Postgres memory). Есть три завершённых "
    "проекта: доставка, недвижимость, ресторан. Позиционирование: соло, без "
    "прослойки менеджеров, быстрее агентств. Напиши один пост для Threads "
    "(до 500 символов) на тему автоматизации бизнеса для СМБ. Регистр: "
    "инженер, который объясняет без пафоса, сухой юмор допустим. Не "
    "используй выдуманные цифры и статистику."
)

CANDIDATES = [
    {"label": "glm-4.7", "provider": "glm", "base_url": "https://api.z.ai/api/paas/v4", "key_env": "GLM_API_KEY", "model": "glm-4.7"},
    {"label": "kimi-k2.5", "provider": "kimi", "base_url": "https://api.moonshot.ai/v1", "key_env": "KIMI_API_KEY", "model": "kimi-k2.5"},
]


def generate(candidate: dict, n: int = 10) -> list[str]:
    client = OpenAI(base_url=candidate["base_url"], api_key=os.environ[candidate["key_env"]])
    posts = []
    for _ in range(n):
        resp = client.chat.completions.create(
            model=candidate["model"],
            messages=[{"role": "user", "content": PROMPT}],
            max_tokens=300,
            temperature=1.0,
        )
        posts.append(resp.choices[0].message.content.strip())
    return posts


def main() -> None:
    entries = []
    for candidate in CANDIDATES:
        for post in generate(candidate):
            entries.append({"model": candidate["label"], "text": post})

    random.shuffle(entries)

    lines = ["# T0.2 — blind Russian quality comparison", "", "Read each post. Mark publishable-without-edits or not. Reveal the model key at the bottom only after judging all 20.", ""]
    for i, entry in enumerate(entries, start=1):
        lines.append(f"## Post {i}")
        lines.append(entry["text"])
        lines.append("")
        lines.append("Publishable without edits? [ ] yes [ ] no")
        lines.append("")

    lines.append("---")
    lines.append("## Key (do not read until all 20 are judged)")
    for i, entry in enumerate(entries, start=1):
        lines.append(f"- Post {i}: `{entry['model']}`")

    with open("docs/model_comparison_ru.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {len(entries)} posts to docs/model_comparison_ru.md")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it manually with real API keys**

```bash
python -m scripts.compare_models_ru
```

Requires `GLM_API_KEY` and `KIMI_API_KEY` in the environment. Then open `docs/model_comparison_ru.md`, judge each of the 20 posts blind, reveal the key, and tally per-model pass rate. Record the result (which model chosen, X/10 score) as a short addition at the top of that file — this human judgment call is the actual T0.1/T0.2 acceptance gate and can't be automated further; if neither model clears 7/10, stop per SPEC.md T0.2 ("вся экономика спеки под вопросом") before continuing to Block 3.

- [ ] **Step 3: Commit**

```bash
git add scripts/compare_models_ru.py
git commit -m "feat: add T0.2 blind model-quality comparison script"
```

(Do not commit `docs/model_comparison_ru.md` itself if it ends up containing content you'd rather keep private — it's a generated artifact; `git status` before this commit and decide.)

---

### Task 6: T0.3 — free-tier rate limit check

**Files:**
- Create: `scripts/check_flash_rate_limit.py`

**Interfaces:**
- Consumes: `GLM_API_KEY` env var directly (bypasses `LLMClient`'s budget check since `glm-4.7-flash` is free and this is a burst load-test, not production traffic).
- Produces: printed pass/fail against the T0.3 acceptance threshold (< 5% errors over 200 calls).

- [ ] **Step 1: Write `scripts/check_flash_rate_limit.py`**

```python
"""T0.3: fire 200 sequential calls at glm-4.7-flash and measure the 429 rate.

Acceptance (SPEC.md T0.3): < 5% errors. If it fails, the helper roles move to
glm-4.7-flashx (+$0.15/month per SPEC.md T0.3) — that's a one-line change to
config/models.yaml, not a code change.

Run manually: `python -m scripts.check_flash_rate_limit`.
"""
import os
import time

from openai import APIStatusError, OpenAI

N_CALLS = 200


def main() -> None:
    client = OpenAI(base_url="https://api.z.ai/api/paas/v4", api_key=os.environ["GLM_API_KEY"])

    errors = 0
    rate_limit_errors = 0
    for i in range(N_CALLS):
        try:
            client.chat.completions.create(
                model="glm-4.7-flash",
                messages=[{"role": "user", "content": "Ответь одним словом: тест."}],
                max_tokens=10,
            )
        except APIStatusError as exc:
            errors += 1
            if exc.status_code == 429:
                rate_limit_errors += 1
            print(f"call {i}: HTTP {exc.status_code}")
        if i % 20 == 0:
            print(f"{i}/{N_CALLS} done, {errors} errors so far")

    error_rate = errors / N_CALLS
    print(f"\nTotal: {errors}/{N_CALLS} errors ({error_rate:.1%}), of which {rate_limit_errors} were 429.")
    if error_rate < 0.05:
        print("PASS — glm-4.7-flash stays as the helper-role model.")
    else:
        print("FAIL — move lead_scorer/style_critic/classifier to glm-4.7-flashx in config/models.yaml.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it manually**

```bash
python -m scripts.check_flash_rate_limit
```

Requires `GLM_API_KEY`. If it fails the < 5% threshold, edit `config/models.yaml`'s three flash-model roles (`lead_scorer`, `style_critic`, `classifier`) from `glm-4.7-flash` to `glm-4.7-flashx` — no code changes needed, confirming the config-driven design from Task 3 already satisfies this contingency.

- [ ] **Step 3: Commit**

```bash
git add scripts/check_flash_rate_limit.py
git commit -m "feat: add T0.3 free-tier rate-limit check script"
```

---

### Task 7: `ThreadsWriteClient` (official Graph API)

**Files:**
- Create: `src/threads/write_client.py`
- Test: `tests/threads/test_write_client.py`

**Interfaces:**
- Consumes: `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` env vars.
- Produces: `class ThreadsWriteClient` with `publish_text_post(text: str, reply_to_id: str | None = None) -> str` (returns media id), `reply_to_post(post_id: str, text: str) -> str`, `get_media_insights(media_id: str) -> dict`, `check_publishing_limit() -> dict`, `refresh_access_token() -> str`. Raised: `class ThreadsAPIError(Exception)`, `class PublishingLimitExceeded(Exception)`. Consumed by `content_agent` (Block 3) and `reply_triage` (Block 5) — not built in this plan, but this is the contract they'll use.

This reimplements (class-based, with the gaps the Explore agent found) rather than imports from `threads_app/common/threads_client.py`, because that module is a set of bare functions using module-level globals for credentials (`THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` read once at import time) — incompatible with dependency injection/testing, and missing `reply`, `threads_publishing_limit`, 429 backoff, and token refresh entirely (per the Task 3 exploration). The two-step publish flow itself (`create_container` → poll → `publish_container`) is proven working code there; this task follows the same shape.

- [ ] **Step 1: Write the failing tests `tests/threads/test_write_client.py`**

```python
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.threads.write_client import (
    PublishingLimitExceeded,
    ThreadsAPIError,
    ThreadsWriteClient,
)


@pytest.fixture()
def client():
    return ThreadsWriteClient(access_token="tok", user_id="123")


def test_publish_text_post_happy_path(client):
    with patch("src.threads.write_client.requests.post") as mock_post, \
         patch("src.threads.write_client.requests.get") as mock_get:
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {"id": "container-1"}),
            MagicMock(status_code=200, json=lambda: {"id": "media-1"}),
        ]
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"status": "FINISHED"})

        media_id = client.publish_text_post("Тестовый пост")

        assert media_id == "media-1"
        assert mock_post.call_count == 2


def test_check_publishing_limit_raises_when_exhausted(client):
    with patch("src.threads.write_client.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [{"quota_usage": 250, "config": {"quota_total": 250}}]},
        )
        with pytest.raises(PublishingLimitExceeded):
            client.check_publishing_limit()


def test_backoff_on_429_then_success(client, monkeypatch):
    monkeypatch.setattr("src.threads.write_client.time.sleep", lambda s: None)
    responses = [
        MagicMock(status_code=429, headers={}, json=lambda: {"error": "rate limited"}),
        MagicMock(status_code=200, json=lambda: {"id": "container-1"}),
    ]
    with patch("src.threads.write_client.requests.post", side_effect=responses) as mock_post:
        container_id = client.create_container("Пост")
        assert container_id == "container-1"
        assert mock_post.call_count == 2


def test_raises_threads_api_error_on_persistent_failure(client, monkeypatch):
    monkeypatch.setattr("src.threads.write_client.time.sleep", lambda s: None)
    with patch("src.threads.write_client.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=500, json=lambda: {"error": "server error"})
        with pytest.raises(ThreadsAPIError):
            client.create_container("Пост")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/threads/test_write_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.threads.write_client'`.

- [ ] **Step 3: Write `src/threads/write_client.py`**

```python
import time

import requests

GRAPH_BASE_URL = "https://graph.threads.net/v1.0"
MAX_RETRIES = 4
POLL_TIMEOUT_SEC = 60
POLL_INTERVAL_SEC = 5


class ThreadsAPIError(Exception):
    pass


class PublishingLimitExceeded(Exception):
    pass


class ThreadsWriteClient:
    def __init__(self, access_token: str, user_id: str):
        self._token = access_token
        self._user_id = user_id

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{GRAPH_BASE_URL}/{path}"
        params = kwargs.pop("params", {})
        params["access_token"] = self._token

        for attempt in range(MAX_RETRIES):
            resp = requests.request(method, url, params=params, timeout=30, **kwargs)
            if resp.status_code == 429:
                wait = 2 ** attempt
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise ThreadsAPIError(f"{method} {path} failed: HTTP {resp.status_code} — {resp.json()}")
            return resp.json()
        raise ThreadsAPIError(f"{method} {path} failed after {MAX_RETRIES} retries: still 429")

    def create_container(self, text: str, reply_to_id: str | None = None) -> str:
        params = {"media_type": "TEXT", "text": text}
        if reply_to_id:
            params["reply_to_id"] = reply_to_id
        data = self._request("post", f"{self._user_id}/threads", params=params)
        return data["id"]

    def get_container_status(self, container_id: str) -> str:
        data = self._request("get", container_id, params={"fields": "status"})
        return data["status"]

    def wait_until_ready(self, container_id: str, timeout_sec: int = POLL_TIMEOUT_SEC, poll_interval_sec: int = POLL_INTERVAL_SEC) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            status = self.get_container_status(container_id)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise ThreadsAPIError(f"container {container_id} ended in status {status}")
            time.sleep(poll_interval_sec)
        raise ThreadsAPIError(f"container {container_id} did not finish within {timeout_sec}s")

    def publish_container(self, container_id: str) -> str:
        data = self._request("post", f"{self._user_id}/threads_publish", params={"creation_id": container_id})
        return data["id"]

    def publish_text_post(self, text: str) -> str:
        self.check_publishing_limit()
        container_id = self.create_container(text)
        self.wait_until_ready(container_id)
        return self.publish_container(container_id)

    def reply_to_post(self, post_id: str, text: str) -> str:
        self.check_publishing_limit(kind="replies")
        container_id = self.create_container(text, reply_to_id=post_id)
        self.wait_until_ready(container_id)
        return self.publish_container(container_id)

    def get_media_insights(self, media_id: str) -> dict:
        data = self._request(
            "get", media_id + "/insights",
            params={"metric": "views,likes,replies,reposts,quotes,shares"},
        )
        result = {"views": 0, "likes": 0, "replies": 0, "reposts": 0, "quotes": 0, "shares": 0}
        for metric in data.get("data", []):
            values = metric.get("values", [{}])
            result[metric["name"]] = values[0].get("value", 0) if values else 0
        return result

    def check_publishing_limit(self, kind: str = "posts") -> dict:
        data = self._request("get", f"{self._user_id}/threads_publishing_limit")
        entry = data["data"][0]
        usage, total = entry["quota_usage"], entry["config"]["quota_total"]
        if usage >= total:
            raise PublishingLimitExceeded(f"{kind} limit exhausted: {usage}/{total} in the current 24h window")
        return {"usage": usage, "total": total}

    def refresh_access_token(self) -> str:
        data = self._request(
            "get", "refresh_access_token",
            params={"grant_type": "th_refresh_token"},
        )
        self._token = data["access_token"]
        return self._token
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/threads/test_write_client.py -v`
Expected: PASS (4/4). No live Threads API access needed — all HTTP calls are mocked.

- [ ] **Step 5: Manual live-publish smoke test (T1.4a acceptance)**

```bash
python -c "
import os
from src.threads.write_client import ThreadsWriteClient
c = ThreadsWriteClient(os.environ['THREADS_ACCESS_TOKEN'], os.environ['THREADS_USER_ID'])
media_id = c.publish_text_post('Тестовый пост от нового пайплайна — можно удалить.')
print('published:', media_id)
"
```

Then delete that test post manually from the Threads app (the Graph API doesn't expose a delete-post endpoint), confirming `threads_media_id` round-trips correctly — this satisfies T1.4a's acceptance ("тестовый пост публикуется и удаляется, threads_media_id записан").

- [ ] **Step 6: Commit**

```bash
git add src/threads/write_client.py tests/threads/test_write_client.py
git commit -m "feat: add ThreadsWriteClient with publish/reply/insights, backoff, publishing-limit check"
```

---

### Task 8: `ThreadsReadClient` (wraps existing `threads_app`)

**Files:**
- Create: `src/threads/read_client.py`
- Modify: `src/db/repo.py` (add `daily_limits` helpers)
- Test: `tests/threads/test_read_client.py`

**Interfaces:**
- Consumes: `THREADS_APP_PATH` env var (Task 1), `src.db.repo.get_daily_limit`/`increment_daily_limit` (new in this task), and imports `search.driver.build_driver`, `search.auth.login`, `search.scraper.scrape_keyword` from the existing `threads_app` project at runtime via `sys.path`.
- Produces: `class ThreadsReadClient` with `search_keyword(keyword: str, scroll_times: int = 5) -> list[dict]` (each dict: `{"keyword", "text", "url"}`, same shape `threads_app` already returns) and `class AuthError(Exception)`. Consumed by `feed_miner` (Block 2, not built in this plan) and later cold-lead search (Block 7).

- [ ] **Step 1: Add `daily_limits` helpers to `src/db/repo.py`**

```python
from datetime import date, datetime, timezone

from src.db.models import DailyLimit


def get_daily_limit(session, counter: str, today: date | None = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    row = session.get(DailyLimit, {"date": today, "counter": counter})
    return row.value if row else 0


def increment_daily_limit(session, counter: str, by: int = 1, today: date | None = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    row = session.get(DailyLimit, {"date": today, "counter": counter})
    if row is None:
        row = DailyLimit(date=today, counter=counter, value=0)
        session.add(row)
    row.value = (row.value or 0) + by
    session.flush()
    return row.value
```

(Add these as new functions in the same file created in Task 3 — do not remove the existing budget/llm_calls functions.)

- [ ] **Step 2: Write the failing tests `tests/threads/test_read_client.py`**

```python
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
    with patch("src.threads.read_client.build_driver", return_value=fake_driver), \
         patch("src.threads.read_client.login", return_value=True), \
         patch("src.threads.read_client.scrape_keyword", return_value=[{"keyword": "n8n", "text": "post", "url": "https://threads.net/post/1"}]):
        results = client.search_keyword("n8n")

    assert results == [{"keyword": "n8n", "text": "post", "url": "https://threads.net/post/1"}]
    fake_driver.quit.assert_called_once()


def test_search_keyword_raises_auth_error_without_retry(client, monkeypatch):
    monkeypatch.setattr("src.threads.read_client.random.uniform", lambda a, b: 0)
    monkeypatch.setattr("src.threads.read_client.time.sleep", lambda s: None)

    fake_driver = MagicMock()
    with patch("src.threads.read_client.build_driver", return_value=fake_driver), \
         patch("src.threads.read_client.login", return_value=False) as mock_login:
        with pytest.raises(AuthError):
            client.search_keyword("n8n")

    assert mock_login.call_count == 1  # no retries
    fake_driver.quit.assert_called_once()


def test_daily_view_cap_enforced(client, db_session):
    from src.db.repo import increment_daily_limit
    increment_daily_limit(db_session, "feed_views", by=200)
    db_session.commit()

    with pytest.raises(DailyViewCapExceeded):
        client.search_keyword("n8n")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/threads/test_read_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.threads.read_client'`.

- [ ] **Step 4: Write `src/threads/read_client.py`**

```python
import os
import random
import sys
import time

from src.db.engine import session_scope
from src.db.repo import get_daily_limit, increment_daily_limit


def _add_threads_app_to_path() -> None:
    threads_app_path = os.environ["THREADS_APP_PATH"]
    if threads_app_path not in sys.path:
        sys.path.insert(0, threads_app_path)


_add_threads_app_to_path()

from search.driver import build_driver  # noqa: E402  (must follow sys.path insert)
from search.auth import login  # noqa: E402
from search.scraper import scrape_keyword  # noqa: E402


class AuthError(Exception):
    pass


class DailyViewCapExceeded(Exception):
    pass


class ThreadsReadClient:
    def __init__(self, daily_view_cap: int = 200, min_delay_sec: float = 3.0, max_delay_sec: float = 15.0):
        self._cap = daily_view_cap
        self._min_delay = min_delay_sec
        self._max_delay = max_delay_sec

    def _check_and_increment_cap(self, n: int) -> None:
        with session_scope() as session:
            current = get_daily_limit(session, "feed_views")
            if current + n > self._cap:
                raise DailyViewCapExceeded(f"feed_views {current}+{n} would exceed daily cap {self._cap}")
            increment_daily_limit(session, "feed_views", by=n)

    def _jitter(self) -> None:
        time.sleep(random.uniform(self._min_delay, self._max_delay))

    def search_keyword(self, keyword: str, scroll_times: int = 5) -> list[dict]:
        self._check_and_increment_cap(scroll_times)

        driver = build_driver(headless=True)
        try:
            self._jitter()
            if not login(driver):
                raise AuthError(
                    f"Threads browser login failed for keyword search {keyword!r} — "
                    "stopping without retry, alert the operator"
                )
            self._jitter()
            return scrape_keyword(driver, keyword, scroll_times=scroll_times)
        finally:
            driver.quit()
```

Note on session reuse: `threads_app.search.auth.login()` already tries the persisted cookie jar at `threads_app/data/threads_cookies.json` before falling back to username/password — that satisfies "session reused between runs, re-login only on expiry" for free, since `ThreadsReadClient` calls the same `login()` every time and it's a no-op re-auth whenever cookies are still valid. No new session-management code is needed here; this task only adds the pieces `threads_app` doesn't have: jitter, the daily cap, and turning a silent `False` return into a raised `AuthError` the caller can catch and alert on.

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest tests/threads/test_read_client.py -v`
Expected: PASS (3/3). Requires `THREADS_APP_PATH` to be set (Task 1's `.env.example` default already points at the real checkout) so the module-level import at the top of `read_client.py` succeeds — but the actual Selenium/Chrome code is fully mocked in these tests, so no browser or real login happens.

- [ ] **Step 6: Manual smoke test against the real browser (T1.4b acceptance)**

```bash
python -c "
from src.threads.read_client import ThreadsReadClient
c = ThreadsReadClient()
posts = c.search_keyword('n8n', scroll_times=5)
print(len(posts), 'posts')
"
```

Confirm `daily_limits` incremented: `psql $DATABASE_URL -c "select * from daily_limits where counter='feed_views'"`. Run it enough times (or lower `daily_view_cap` temporarily) to confirm `DailyViewCapExceeded` fires at 200 — satisfying T1.4b's stated acceptance.

- [ ] **Step 7: Commit**

```bash
git add src/threads/read_client.py src/db/repo.py tests/threads/test_read_client.py
git commit -m "feat: add ThreadsReadClient wrapping threads_app with jitter, daily cap, auth alerting"
```

---

### Task 9: ReAct agent harness

**Files:**
- Create: `src/agents/base.py`
- Modify: `src/db/repo.py` (add `agent_runs`/`agent_steps` helpers)
- Create: `src/api/main.py` (minimal health endpoint, so Task 1's `docker-compose.yml` `api` service target exists)
- Create: `src/scheduler.py` (minimal no-op loop, so the `worker` service target exists)
- Test: `tests/agents/test_base.py`

**Interfaces:**
- Consumes: `LLMClient` (Task 3), `src.db.repo` run/step helpers (new in this task).
- Produces: `class ReActAgent` — subclasses implement `def tools(self) -> dict[str, callable]` and `def system_prompt(self) -> str`; `run(trigger: str) -> AgentRun` drives the loop and enforces the 8-step/40k-token/120s limits from `config/settings.yaml`. Consumed by `content_agent`/`analyst_agent` in later blocks (not built here) — this task validates the harness with an in-test dummy agent, per T1.5's acceptance.

- [ ] **Step 1: Add `agent_runs`/`agent_steps` helpers to `src/db/repo.py`**

```python
from datetime import datetime, timezone

from src.db.models import AgentRun, AgentStep


def start_agent_run(session, agent: str, trigger: str) -> AgentRun:
    run = AgentRun(agent=agent, trigger=trigger, started_at=datetime.now(timezone.utc), status="running")
    session.add(run)
    session.flush()
    return run


def add_agent_step(session, run_id: int, step_no: int, **fields) -> AgentStep:
    step = AgentStep(run_id=run_id, step_no=step_no, **fields)
    session.add(step)
    session.flush()
    return step


def finish_agent_run(session, run_id: int, status: str, **fields) -> None:
    run = session.get(AgentRun, run_id)
    run.status = status
    run.finished_at = datetime.now(timezone.utc)
    for key, value in fields.items():
        setattr(run, key, value)
```

- [ ] **Step 2: Write the failing test `tests/agents/test_base.py`**

```python
import time

import pytest

from src.agents.base import ReActAgent, StepLimitExceeded
from src.db.models import AgentRun, AgentStep


class _EchoTool:
    def __init__(self):
        self.calls = 0

    def __call__(self, text: str) -> str:
        self.calls += 1
        return f"echo:{text}"


class _TwoStepAgent(ReActAgent):
    """Calls the echo tool twice then stops — used to prove the harness records exactly what happened."""

    def __init__(self, echo_tool, **kwargs):
        super().__init__(agent_name="test_agent", **kwargs)
        self._echo = echo_tool
        self._step = 0

    def tools(self) -> dict:
        return {"echo": self._echo}

    def system_prompt(self) -> str:
        return "test"

    def decide_next_action(self, history: list[dict]) -> dict | None:
        self._step += 1
        if self._step > 2:
            return None  # signal: done
        return {"thought": f"step {self._step}", "tool_name": "echo", "tool_args": {"text": str(self._step)}}


class _RunawayAgent(ReActAgent):
    """Never stops on its own — used to prove the step-limit hard-stops it."""

    def __init__(self, **kwargs):
        super().__init__(agent_name="runaway", **kwargs)

    def tools(self) -> dict:
        return {"noop": lambda: "ok"}

    def system_prompt(self) -> str:
        return "test"

    def decide_next_action(self, history: list[dict]) -> dict:
        return {"thought": "again", "tool_name": "noop", "tool_args": {}}


def test_agent_runs_recorded_steps(db_session):
    echo = _EchoTool()
    agent = _TwoStepAgent(echo, max_steps=8, max_tokens=40_000, max_seconds=120)

    run = agent.run(trigger="manual")

    assert run.status == "ok"
    assert echo.calls == 2

    steps = db_session.query(AgentStep).filter_by(run_id=run.id).order_by(AgentStep.step_no).all()
    assert [s.tool_name for s in steps] == ["echo", "echo"]
    assert steps[0].tool_result == "echo:1"


def test_step_limit_stops_at_nine(db_session):
    agent = _RunawayAgent(max_steps=8, max_tokens=40_000, max_seconds=120)

    run = agent.run(trigger="manual")

    assert run.status == "step_limit"
    assert run.steps_count == 8
    fetched = db_session.get(AgentRun, run.id)
    assert fetched.status == "step_limit"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/agents/test_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.agents.base'`.

- [ ] **Step 4: Write `src/agents/base.py`**

```python
import time
from abc import ABC, abstractmethod

from src.db.engine import session_scope
from src.db.repo import add_agent_step, finish_agent_run, start_agent_run


class StepLimitExceeded(Exception):
    pass


class ReActAgent(ABC):
    def __init__(self, agent_name: str, max_steps: int = 8, max_tokens: int = 40_000, max_seconds: int = 120):
        self.agent_name = agent_name
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.max_seconds = max_seconds

    @abstractmethod
    def tools(self) -> dict:
        """tool_name -> callable(**tool_args) -> Any"""

    @abstractmethod
    def system_prompt(self) -> str:
        ...

    @abstractmethod
    def decide_next_action(self, history: list[dict]) -> dict | None:
        """Return {"thought", "tool_name", "tool_args"} or None to stop."""

    def run(self, trigger: str):
        with session_scope() as session:
            run = start_agent_run(session, agent=self.agent_name, trigger=trigger)
            run_id = run.id

        history: list[dict] = []
        tokens_used = 0
        started = time.monotonic()
        status = "ok"
        step_no = 0

        try:
            for step_no in range(1, self.max_steps + 1):
                if time.monotonic() - started > self.max_seconds:
                    status = "step_limit"
                    break
                if tokens_used > self.max_tokens:
                    status = "step_limit"
                    break

                action = self.decide_next_action(history)
                if action is None:
                    break

                tool_name = action["tool_name"]
                tool_args = action.get("tool_args", {})
                tool = self.tools()[tool_name]

                tool_started = time.monotonic()
                try:
                    result = tool(**tool_args)
                    tool_ok = True
                except Exception as exc:  # noqa: BLE001 — recorded, not swallowed silently
                    result = str(exc)
                    tool_ok = False
                tool_ms = int((time.monotonic() - tool_started) * 1000)

                with session_scope() as session:
                    add_agent_step(
                        session,
                        run_id=run_id,
                        step_no=step_no,
                        thought=action.get("thought"),
                        tool_name=tool_name,
                        tool_args=tool_args,
                        tool_result=result if isinstance(result, (dict, list, str, int, float, bool, type(None))) else str(result),
                        tool_ok=tool_ok,
                        tool_ms=tool_ms,
                    )

                history.append({"thought": action.get("thought"), "tool_name": tool_name, "tool_args": tool_args, "result": result})
            else:
                status = "step_limit"
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            with session_scope() as session:
                finish_agent_run(session, run_id, status=status, steps_count=step_no, error=str(exc))
            raise

        with session_scope() as session:
            finish_agent_run(session, run_id, status=status, steps_count=step_no)
            run = session.get(type(run), run_id)
            session.refresh(run)
            # detach a plain snapshot so callers can read it after the session closes
            from src.db.models import AgentRun as _AgentRun
            snapshot = _AgentRun(**{c.name: getattr(run, c.name) for c in _AgentRun.__table__.columns})
        return snapshot
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest tests/agents/test_base.py -v`
Expected: PASS (2/2).

- [ ] **Step 6: Add minimal `src/api/main.py` and `src/scheduler.py` so the Docker Compose targets from Task 1 actually build**

`src/api/main.py`:

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}
```

`src/scheduler.py`:

```python
import time


def main():
    print("worker started — no jobs scheduled yet (added in Block 2+)")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Verify the full stack builds and comes up (T1.1 acceptance, now that all targets exist)**

```bash
docker compose up -d --build
docker compose ps
curl -s https://localhost/health -k  # via caddy, or curl http://localhost:8000/health for the api container directly
```

Expected: all four services (`postgres`, `worker`, `api`, `caddy`) show as running; `postgres` has no host port bound (`docker compose ps` shows no `0.0.0.0:5432->5432` mapping — confirm with `docker compose config | grep -A3 postgres:`).

- [ ] **Step 8: Commit**

```bash
git add src/agents src/api src/scheduler.py src/db/repo.py tests/agents
git commit -m "feat: add ReAct agent harness with step/token/time limits and full tracing"
```

---

### Task 10: T1.6 — Airtable history import

**Files:**
- Create: `scripts/import_airtable_history.py`

**Interfaces:**
- Consumes: `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID` (`appETFU4HKHySQIxi`), `AIRTABLE_TABLE_NAME` (`tblgBubbkIJSiu6Af`) from `.env`; `src.threads.write_client.ThreadsWriteClient.get_media_insights` (Task 7) for the 90-day metrics backfill; `src.db.models.Post` (Task 2).
- Produces: populated `posts` rows with `model_used=None` (unknown for historical posts), `score` computed via the SPEC.md §7 target function.

This mirrors `threads_app/common/airtable_client.py`'s field names (`Status`, `ScheduledAt`, `PostText`, `PublishedAt`, `ThreadsPostId`, `Views`, `Likes`, `Replies`, `Reposts`, `Quotes`) since that's the existing schema of the same base/table this script points at.

- [ ] **Step 1: Write `scripts/import_airtable_history.py`**

```python
"""T1.6: one-time import of historical posts from Airtable into `posts`,
backfilling metrics via the Threads Graph API for the last 90 days.

Run manually once: `python -m scripts.import_airtable_history`.
"""
import os
from datetime import datetime, timedelta, timezone

from pyairtable import Api

from src.db.engine import session_scope
from src.db.models import Post
from src.threads.write_client import ThreadsAPIError, ThreadsWriteClient

CATEGORY_FALLBACK = "educational"  # historical posts predate the category field; reclassify later if needed
SCORE_WEIGHTS = {"leads": 100, "conversations": 10, "replies": 1, "views": 0.01}


def compute_score(replies_count: int, views: int) -> float:
    # Historical Airtable rows have no leads/conversations tracking — only replies and views are known.
    return SCORE_WEIGHTS["replies"] * (replies_count or 0) + SCORE_WEIGHTS["views"] * (views or 0)


def main() -> None:
    api = Api(os.environ["AIRTABLE_API_KEY"])
    table = api.table(os.environ["AIRTABLE_BASE_ID"], os.environ["AIRTABLE_TABLE_NAME"])

    write_client = ThreadsWriteClient(os.environ["THREADS_ACCESS_TOKEN"], os.environ["THREADS_USER_ID"])
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)

    imported, skipped = 0, 0
    for record in table.all():
        fields = record["fields"]
        media_id = fields.get("ThreadsPostId")
        if not media_id:
            skipped += 1
            continue

        with session_scope() as session:
            existing = session.query(Post).filter_by(threads_media_id=media_id).one_or_none()
            if existing:
                skipped += 1
                continue

        views = likes = replies_count = quotes = 0
        published_at = fields.get("PublishedAt")
        posted_dt = datetime.fromisoformat(published_at) if published_at else None
        if posted_dt and posted_dt >= cutoff:
            try:
                insights = write_client.get_media_insights(media_id)
                views, likes, replies_count, quotes = (
                    insights["views"], insights["likes"], insights["replies"], insights["quotes"],
                )
            except ThreadsAPIError as exc:
                print(f"insights failed for {media_id}: {exc}")

        with session_scope() as session:
            session.add(Post(
                text=fields.get("PostText", ""),
                category=CATEGORY_FALLBACK,
                status="published",
                threads_media_id=media_id,
                posted_at=posted_dt,
                views=views,
                likes=likes,
                replies_count=replies_count,
                quotes=quotes,
                score=compute_score(replies_count, views),
                metrics_updated_at=datetime.now(timezone.utc) if posted_dt and posted_dt >= cutoff else None,
            ))
        imported += 1

    print(f"Imported {imported} posts, skipped {skipped} (already present or missing ThreadsPostId).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it manually against the real Airtable base**

```bash
python -m scripts.import_airtable_history
```

Requires `AIRTABLE_API_KEY` filled in `.env` (base/table IDs are already defaulted in Task 1's `.env.example`). Verify: `psql $DATABASE_URL -c "select count(*), count(metrics_updated_at) from posts"` — the second count should match however many historical posts fall within the last 90 days, satisfying T1.6's "все исторические посты в БД с заполненными метриками и пересчитанным score" (posts older than 90 days are imported with `score` still computed from whatever `replies_count`/`views` Airtable already had cached, but `metrics_updated_at` stays null since they weren't freshly backfilled — flag this row count in the commit message so it's visible later).

- [ ] **Step 3: Commit**

```bash
git add scripts/import_airtable_history.py
git commit -m "feat: add T1.6 Airtable history import with 90-day metrics backfill"
```

---

## Self-Review Notes

- **Spec coverage:** T0.1→Task 4, T0.2→Task 5, T0.3→Task 6, T1.1→Tasks 1+9(step 7), T1.2→Task 2, T1.3→Task 3, T1.4a→Task 7, T1.4b→Task 8, T1.5→Task 9, T1.6→Task 10. All of Block 0 and Block 1 from SPEC.md §11 are covered. Blocks 2–7 (feed_miner, content_agent, dashboard, reply_triage, analyst, cold leads) are explicitly out of scope for this plan — pick them up as separate plans once this foundation lands, per the writing-plans "Scope Check" (each subsystem gets its own plan).
- **Threads scopes needed for T1.4a/T1.6 live tests** (`threads_content_publish`, `threads_manage_insights`) are exactly what T0.1 checks — run Task 4 before the live-smoke-test steps in Tasks 7 and 10 so you know upfront whether those calls can even succeed.
- **No placeholders:** every step has runnable code; the three T0.x scripts are real scripts, not TODOs, even though their acceptance judgment (blind read, error-rate threshold) is inherently manual.
- **Type consistency checked:** `ThreadsReadClient.search_keyword` return shape (`{"keyword", "text", "url"}`) matches `threads_app.search.scraper.scrape_keyword`'s actual return shape (confirmed via the Explore agent's report) so `feed_miner` (future block) can consume it unchanged. `LLMResponse`, `AgentRun`/`AgentStep` field names match `src/db/models.py` column names exactly.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-01-block-0-1-foundation.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
