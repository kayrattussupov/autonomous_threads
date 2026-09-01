# Block 4 Dashboard API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the FastAPI read endpoints and the two approve/reject mutation endpoints that the Vercel dashboard needs — `/posts`, `/runs`, `/runs/{id}/steps`, `/styles` (+ approve/reject), `/playbook` (+ approve/reject), `/funnel`, `/spend` — all Bearer-token-gated, all reading/writing the existing Postgres schema.

**Architecture:** One `require_bearer_token` FastAPI dependency guards every new route. One `get_db` dependency yields a SQLAlchemy session per request (commit on success, rollback on exception — the request-scoped counterpart to `src/db/engine.py`'s `session_scope`). Query/mutation logic lives in `src/db/repo.py` (extends the module Block 0/1 already established); routers stay thin — parse params, call repo, serialize with Pydantic. Two new exception types (`InvalidStateTransition`, `RetirementBlocked`) let repo functions signal invalid approve/reject attempts without routers knowing repo internals; two FastAPI exception handlers translate them to `409`/`422`.

**Tech Stack:** Python 3.12, FastAPI (already a dependency), Pydantic v2 (ships with FastAPI), SQLAlchemy 2.x, pytest + `fastapi.testclient.TestClient` + `httpx`.

**Spec:** [docs/superpowers/specs/2026-09-01-block-4-dashboard-design.md](../specs/2026-09-01-block-4-dashboard-design.md) §3, §6 — parent: [SPEC.md](../../../SPEC.md) §10, §11 Block 4 (T4.1, T4.3).

## Global Constraints

- Table, column, and role names are exactly as in SPEC.md §8 and §5 — never renamed.
- Postgres has no public port; this plan only adds routes to the existing `api` service, it doesn't touch networking (SPEC.md §3).
- Every new route except `/health` requires `Authorization: Bearer <API_BEARER_TOKEN>`; missing or wrong value → `401` (design doc §3, SPEC.md §10).
- Style-variant comparison and retirement decisions are always by **median** score, never mean (SPEC.md §7, D9).
- A style variant may not be retired while `posts_n < 20` (SPEC.md §7, D8) — enforced in `src/db/repo.py`, not trusted to the caller.
- "Диалог" for `/funnel` = a `replies` row with `kind IN ('question','objection')` AND `responded_at IS NOT NULL` (design doc §3) — this definition isn't in SPEC.md itself, it was fixed during design.
- `style_variants.status = 'rejected'` is a new but valid value this plan introduces (TEXT column, no `CHECK` constraint in the schema — adding a value is non-breaking) (design doc §3).
- Approve/reject on a row not in its expected pending state → `409 Conflict`, not a silent no-op (design doc §6).

---

## File Structure

```
src/
├── api/
│   ├── main.py            # MODIFY: include_router calls, exception handlers
│   ├── deps.py             # NEW: require_bearer_token, get_db
│   ├── schemas.py          # NEW: all Pydantic response models
│   └── routers/
│       ├── __init__.py     # NEW: empty
│       ├── posts.py        # NEW: GET /posts
│       ├── runs.py         # NEW: GET /runs, GET /runs/{id}/steps
│       ├── styles.py       # NEW: GET /styles, POST /styles/{id}/approve|reject
│       ├── playbook.py     # NEW: GET /playbook, POST /playbook/{id}/approve|reject
│       ├── funnel.py       # NEW: GET /funnel
│       └── spend.py        # NEW: GET /spend
├── db/
│   └── repo.py             # MODIFY: query/mutation helpers, two new exception classes
tests/
├── conftest.py              # MODIFY: default API_BEARER_TOKEN for tests
└── api/
    ├── __init__.py          # NEW: empty
    ├── test_posts.py        # NEW
    ├── test_runs.py         # NEW
    ├── test_styles.py       # NEW
    ├── test_playbook.py     # NEW
    ├── test_funnel.py       # NEW
    └── test_spend.py        # NEW
requirements.txt              # MODIFY: add httpx (TestClient needs it explicitly)
```

**Why this split:** `deps.py` is separate from `schemas.py` because one is request plumbing (auth, DB session) and the other is pure data shape — different reasons to change. One router file per resource matches how `src/threads/` already splits `write_client.py`/`read_client.py` by responsibility rather than bundling everything into one file. `repo.py` keeps growing additively, exactly as it did across Tasks 3/8/9 of the Block 0/1 plan — routers never write raw SQL.

---

### Task 1: Auth + DB session dependencies, shared schemas, exception plumbing

**Files:**
- Create: `src/api/deps.py`
- Create: `src/api/schemas.py`
- Create: `src/api/routers/__init__.py` (empty)
- Modify: `src/api/main.py`
- Modify: `src/db/repo.py` (add `InvalidStateTransition`, `RetirementBlocked` exception classes only — no query functions yet)
- Modify: `tests/conftest.py`
- Modify: `requirements.txt`
- Create: `tests/api/__init__.py` (empty)

**Interfaces:**
- Produces: `src.api.deps.require_bearer_token` (FastAPI dependency, raises `HTTPException(401)`), `src.api.deps.get_db` (FastAPI generator dependency yielding `Session`) — every router task after this one uses both.
- Produces: `src.api.schemas.{PostOut, PostsPageOut, AgentRunOut, AgentStepOut, StyleVariantOut, PlaybookRuleOut, FunnelMonthOut, SpendOut}` — consumed by Tasks 2–7.
- Produces: `src.db.repo.InvalidStateTransition`, `src.db.repo.RetirementBlocked` — raised by Task 4/5's approve/reject functions, caught by `main.py`'s exception handlers added in this task.

- [ ] **Step 1: Add `httpx` to `requirements.txt`**

`fastapi.testclient.TestClient` requires `httpx` explicitly (Starlette no longer vendors a test client transport). Add this line to `requirements.txt` (anywhere, e.g. after the `fastapi` line):

```
httpx>=0.27,<1.0
```

- [ ] **Step 2: Write `src/api/deps.py`**

```python
import os
from collections.abc import Generator

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from src.db.engine import get_sessionmaker


def require_bearer_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ["API_BEARER_TOKEN"]
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def get_db() -> Generator[Session, None, None]:
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

- [ ] **Step 3: Write `src/api/schemas.py`**

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    text: str
    category: str
    status: str
    source_url: str | None
    style_variant_id: int | None
    experiment_id: str | None
    playbook_version: int | None
    model_used: str | None
    scheduled_at: datetime | None
    posted_at: datetime | None
    threads_media_id: str | None
    views: int | None
    likes: int | None
    replies_count: int | None
    quotes: int | None
    score: float | None
    metrics_updated_at: datetime | None
    created_at: datetime


class PostsPageOut(BaseModel):
    items: list[PostOut]
    total: int
    page: int
    page_size: int
    median_score: float | None


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    steps_count: int | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    error: str | None
    output_ref: str | None


class AgentStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    step_no: int
    thought: str | None
    tool_name: str | None
    tool_args: dict | None
    tool_result: dict | None
    tool_ok: bool | None
    tool_ms: int | None
    created_at: datetime


class StyleVariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    genome: str
    status: str
    created_by: str
    parent_id: int | None
    rationale: str | None
    posts_n: int | None
    median_score: float | None
    created_at: datetime


class PlaybookRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_text: str
    status: str
    hypothesis: str | None
    target_metric: str | None
    evidence_n: int | None
    median_before: float | None
    median_after: float | None
    version: int
    introduced_at: datetime


class FunnelMonthOut(BaseModel):
    month: str
    posts: int
    views: int
    replies: int
    conversations: int
    leads: int


class SpendOut(BaseModel):
    month_to_date_usd: float
    cap_usd: float
```

- [ ] **Step 4: Add the two exception classes to `src/db/repo.py`**

Open `src/db/repo.py` and add near the top, after the imports (do not remove or reorder any existing function):

```python
class InvalidStateTransition(Exception):
    """Raised when approve/reject targets a row that isn't in its expected pending state."""


class RetirementBlocked(Exception):
    """Raised when approving a style variant would retire another one with posts_n < 20."""
```

- [ ] **Step 5: Wire exception handlers and confirm `src/api/main.py`**

Replace the full contents of `src/api/main.py` with:

```python
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.db.repo import InvalidStateTransition, RetirementBlocked

app = FastAPI()


@app.exception_handler(InvalidStateTransition)
def handle_invalid_state_transition(request: Request, exc: InvalidStateTransition):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(RetirementBlocked)
def handle_retirement_blocked(request: Request, exc: RetirementBlocked):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok"}
```

(Tasks 2–7 each add one `app.include_router(...)` line below the exception handlers — this step only establishes the base file.)

- [ ] **Step 6: Add a default `API_BEARER_TOKEN` for tests to `tests/conftest.py`**

Open `tests/conftest.py` and add this line directly below the existing `os.environ.setdefault("DATABASE_URL", ...)` line (don't remove anything):

```python
os.environ.setdefault("API_BEARER_TOKEN", "test-token")
```

- [ ] **Step 7: Write the failing test `tests/api/test_deps.py`**

```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.deps import require_bearer_token


def _client_with_protected_route() -> TestClient:
    app = FastAPI()

    @app.get("/protected")
    def protected_route(_: None = Depends(require_bearer_token)):
        return {"ok": True}

    return TestClient(app)


def test_require_bearer_token_rejects_missing_header():
    client = _client_with_protected_route()
    response = client.get("/protected")
    assert response.status_code == 401


def test_require_bearer_token_rejects_wrong_token():
    client = _client_with_protected_route()
    response = client.get("/protected", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_require_bearer_token_accepts_correct_token():
    client = _client_with_protected_route()
    response = client.get("/protected", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
```

- [ ] **Step 8: Install `httpx` and run the test**

```bash
pip install -r requirements.txt
pytest tests/api/test_deps.py -v
```

Expected: 3 PASS.

- [ ] **Step 9: Commit**

```bash
git add src/api/deps.py src/api/schemas.py src/api/routers/__init__.py src/api/main.py src/db/repo.py tests/conftest.py tests/api/__init__.py tests/api/test_deps.py requirements.txt
git commit -m "feat: add dashboard API auth/db dependencies, shared schemas, exception plumbing"
```

---

### Task 2: `GET /posts`

**Files:**
- Modify: `src/db/repo.py` (add `list_posts`, `median_post_score`)
- Create: `src/api/routers/posts.py`
- Modify: `src/api/main.py` (include the router)
- Create: `tests/api/test_posts.py`

**Interfaces:**
- Consumes: `src.api.deps.{require_bearer_token, get_db}`, `src.api.schemas.{PostOut, PostsPageOut}` from Task 1.
- Produces: `src.db.repo.list_posts(session, *, category=None, style_variant_id=None, model_used=None, status=None, page=1, page_size=25) -> tuple[list[Post], int]` and `src.db.repo.median_post_score(session, *, category=None, style_variant_id=None, model_used=None, status=None) -> float | None` — not consumed elsewhere in this plan, but same filter-kwarg shape is reused by Task 6's funnel filters for consistency.

- [ ] **Step 1: Write the failing test `tests/api/test_posts.py`**

```python
from src.api.main import app
from src.db.models import Post
from fastapi.testclient import TestClient

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_posts_requires_bearer_token():
    response = client.get("/posts")
    assert response.status_code == 401


def test_get_posts_filters_by_status_and_reports_total(db_session):
    db_session.add(Post(text="published one", category="news", status="published", views=100, score=5))
    db_session.add(Post(text="scheduled one", category="educational", status="scheduled", views=50, score=3))
    db_session.commit()

    response = client.get("/posts", params={"status": "published"}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["text"] == "published one"
    assert body["page"] == 1
    assert body["page_size"] == 25


def test_get_posts_median_score_reflects_filtered_set(db_session):
    db_session.add(Post(text="a", category="news", status="published", score=10))
    db_session.add(Post(text="b", category="news", status="published", score=20))
    db_session.add(Post(text="c", category="educational", status="published", score=1000))
    db_session.commit()

    response = client.get("/posts", params={"category": "news"}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["median_score"] == 15.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/api/test_posts.py -v
```

Expected: FAIL — `/posts` doesn't exist yet (404s, not the asserted status codes).

- [ ] **Step 3: Add `list_posts` and `median_post_score` to `src/db/repo.py`**

Add near the bottom of the file (don't touch existing functions), after the necessary import additions at the top — add `Post` to the existing `from src.db.models import ...` line rather than duplicating the import:

```python
def list_posts(
    session: Session,
    *,
    category: str | None = None,
    style_variant_id: int | None = None,
    model_used: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[Post], int]:
    stmt = select(Post)
    if category is not None:
        stmt = stmt.where(Post.category == category)
    if style_variant_id is not None:
        stmt = stmt.where(Post.style_variant_id == style_variant_id)
    if model_used is not None:
        stmt = stmt.where(Post.model_used == model_used)
    if status is not None:
        stmt = stmt.where(Post.status == status)

    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = session.execute(
        stmt.order_by(Post.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return list(items), total


def median_post_score(
    session: Session,
    *,
    category: str | None = None,
    style_variant_id: int | None = None,
    model_used: str | None = None,
    status: str | None = None,
) -> float | None:
    stmt = select(func.percentile_cont(0.5).within_group(Post.score))
    if category is not None:
        stmt = stmt.where(Post.category == category)
    if style_variant_id is not None:
        stmt = stmt.where(Post.style_variant_id == style_variant_id)
    if model_used is not None:
        stmt = stmt.where(Post.model_used == model_used)
    if status is not None:
        stmt = stmt.where(Post.status == status)
    result = session.execute(stmt).scalar_one_or_none()
    return float(result) if result is not None else None
```

Update the top-of-file import line from:

```python
from src.db.models import AgentRun, AgentStep, DailyLimit, DailySpend, LlmCall
```

to:

```python
from src.db.models import AgentRun, AgentStep, DailyLimit, DailySpend, LlmCall, Post
```

- [ ] **Step 4: Write `src/api/routers/posts.py`**

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import PostsPageOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/posts", response_model=PostsPageOut)
def get_posts(
    category: str | None = None,
    style_variant_id: int | None = None,
    model_used: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PostsPageOut:
    filters = dict(category=category, style_variant_id=style_variant_id, model_used=model_used, status=status)
    items, total = repo.list_posts(db, page=page, page_size=page_size, **filters)
    median = repo.median_post_score(db, **filters)
    return PostsPageOut(items=items, total=total, page=page, page_size=page_size, median_score=median)
```

- [ ] **Step 5: Wire the router into `src/api/main.py`**

Add near the top, with the other imports:

```python
from src.api.routers import posts
```

Add directly below `app = FastAPI()`:

```python
app.include_router(posts.router)
```

- [ ] **Step 6: Run the test**

```bash
pytest tests/api/test_posts.py -v
```

Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/db/repo.py src/api/routers/posts.py src/api/main.py tests/api/test_posts.py
git commit -m "feat: add GET /posts dashboard endpoint with filters, pagination, median score"
```

---

### Task 3: `GET /runs`, `GET /runs/{id}/steps`

**Files:**
- Modify: `src/db/repo.py` (add `list_agent_runs`, `list_agent_steps`)
- Create: `src/api/routers/runs.py`
- Modify: `src/api/main.py`
- Create: `tests/api/test_runs.py`

**Interfaces:**
- Consumes: `src.api.deps.{require_bearer_token, get_db}`, `src.api.schemas.{AgentRunOut, AgentStepOut}` from Task 1.
- Produces: `src.db.repo.list_agent_runs(session, limit=50) -> list[AgentRun]`, `src.db.repo.list_agent_steps(session, run_id) -> list[AgentStep]` — not consumed elsewhere in this plan.

- [ ] **Step 1: Write the failing test `tests/api/test_runs.py`**

```python
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import AgentRun, AgentStep

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_runs_requires_bearer_token():
    response = client.get("/runs")
    assert response.status_code == 401


def test_get_runs_returns_newest_first(db_session):
    older = AgentRun(agent="content", trigger="cron", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status="ok")
    newer = AgentRun(agent="content", trigger="cron", started_at=datetime(2026, 2, 1, tzinfo=timezone.utc), status="ok")
    db_session.add_all([older, newer])
    db_session.commit()

    response = client.get("/runs", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["id"] == newer.id
    assert body[1]["id"] == older.id


def test_get_run_steps_returns_steps_in_order(db_session):
    run = AgentRun(agent="content", trigger="cron", started_at=datetime.now(timezone.utc), status="running")
    db_session.add(run)
    db_session.flush()
    db_session.add(AgentStep(run_id=run.id, step_no=2, thought="second", tool_name="save_draft"))
    db_session.add(AgentStep(run_id=run.id, step_no=1, thought="first", tool_name="get_playbook"))
    db_session.commit()

    response = client.get(f"/runs/{run.id}/steps", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert [s["step_no"] for s in body] == [1, 2]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/api/test_runs.py -v
```

Expected: FAIL (404 on both routes).

- [ ] **Step 3: Add `list_agent_runs` and `list_agent_steps` to `src/db/repo.py`**

```python
def list_agent_runs(session: Session, limit: int = 50) -> list[AgentRun]:
    stmt = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


def list_agent_steps(session: Session, run_id: int) -> list[AgentStep]:
    stmt = select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.step_no.asc())
    return list(session.execute(stmt).scalars().all())
```

(`AgentRun` and `AgentStep` are already imported at the top of `repo.py` from Block 0/1 — no import change needed.)

- [ ] **Step 4: Write `src/api/routers/runs.py`**

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import AgentRunOut, AgentStepOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/runs", response_model=list[AgentRunOut])
def get_runs(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[AgentRunOut]:
    return repo.list_agent_runs(db, limit=limit)


@router.get("/runs/{run_id}/steps", response_model=list[AgentStepOut])
def get_run_steps(run_id: int, db: Session = Depends(get_db)) -> list[AgentStepOut]:
    return repo.list_agent_steps(db, run_id)
```

- [ ] **Step 5: Wire the router into `src/api/main.py`**

Add to the imports:

```python
from src.api.routers import runs
```

Add below `app.include_router(posts.router)`:

```python
app.include_router(runs.router)
```

- [ ] **Step 6: Run the test**

```bash
pytest tests/api/test_runs.py -v
```

Expected: 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/db/repo.py src/api/routers/runs.py src/api/main.py tests/api/test_runs.py
git commit -m "feat: add GET /runs and GET /runs/{id}/steps dashboard endpoints"
```

---

### Task 4: `GET /styles`, `POST /styles/{id}/approve`, `POST /styles/{id}/reject`

**Files:**
- Modify: `src/db/repo.py` (add `list_style_variants`, `approve_style_variant`, `reject_style_variant`)
- Create: `src/api/routers/styles.py`
- Modify: `src/api/main.py`
- Create: `tests/api/test_styles.py`

**Interfaces:**
- Consumes: `src.api.deps.{require_bearer_token, get_db}`, `src.api.schemas.StyleVariantOut`, `src.db.repo.{InvalidStateTransition, RetirementBlocked}` from Task 1.
- Produces: `src.db.repo.list_style_variants(session) -> list[StyleVariant]`, `src.db.repo.approve_style_variant(session, variant_id) -> StyleVariant`, `src.db.repo.reject_style_variant(session, variant_id) -> StyleVariant` — not consumed elsewhere in this plan.

This is the most involved task: approve must retire the worse-median currently-`active` variant when there are already 2 active, but only if that variant's `posts_n >= 20` — otherwise it raises `RetirementBlocked` (SPEC.md §7, D8, D9).

- [ ] **Step 1: Write the failing test `tests/api/test_styles.py`**

```python
from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import StyleVariant

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_styles_requires_bearer_token():
    response = client.get("/styles")
    assert response.status_code == 401


def test_get_styles_returns_all_variants(db_session):
    db_session.add(StyleVariant(name="v1", genome="g1", status="active", created_by="human"))
    db_session.commit()

    response = client.get("/styles", headers=AUTH)
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_approve_promotes_draft_to_active_when_fewer_than_two_active(db_session):
    draft = StyleVariant(name="v-new", genome="g", status="draft", created_by="analyst", rationale="better hooks")
    db_session.add(draft)
    db_session.commit()

    response = client.post(f"/styles/{draft.id}/approve", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "active"


def test_approve_retires_worse_median_active_variant_when_guard_satisfied(db_session):
    weak = StyleVariant(name="weak", genome="g", status="active", created_by="human", posts_n=25, median_score=10)
    strong = StyleVariant(name="strong", genome="g", status="active", created_by="human", posts_n=25, median_score=50)
    candidate = StyleVariant(name="candidate", genome="g", status="draft", created_by="analyst", rationale="radical rewrite")
    db_session.add_all([weak, strong, candidate])
    db_session.commit()

    response = client.post(f"/styles/{candidate.id}/approve", headers=AUTH)
    assert response.status_code == 200

    db_session.refresh(weak)
    db_session.refresh(strong)
    assert weak.status == "retired"
    assert strong.status == "active"


def test_approve_blocked_when_retirement_candidate_has_too_few_posts(db_session):
    weak = StyleVariant(name="weak", genome="g", status="active", created_by="human", posts_n=5, median_score=10)
    strong = StyleVariant(name="strong", genome="g", status="active", created_by="human", posts_n=30, median_score=50)
    candidate = StyleVariant(name="candidate", genome="g", status="draft", created_by="analyst")
    db_session.add_all([weak, strong, candidate])
    db_session.commit()

    response = client.post(f"/styles/{candidate.id}/approve", headers=AUTH)
    assert response.status_code == 422

    db_session.refresh(weak)
    assert weak.status == "active"


def test_approve_on_non_draft_returns_409(db_session):
    already_active = StyleVariant(name="a", genome="g", status="active", created_by="human")
    db_session.add(already_active)
    db_session.commit()

    response = client.post(f"/styles/{already_active.id}/approve", headers=AUTH)
    assert response.status_code == 409


def test_reject_sets_status_rejected(db_session):
    draft = StyleVariant(name="v-new", genome="g", status="draft", created_by="analyst")
    db_session.add(draft)
    db_session.commit()

    response = client.post(f"/styles/{draft.id}/reject", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/api/test_styles.py -v
```

Expected: FAIL (404s).

- [ ] **Step 3: Add the three functions to `src/db/repo.py`**

```python
def list_style_variants(session: Session) -> list[StyleVariant]:
    stmt = select(StyleVariant).order_by(StyleVariant.created_at.desc())
    return list(session.execute(stmt).scalars().all())


def approve_style_variant(session: Session, variant_id: int) -> StyleVariant:
    variant = session.get(StyleVariant, variant_id)
    if variant is None or variant.status != "draft":
        raise InvalidStateTransition(f"style_variant {variant_id} is not in a pending 'draft' state")

    active = list(session.execute(select(StyleVariant).where(StyleVariant.status == "active")).scalars().all())
    if len(active) >= 2:
        def _score(v: StyleVariant) -> float:
            return float(v.median_score) if v.median_score is not None else float("-inf")

        worst = min(active, key=_score)
        if (worst.posts_n or 0) < 20:
            raise RetirementBlocked(
                f"cannot retire style_variant {worst.id}: posts_n={worst.posts_n or 0} < 20"
            )
        worst.status = "retired"

    variant.status = "active"
    session.flush()
    return variant


def reject_style_variant(session: Session, variant_id: int) -> StyleVariant:
    variant = session.get(StyleVariant, variant_id)
    if variant is None or variant.status != "draft":
        raise InvalidStateTransition(f"style_variant {variant_id} is not in a pending 'draft' state")
    variant.status = "rejected"
    session.flush()
    return variant
```

Update the top-of-file import line to add `StyleVariant`:

```python
from src.db.models import AgentRun, AgentStep, DailyLimit, DailySpend, LlmCall, Post, StyleVariant
```

- [ ] **Step 4: Write `src/api/routers/styles.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import StyleVariantOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/styles", response_model=list[StyleVariantOut])
def get_styles(db: Session = Depends(get_db)) -> list[StyleVariantOut]:
    return repo.list_style_variants(db)


@router.post("/styles/{variant_id}/approve", response_model=StyleVariantOut)
def approve_style(variant_id: int, db: Session = Depends(get_db)) -> StyleVariantOut:
    return repo.approve_style_variant(db, variant_id)


@router.post("/styles/{variant_id}/reject", response_model=StyleVariantOut)
def reject_style(variant_id: int, db: Session = Depends(get_db)) -> StyleVariantOut:
    return repo.reject_style_variant(db, variant_id)
```

- [ ] **Step 5: Wire the router into `src/api/main.py`**

Add to the imports:

```python
from src.api.routers import styles
```

Add below `app.include_router(runs.router)`:

```python
app.include_router(styles.router)
```

- [ ] **Step 6: Run the test**

```bash
pytest tests/api/test_styles.py -v
```

Expected: 7 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/db/repo.py src/api/routers/styles.py src/api/main.py tests/api/test_styles.py
git commit -m "feat: add GET /styles and approve/reject endpoints with retirement guard"
```

---

### Task 5: `GET /playbook`, `POST /playbook/{id}/approve`, `POST /playbook/{id}/reject`

**Files:**
- Modify: `src/db/repo.py` (add `list_playbook_rules`, `approve_playbook_rule`, `reject_playbook_rule`)
- Create: `src/api/routers/playbook.py`
- Modify: `src/api/main.py`
- Create: `tests/api/test_playbook.py`

**Interfaces:**
- Consumes: `src.api.deps.{require_bearer_token, get_db}`, `src.api.schemas.PlaybookRuleOut`, `src.db.repo.InvalidStateTransition` from Task 1.
- Produces: `src.db.repo.list_playbook_rules(session) -> list[PlaybookRule]`, `src.db.repo.approve_playbook_rule(session, rule_id) -> PlaybookRule`, `src.db.repo.reject_playbook_rule(session, rule_id) -> PlaybookRule` — not consumed elsewhere in this plan.

- [ ] **Step 1: Write the failing test `tests/api/test_playbook.py`**

```python
from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import PlaybookRule

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_playbook_requires_bearer_token():
    response = client.get("/playbook")
    assert response.status_code == 401


def test_get_playbook_returns_all_rules(db_session):
    db_session.add(PlaybookRule(rule_text="post at 9am", status="confirmed", version=1))
    db_session.commit()

    response = client.get("/playbook", headers=AUTH)
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_approve_moves_proposed_to_testing(db_session):
    rule = PlaybookRule(rule_text="new rule", status="proposed", version=1, hypothesis="h")
    db_session.add(rule)
    db_session.commit()

    response = client.post(f"/playbook/{rule.id}/approve", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "testing"


def test_approve_on_non_proposed_returns_409(db_session):
    rule = PlaybookRule(rule_text="already testing", status="testing", version=1)
    db_session.add(rule)
    db_session.commit()

    response = client.post(f"/playbook/{rule.id}/approve", headers=AUTH)
    assert response.status_code == 409


def test_reject_sets_status_rejected(db_session):
    rule = PlaybookRule(rule_text="bad idea", status="proposed", version=1)
    db_session.add(rule)
    db_session.commit()

    response = client.post(f"/playbook/{rule.id}/reject", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
```

Note: `PlaybookRule` has no `rationale` column in `src/db/models.py` (SPEC.md §8 doesn't define one on this table) — the design doc's "rationale on Playbook screen" point (1.3) is served by `hypothesis` instead, which the test above uses.

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/api/test_playbook.py -v
```

Expected: FAIL (404s).

- [ ] **Step 3: Add the three functions to `src/db/repo.py`**

```python
def list_playbook_rules(session: Session) -> list[PlaybookRule]:
    stmt = select(PlaybookRule).order_by(PlaybookRule.introduced_at.desc())
    return list(session.execute(stmt).scalars().all())


def approve_playbook_rule(session: Session, rule_id: int) -> PlaybookRule:
    rule = session.get(PlaybookRule, rule_id)
    if rule is None or rule.status != "proposed":
        raise InvalidStateTransition(f"playbook_rule {rule_id} is not in a pending 'proposed' state")
    rule.status = "testing"
    session.flush()
    return rule


def reject_playbook_rule(session: Session, rule_id: int) -> PlaybookRule:
    rule = session.get(PlaybookRule, rule_id)
    if rule is None or rule.status != "proposed":
        raise InvalidStateTransition(f"playbook_rule {rule_id} is not in a pending 'proposed' state")
    rule.status = "rejected"
    session.flush()
    return rule
```

Update the top-of-file import line to add `PlaybookRule`:

```python
from src.db.models import AgentRun, AgentStep, DailyLimit, DailySpend, LlmCall, PlaybookRule, Post, StyleVariant
```

- [ ] **Step 4: Write `src/api/routers/playbook.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import PlaybookRuleOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/playbook", response_model=list[PlaybookRuleOut])
def get_playbook(db: Session = Depends(get_db)) -> list[PlaybookRuleOut]:
    return repo.list_playbook_rules(db)


@router.post("/playbook/{rule_id}/approve", response_model=PlaybookRuleOut)
def approve_playbook(rule_id: int, db: Session = Depends(get_db)) -> PlaybookRuleOut:
    return repo.approve_playbook_rule(db, rule_id)


@router.post("/playbook/{rule_id}/reject", response_model=PlaybookRuleOut)
def reject_playbook(rule_id: int, db: Session = Depends(get_db)) -> PlaybookRuleOut:
    return repo.reject_playbook_rule(db, rule_id)
```

- [ ] **Step 5: Wire the router into `src/api/main.py`**

Add to the imports:

```python
from src.api.routers import playbook
```

Add below `app.include_router(styles.router)`:

```python
app.include_router(playbook.router)
```

- [ ] **Step 6: Run the test**

```bash
pytest tests/api/test_playbook.py -v
```

Expected: 5 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/db/repo.py src/api/routers/playbook.py src/api/main.py tests/api/test_playbook.py
git commit -m "feat: add GET /playbook and approve/reject endpoints"
```

---

### Task 6: `GET /funnel`

**Files:**
- Modify: `src/db/repo.py` (add `get_funnel`)
- Create: `src/api/routers/funnel.py`
- Modify: `src/api/main.py`
- Create: `tests/api/test_funnel.py`

**Interfaces:**
- Consumes: `src.api.deps.{require_bearer_token, get_db}`, `src.api.schemas.FunnelMonthOut` from Task 1.
- Produces: `src.db.repo.get_funnel(session, months=6) -> list[dict]` where each dict has keys `month` (`"YYYY-MM"` string), `posts`, `views`, `replies`, `conversations`, `leads` (all `int`) — not consumed elsewhere in this plan.

- [ ] **Step 1: Write the failing test `tests/api/test_funnel.py`**

```python
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import Lead, Post, Reply

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_funnel_requires_bearer_token():
    response = client.get("/funnel")
    assert response.status_code == 401


def test_get_funnel_aggregates_by_month(db_session):
    posted = datetime(2026, 3, 15, tzinfo=timezone.utc)
    post = Post(text="p1", category="news", status="published", posted_at=posted, views=100, replies_count=2)
    db_session.add(post)
    db_session.flush()

    db_session.add(Reply(
        threads_reply_id="r1", post_id=post.id, kind="question",
        received_at=posted, responded_at=posted, status="sent",
    ))
    db_session.add(Reply(
        threads_reply_id="r2", post_id=post.id, kind="spam",
        received_at=posted, responded_at=None, status="ignored",
    ))
    db_session.add(Lead(threads_username="u1", created_at=posted))
    db_session.commit()

    response = client.get("/funnel", params={"months": 12}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    march = next(m for m in body if m["month"] == "2026-03")
    assert march["posts"] == 1
    assert march["views"] == 100
    assert march["replies"] == 2
    assert march["conversations"] == 1  # only the responded question counts, not the unresponded spam
    assert march["leads"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/api/test_funnel.py -v
```

Expected: FAIL (404).

- [ ] **Step 3: Add `get_funnel` to `src/db/repo.py`**

```python
def get_funnel(session: Session, months: int = 6) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=31 * months)

    def _key(dt: datetime) -> str:
        return dt.strftime("%Y-%m")

    months_map: dict[str, dict] = {}

    def _bucket(month_key: str) -> dict:
        return months_map.setdefault(
            month_key, {"posts": 0, "views": 0, "replies": 0, "conversations": 0, "leads": 0}
        )

    posts_rows = session.execute(
        select(
            func.date_trunc("month", Post.posted_at).label("month"),
            func.count(Post.id).label("posts"),
            func.coalesce(func.sum(Post.views), 0).label("views"),
            func.coalesce(func.sum(Post.replies_count), 0).label("replies"),
        )
        .where(Post.posted_at.is_not(None), Post.posted_at >= since)
        .group_by(func.date_trunc("month", Post.posted_at))
    ).all()
    for row in posts_rows:
        bucket = _bucket(_key(row.month))
        bucket["posts"] = row.posts
        bucket["views"] = int(row.views)
        bucket["replies"] = int(row.replies)

    conversations_rows = session.execute(
        select(
            func.date_trunc("month", Reply.received_at).label("month"),
            func.count(Reply.id).label("conversations"),
        )
        .where(
            Reply.kind.in_(["question", "objection"]),
            Reply.responded_at.is_not(None),
            Reply.received_at.is_not(None),
            Reply.received_at >= since,
        )
        .group_by(func.date_trunc("month", Reply.received_at))
    ).all()
    for row in conversations_rows:
        _bucket(_key(row.month))["conversations"] = row.conversations

    leads_rows = session.execute(
        select(
            func.date_trunc("month", Lead.created_at).label("month"),
            func.count(Lead.id).label("leads"),
        )
        .where(Lead.created_at >= since)
        .group_by(func.date_trunc("month", Lead.created_at))
    ).all()
    for row in leads_rows:
        _bucket(_key(row.month))["leads"] = row.leads

    return [{"month": month, **data} for month, data in sorted(months_map.items())]
```

Add `timedelta` to the existing `from datetime import date, datetime, timezone` line at the top of `repo.py` (becomes `from datetime import date, datetime, timedelta, timezone`), and add `Lead, Reply` to the models import line:

```python
from src.db.models import AgentRun, AgentStep, DailyLimit, DailySpend, Lead, LlmCall, PlaybookRule, Post, Reply, StyleVariant
```

- [ ] **Step 4: Write `src/api/routers/funnel.py`**

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import FunnelMonthOut
from src.db import repo

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/funnel", response_model=list[FunnelMonthOut])
def get_funnel(
    months: int = Query(default=6, ge=1, le=24),
    db: Session = Depends(get_db),
) -> list[FunnelMonthOut]:
    return repo.get_funnel(db, months=months)
```

- [ ] **Step 5: Wire the router into `src/api/main.py`**

Add to the imports:

```python
from src.api.routers import funnel
```

Add below `app.include_router(playbook.router)`:

```python
app.include_router(funnel.router)
```

- [ ] **Step 6: Run the test**

```bash
pytest tests/api/test_funnel.py -v
```

Expected: 2 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/db/repo.py src/api/routers/funnel.py src/api/main.py tests/api/test_funnel.py
git commit -m "feat: add GET /funnel dashboard endpoint with monthly aggregation"
```

---

### Task 7: `GET /spend`, README update, full-suite check

**Files:**
- Create: `src/api/routers/spend.py`
- Modify: `src/api/main.py`
- Create: `tests/api/test_spend.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `src.db.repo.get_month_to_date_cost_usd` (already exists from Block 0/1), `src.config.load_settings` (already exists), `src.api.schemas.SpendOut` from Task 1.
- Produces: nothing consumed by later tasks in this plan — this is the last backend task. The Frontend plan consumes this endpoint's shape (`{"month_to_date_usd": float, "cap_usd": float}`) by contract, not by import.

- [ ] **Step 1: Write the failing test `tests/api/test_spend.py`**

```python
from datetime import date

from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import DailySpend

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_spend_requires_bearer_token():
    response = client.get("/spend")
    assert response.status_code == 401


def test_get_spend_returns_month_to_date_and_cap(db_session):
    db_session.add(DailySpend(date=date.today(), model="glm-4.7", tokens_in=1000, tokens_out=500, cost_usd=1.23))
    db_session.commit()

    response = client.get("/spend", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["month_to_date_usd"] == 1.23
    assert body["cap_usd"] == 10.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/api/test_spend.py -v
```

Expected: FAIL (404).

- [ ] **Step 3: Write `src/api/routers/spend.py`**

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import SpendOut
from src.config import load_settings
from src.db.repo import get_month_to_date_cost_usd

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/spend", response_model=SpendOut)
def get_spend(db: Session = Depends(get_db)) -> SpendOut:
    settings = load_settings()
    cap_usd = settings["budget"]["hard_stop_usd"]
    spent = get_month_to_date_cost_usd(db)
    return SpendOut(month_to_date_usd=spent, cap_usd=cap_usd)
```

- [ ] **Step 4: Wire the router into `src/api/main.py`**

Add to the imports:

```python
from src.api.routers import spend
```

Add below `app.include_router(funnel.router)`:

```python
app.include_router(spend.router)
```

- [ ] **Step 5: Run the new test**

```bash
pytest tests/api/test_spend.py -v
```

Expected: 2 PASS.

- [ ] **Step 6: Run the full test suite**

```bash
pytest -v
```

Expected: every test in `tests/` (Block 0/1's existing suite plus all `tests/api/` tests added by this plan) passes. If anything outside `tests/api/` fails, stop and diagnose before continuing — this plan should never break existing behavior.

- [ ] **Step 7: Update `README.md`**

Add a new section after the existing "Running tests" section (step 5 in the file):

```markdown
## Dashboard API

Once the stack is up (`docker compose up -d --build`), the dashboard endpoints are
reachable through Caddy at `https://<host>/posts`, `/runs`, `/runs/{id}/steps`,
`/styles`, `/playbook`, `/funnel`, `/spend`. Every route except `/health` requires
`Authorization: Bearer <API_BEARER_TOKEN>` (the same value as your `.env`).

Example:

```
curl -H "Authorization: Bearer $API_BEARER_TOKEN" https://localhost:8443/posts
```

See `docs/superpowers/specs/2026-09-01-block-4-dashboard-design.md` for the full
endpoint list and the frontend that consumes them.
```

- [ ] **Step 8: Commit**

```bash
git add src/api/routers/spend.py src/api/main.py tests/api/test_spend.py README.md
git commit -m "feat: add GET /spend dashboard endpoint, document dashboard API in README"
```
