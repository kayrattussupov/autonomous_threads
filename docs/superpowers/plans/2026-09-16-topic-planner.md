# Topic Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop ContentAgent from fixating on one business sector: before each run, code picks a (sector, category) pair that balances exploiting sectors with good feedback against exploring sectors not written about recently.

**Architecture:** New `posts.sector` column + `sectors` table. A pure-Python `src/content/topic_planner.py` computes weights (feedback × recency, capped share for the leader) and picks an `Assignment`. `scheduler.run_content_agent_if_queue_low` passes it to `ContentAgent(assignment=...)`, which injects it into the prompt and forces sector/category on `save_draft`. API + dashboard expose sector filter and a "Сферы" page; a one-off script backfills existing posts.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, FastAPI, pytest (real Postgres), Next.js 15 / React 19 dashboard.

**Spec:** `docs/superpowers/specs/2026-09-16-topic-planner-design.md`

## Global Constraints

- No new Python or npm dependencies.
- All LLM-facing prompt text is Russian.
- `TOOL_SELECTION_PROMPT` in `src/agents/content.py` contains literal JSON braces — fill its slots with `str.replace`, never `str.format`.
- Sector names are stored normalized: `" ".join(name.split()).lower()` (function `normalize_sector`).
- Categories are exactly: `utp_cta`, `educational`, `news`, `personal`.
- Planner window statuses: `scheduled`, `published`, `needs_review`.
- Sector performance = **mean** score of published posts in the sector, shrunk toward the **overall median** (deviation from spec wording "median", recorded in the spec: a median hides the one-off viral post with leads, which is exactly the feedback to keep exploiting; `max_sector_share` bounds it).
- Tests need Postgres: `export DATABASE_URL=postgresql+psycopg://threads_agent:changeme@localhost:5432/threads_agent_test` (adjust host/port to your container), then `pytest`. Test schema comes from `Base.metadata.create_all`, not Alembic.
- Do NOT run `alembic upgrade head` from the host against the shared dev DB used by the worker/api containers (README "Gotcha"); containers apply migrations on boot via `scripts/boot.sh`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `migrations/versions/0003_add_sectors.py` (create) | `sectors` table, `posts.sector` column + index |
| `src/db/models.py` (modify) | `Sector` model, `Post.sector` |
| `src/db/repo.py` (modify) | sector queries, planner window/scores, `sector` filters, `set_agent_run_output_ref` |
| `config/settings.yaml` (modify) | `topic_planner` block |
| `src/tools/safe_sql.py` (modify) | allow analyst SQL on `sectors` |
| `src/content/__init__.py`, `src/content/topic_planner.py` (create) | weights, choice, `plan_next_post`, `describe_sectors` |
| `src/prompt/assembler.py` (modify) | "best in this sector" vs "best overall" examples |
| `src/agents/content.py` (modify) | assignment in prompt, forced sector/category, LLM-proposed sectors |
| `src/scheduler.py` (modify) | plan before running ContentAgent |
| `src/api/schemas.py`, `src/api/routers/posts.py`, `src/api/routers/sectors.py` (create), `src/api/main.py` | sector filter, `GET /sectors` |
| `dashboard/lib/api-types.ts`, `dashboard/lib/api-client.ts`, `dashboard/app/(dashboard)/posts/page.tsx`, `dashboard/app/(dashboard)/sectors/page.tsx` (create), `dashboard/app/(dashboard)/layout.tsx` | UI |
| `scripts/backfill_post_sectors.py`, `scripts/simulate_topic_planner.py` (create) | one-off backfill, dry-run distribution |

---

### Task 1: Data layer — migration, models, repo helpers, settings

**Files:**
- Create: `migrations/versions/0003_add_sectors.py`
- Modify: `src/db/models.py`, `src/db/repo.py`, `config/settings.yaml`, `src/tools/safe_sql.py:9`
- Test: `tests/db/test_repo_sectors.py`

**Interfaces:**
- Produces:
  - `Sector` model: `id: int`, `name: str` (unique), `source: str` (`seed`|`llm`), `active: bool`, `created_at`
  - `Post.sector: str | None`
  - `repo.get_or_create_sector(session, name: str, source: str) -> tuple[Sector, bool]` (name must already be normalized; bool = created)
  - `repo.get_active_sector_names(session) -> list[str]` (ordered by id)
  - `repo.list_sectors(session) -> list[Sector]` (ordered by id)
  - `repo.get_planner_window(session, n: int) -> list[tuple[str | None, str]]` — `(sector, category)`, newest first
  - `repo.get_published_scores(session) -> list[tuple[str | None, float]]`
  - `repo.get_last_post_at_by_sector(session) -> dict[str, datetime]`
  - `repo.get_top_performers(session, n=5, sector: str | None = None)`
  - `repo.list_posts(..., sector=None)`, `repo.median_post_score(..., sector=None)`
  - `repo.set_agent_run_output_ref(session, run_id: int, output_ref: str) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/db/test_repo_sectors.py`:

```python
from datetime import datetime, timezone

from src.db.models import AgentRun, Post, Sector
from src.db.repo import (
    get_active_sector_names,
    get_last_post_at_by_sector,
    get_or_create_sector,
    get_planner_window,
    get_published_scores,
    get_top_performers,
    list_posts,
    list_sectors,
    median_post_score,
    set_agent_run_output_ref,
)


def test_get_or_create_sector_returns_existing_row_without_duplicating(db_session):
    first, created_first = get_or_create_sector(db_session, "производство", source="seed")
    second, created_second = get_or_create_sector(db_session, "производство", source="llm")
    db_session.commit()

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert second.source == "seed"
    assert db_session.query(Sector).count() == 1


def test_get_active_sector_names_excludes_inactive_and_keeps_insertion_order(db_session):
    db_session.add_all([
        Sector(name="производство", source="seed"),
        Sector(name="архив", source="llm", active=False),
        Sector(name="horeca", source="seed"),
    ])
    db_session.commit()

    assert get_active_sector_names(db_session) == ["производство", "horeca"]
    assert [s.name for s in list_sectors(db_session)] == ["производство", "архив", "horeca"]


def test_get_planner_window_filters_statuses_orders_newest_first_and_limits(db_session):
    db_session.add_all([
        Post(text="old", category="utp_cta", status="published", sector="a",
             created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        Post(text="draft", category="news", status="draft", sector="b",
             created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
        Post(text="review", category="personal", status="needs_review", sector="c",
             created_at=datetime(2026, 1, 3, tzinfo=timezone.utc)),
        Post(text="new", category="educational", status="scheduled", sector=None,
             created_at=datetime(2026, 1, 4, tzinfo=timezone.utc)),
    ])
    db_session.commit()

    assert get_planner_window(db_session, n=2) == [(None, "educational"), ("c", "personal")]
    assert get_planner_window(db_session, n=10) == [(None, "educational"), ("c", "personal"), ("a", "utp_cta")]


def test_get_published_scores_only_published_with_score(db_session):
    db_session.add_all([
        Post(text="1", category="utp_cta", status="published", sector="a", score=10),
        Post(text="2", category="utp_cta", status="published", sector=None, score=2.5),
        Post(text="3", category="utp_cta", status="published", sector="a", score=None),
        Post(text="4", category="utp_cta", status="scheduled", sector="a", score=99),
    ])
    db_session.commit()

    assert sorted(get_published_scores(db_session), key=lambda r: r[1]) == [(None, 2.5), ("a", 10.0)]


def test_get_top_performers_can_filter_by_sector(db_session):
    db_session.add_all([
        Post(text="prod", category="utp_cta", status="published", sector="производство", score=125),
        Post(text="log", category="utp_cta", status="published", sector="логистика", score=5),
    ])
    db_session.commit()

    assert [p.text for p in get_top_performers(db_session, n=5)] == ["prod", "log"]
    assert [p.text for p in get_top_performers(db_session, n=5, sector="логистика")] == ["log"]


def test_get_last_post_at_by_sector(db_session):
    db_session.add_all([
        Post(text="1", category="utp_cta", status="published", sector="a",
             created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        Post(text="2", category="utp_cta", status="published", sector="a",
             created_at=datetime(2026, 1, 5, tzinfo=timezone.utc)),
        Post(text="3", category="utp_cta", status="published", sector=None,
             created_at=datetime(2026, 1, 9, tzinfo=timezone.utc)),
    ])
    db_session.commit()

    assert get_last_post_at_by_sector(db_session) == {"a": datetime(2026, 1, 5, tzinfo=timezone.utc)}


def test_list_posts_and_median_filter_by_sector(db_session):
    db_session.add_all([
        Post(text="1", category="utp_cta", status="published", sector="a", score=10),
        Post(text="2", category="utp_cta", status="published", sector="a", score=20),
        Post(text="3", category="utp_cta", status="published", sector="b", score=1000),
    ])
    db_session.commit()

    items, total = list_posts(db_session, sector="a")
    assert total == 2
    assert {p.text for p in items} == {"1", "2"}
    assert median_post_score(db_session, sector="a") == 15.0


def test_set_agent_run_output_ref(db_session):
    run = AgentRun(agent="content", trigger="manual", started_at=datetime.now(timezone.utc), status="running")
    db_session.add(run)
    db_session.commit()

    set_agent_run_output_ref(db_session, run.id, '{"sector": "a"}')
    db_session.commit()
    db_session.refresh(run)

    assert run.output_ref == '{"sector": "a"}'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/db/test_repo_sectors.py -v`
Expected: collection ERROR — `ImportError: cannot import name 'Sector' from 'src.db.models'`

- [ ] **Step 3: Add the model and column**

In `src/db/models.py`, add `sector` to `Post` right after `category`:

```python
    category: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str | None] = mapped_column(Text, index=True)
```

Add a new model after `KnowledgeBaseEntry`:

```python
class Sector(Base):
    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true", default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Create the migration**

Create `migrations/versions/0003_add_sectors.py`:

```python
"""add sectors table and posts.sector

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sectors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("posts", sa.Column("sector", sa.Text))
    op.create_index("ix_posts_sector", "posts", ["sector"])


def downgrade():
    op.drop_index("ix_posts_sector", table_name="posts")
    op.drop_column("posts", "sector")
    op.drop_table("sectors")
```

- [ ] **Step 5: Add repo helpers**

In `src/db/repo.py`, add `Sector` to the `from src.db.models import (...)` list (alphabetical, after `Reply`).

Replace `get_top_performers` with:

```python
def get_top_performers(session: Session, n: int = 5, sector: str | None = None) -> list[Post]:
    stmt = select(Post).where(Post.status == "published", Post.score.isnot(None))
    if sector is not None:
        stmt = stmt.where(Post.sector == sector)
    return list(session.execute(stmt.order_by(Post.score.desc()).limit(n)).scalars().all())
```

In `list_posts` and `median_post_score`, add keyword parameter `sector: str | None = None` after `status`, and next to the other filters:

```python
    if sector is not None:
        stmt = stmt.where(Post.sector == sector)
```

Add at the end of the file:

```python
PLANNER_WINDOW_STATUSES = ("scheduled", "published", "needs_review")


def get_or_create_sector(session: Session, name: str, source: str) -> tuple[Sector, bool]:
    existing = session.execute(select(Sector).where(Sector.name == name)).scalar_one_or_none()
    if existing is not None:
        return existing, False
    sector = Sector(name=name, source=source)
    session.add(sector)
    session.flush()
    return sector, True


def get_active_sector_names(session: Session) -> list[str]:
    return list(session.execute(
        select(Sector.name).where(Sector.active.is_(True)).order_by(Sector.id)
    ).scalars().all())


def list_sectors(session: Session) -> list[Sector]:
    return list(session.execute(select(Sector).order_by(Sector.id)).scalars().all())


def get_planner_window(session: Session, n: int) -> list[tuple[str | None, str]]:
    rows = session.execute(
        select(Post.sector, Post.category)
        .where(Post.status.in_(PLANNER_WINDOW_STATUSES))
        .order_by(Post.created_at.desc(), Post.id.desc())
        .limit(n)
    ).all()
    return [(row.sector, row.category) for row in rows]


def get_published_scores(session: Session) -> list[tuple[str | None, float]]:
    rows = session.execute(
        select(Post.sector, Post.score).where(Post.status == "published", Post.score.isnot(None))
    ).all()
    return [(row.sector, float(row.score)) for row in rows]


def get_last_post_at_by_sector(session: Session) -> dict[str, datetime]:
    rows = session.execute(
        select(Post.sector, func.max(Post.created_at))
        .where(Post.sector.isnot(None))
        .group_by(Post.sector)
    ).all()
    return {sector: last for sector, last in rows}


def set_agent_run_output_ref(session: Session, run_id: int, output_ref: str) -> None:
    session.get(AgentRun, run_id).output_ref = output_ref
```

- [ ] **Step 6: Settings and analyst SQL whitelist**

Append to `config/settings.yaml`:

```yaml
topic_planner:
  seed_sectors:
    - производство
    - розничная торговля
    - логистика и доставка
    - клиники и медицина
    - HoReCa
    - недвижимость
    - услуги и сервис
    - онлайн-школы
    - строительство
    - e-commerce
  category_mix:
    utp_cta: 0.5
    educational: 0.3
    personal: 0.1
    news: 0.1
  window_posts: 12
  max_sector_share: 0.5
  prior_strength: 3
  new_sector_prob: 0.1
```

In `src/tools/safe_sql.py:9` add `"sectors"` to `ALLOWED_TABLES`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/db tests/tools -v`
Expected: all PASS (including the new file; if a `tests/tools` test asserts the exact `ALLOWED_TABLES` set, add `"sectors"` there too).

- [ ] **Step 8: Commit**

```bash
git add migrations/versions/0003_add_sectors.py src/db/models.py src/db/repo.py config/settings.yaml src/tools/safe_sql.py tests/db/test_repo_sectors.py
git commit -m "feat: add sectors table, posts.sector and planner repo queries

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Topic planner module

**Files:**
- Create: `src/content/__init__.py` (empty), `src/content/topic_planner.py`
- Test: `tests/content/test_topic_planner.py`

**Interfaces:**
- Consumes: repo functions from Task 1.
- Produces:
  - `@dataclass(frozen=True) Assignment(sector: str | None, category: str, is_new_sector: bool = False)` with `to_json() -> str`
  - `@dataclass PlannerInputs(sectors: list[str], window: list[tuple[str | None, str]], scores_by_sector: dict[str, list[float]], overall_median: float)`
  - `normalize_sector(name: str) -> str`
  - `performance_weight(scores: list[float], overall_median: float, prior_strength: float) -> float`
  - `recency_weight(posts_since: int, window_size: int) -> float`
  - `compute_sector_weights(inputs: PlannerInputs, cfg: dict) -> dict[str, float]`
  - `compute_category_weights(category_mix: dict[str, float], window) -> dict[str, float]`
  - `choose_assignment(inputs: PlannerInputs, cfg: dict, rng) -> Assignment`
  - `sync_seed_sectors(session, names: list[str]) -> None`
  - `load_planner_inputs(session, cfg: dict) -> PlannerInputs`
  - `plan_next_post(session, rng=None, settings: dict | None = None) -> Assignment` (`settings` = the `topic_planner` block)
  - `describe_sectors(session, settings: dict | None = None) -> list[dict]` with keys `name, source, active, published_n, mean_score, median_score, last_post_at, weight, probability`

- [ ] **Step 1: Write the failing tests**

Create `tests/content/test_topic_planner.py`:

```python
import random

import pytest

from src.content.topic_planner import (
    Assignment,
    PlannerInputs,
    choose_assignment,
    compute_category_weights,
    compute_sector_weights,
    describe_sectors,
    normalize_sector,
    performance_weight,
    plan_next_post,
    recency_weight,
)
from src.db.models import Post, Sector

CFG = {
    "seed_sectors": ["Производство", "HoReCa"],
    "category_mix": {"utp_cta": 0.5, "educational": 0.3, "personal": 0.1, "news": 0.1},
    "window_posts": 12,
    "max_sector_share": 0.5,
    "prior_strength": 3,
    "new_sector_prob": 0.0,
}


def _inputs(sectors, window=(), scores=None, median=1.0) -> PlannerInputs:
    return PlannerInputs(sectors=list(sectors), window=list(window), scores_by_sector=scores or {}, overall_median=median)


class _StubRng:
    """random() returns a fixed value; choices() picks the highest weight."""
    def __init__(self, random_value: float):
        self._random_value = random_value

    def random(self):
        return self._random_value

    def choices(self, population, weights, k):
        return [max(zip(population, weights), key=lambda pair: pair[1])[0]]


def test_normalize_sector_collapses_whitespace_and_lowercases():
    assert normalize_sector("  HoReCa   и  Кафе ") == "horeca и кафе"


def test_assignment_to_json_keeps_cyrillic():
    assert Assignment(sector="производство", category="utp_cta").to_json() == (
        '{"sector": "производство", "category": "utp_cta", "is_new_sector": false}'
    )


def test_recency_weight_bounds():
    assert recency_weight(1, 12) == pytest.approx(0.5 + 1 / 12)
    assert recency_weight(12, 12) == pytest.approx(1.5)
    assert recency_weight(50, 12) == pytest.approx(1.5)


def test_performance_weight_shrinks_single_outlier_toward_overall_median():
    weight = performance_weight([125.0], overall_median=1.73, prior_strength=3)
    assert weight == pytest.approx((125 + 3 * 1.73) / 4 / 1.73)
    assert weight < 125 / 1.73


def test_performance_weight_unknown_is_neutral_consistent_is_high_poor_is_floored():
    assert performance_weight([], overall_median=2.0, prior_strength=3) == pytest.approx(1.0)
    assert performance_weight([20.0] * 10, overall_median=2.0, prior_strength=3) == pytest.approx((200 + 6) / 13 / 2)
    assert performance_weight([0.0] * 30, overall_median=2.0, prior_strength=3) == pytest.approx(0.2)


def test_sector_at_or_above_max_share_gets_zero_weight():
    window = [("производство", "utp_cta")] * 6 + [("логистика", "utp_cta")] * 6
    weights = compute_sector_weights(_inputs(["производство", "логистика", "клиники"], window), CFG)
    assert weights["производство"] == 0.0
    assert weights["логистика"] == 0.0
    assert weights["клиники"] > 0


def test_sector_never_written_gets_max_recency_and_recent_one_gets_min():
    window = [("производство", "utp_cta")]
    weights = compute_sector_weights(_inputs(["производство", "клиники"], window), CFG)
    assert weights["клиники"] == pytest.approx(1.5)
    assert weights["производство"] == pytest.approx(0.5 + 1 / 12)


def test_category_under_target_gets_more_weight():
    window = [(None, "utp_cta")] * 12
    weights = compute_category_weights(CFG["category_mix"], window)
    assert weights["utp_cta"] == pytest.approx(0.5)
    assert weights["educational"] == pytest.approx(0.3 / 0.05)


def test_category_weights_on_empty_window_follow_target_mix():
    weights = compute_category_weights(CFG["category_mix"], [])
    assert weights["utp_cta"] / weights["educational"] == pytest.approx(0.5 / 0.3)


def test_choose_assignment_returns_new_sector_slot_when_probability_hits():
    cfg = {**CFG, "new_sector_prob": 0.1}
    assignment = choose_assignment(_inputs(["производство"]), cfg, _StubRng(0.05))
    assert assignment == Assignment(sector=None, category="utp_cta", is_new_sector=True)


def test_choose_assignment_falls_back_to_least_recent_sector_when_all_capped():
    cfg = {**CFG, "max_sector_share": 0.05}
    window = [("производство", "utp_cta"), ("логистика", "utp_cta")]
    assignment = choose_assignment(_inputs(["производство", "логистика"], window), cfg, _StubRng(0.99))
    assert assignment.sector == "логистика"
    assert assignment.is_new_sector is False


def test_simulation_keeps_leader_frequent_but_bounded_and_covers_all_sectors():
    sectors = ["производство"] + [f"сфера {i}" for i in range(9)]
    inputs = _inputs(sectors, scores={"производство": [125.0, 13.9, 1.8, 1.9]}, median=1.73)
    rng = random.Random(42)
    picks = []
    for _ in range(300):
        assignment = choose_assignment(inputs, CFG, rng)
        picks.append(assignment.sector)
        inputs.window = [(assignment.sector, assignment.category)] + inputs.window[: CFG["window_posts"] - 1]

    leader_share = picks.count("производство") / len(picks)
    assert 0.15 <= leader_share <= 0.51
    assert set(picks) == set(sectors)


def test_plan_next_post_syncs_seed_sectors_idempotently(db_session):
    first = plan_next_post(db_session, rng=random.Random(1), settings=CFG)
    plan_next_post(db_session, rng=random.Random(2), settings=CFG)
    db_session.commit()

    sectors = db_session.query(Sector).order_by(Sector.id).all()
    assert [s.name for s in sectors] == ["производство", "horeca"]
    assert all(s.source == "seed" for s in sectors)
    assert first.sector in {"производство", "horeca"}
    assert first.category in CFG["category_mix"]


def test_plan_next_post_does_not_reactivate_deactivated_seed_sector(db_session):
    db_session.add(Sector(name="horeca", source="seed", active=False))
    db_session.commit()

    for seed in range(20):
        assert plan_next_post(db_session, rng=random.Random(seed), settings=CFG).sector == "производство"


def test_plan_next_post_caps_sector_that_fills_half_the_window(db_session):
    for i in range(6):
        db_session.add(Post(text=f"p{i}", category="utp_cta", status="published", sector="производство", score=100))
    db_session.commit()

    assert plan_next_post(db_session, rng=random.Random(0), settings=CFG).sector == "horeca"


def test_describe_sectors_reports_stats_and_probabilities(db_session):
    db_session.add_all([
        Sector(name="производство", source="seed"),
        Sector(name="horeca", source="seed"),
        Sector(name="архив", source="llm", active=False),
        Post(text="a", category="utp_cta", status="published", sector="производство", score=10),
        Post(text="b", category="utp_cta", status="published", sector="производство", score=20),
    ])
    db_session.commit()

    rows = {row["name"]: row for row in describe_sectors(db_session, settings=CFG)}

    assert rows["производство"]["published_n"] == 2
    assert rows["производство"]["mean_score"] == pytest.approx(15.0)
    assert rows["производство"]["median_score"] == pytest.approx(15.0)
    assert rows["производство"]["last_post_at"] is not None
    assert rows["horeca"]["published_n"] == 0
    assert rows["horeca"]["mean_score"] is None
    assert rows["horeca"]["weight"] > 0
    assert rows["архив"]["weight"] is None
    assert rows["архив"]["probability"] is None
    assert sum(r["probability"] or 0 for r in rows.values()) == pytest.approx(1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/content/test_topic_planner.py -v`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'src.content'`

- [ ] **Step 3: Implement the planner**

Create empty `src/content/__init__.py`.

Create `src/content/topic_planner.py`:

```python
"""Chooses the (sector, category) for the next ContentAgent run. Pure Python,
no LLM: sectors with good feedback keep getting posts, but a share cap and a
recency bonus force regular posts about other sectors too.
See docs/superpowers/specs/2026-09-16-topic-planner-design.md."""
import json
import random
import statistics
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from src.config import load_settings
from src.db.repo import (
    get_active_sector_names,
    get_last_post_at_by_sector,
    get_or_create_sector,
    get_planner_window,
    get_published_scores,
    list_sectors,
)

MIN_PERF_WEIGHT = 0.2
MIN_CATEGORY_SHARE = 0.05


@dataclass(frozen=True)
class Assignment:
    sector: str | None
    category: str
    is_new_sector: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class PlannerInputs:
    sectors: list[str]
    window: list[tuple[str | None, str]]  # (sector, category), newest first
    scores_by_sector: dict[str, list[float]]
    overall_median: float


def normalize_sector(name: str) -> str:
    return " ".join(name.split()).lower()


def performance_weight(scores: list[float], overall_median: float, prior_strength: float) -> float:
    """Sector mean score shrunk toward the overall median, relative to it.
    Mean rather than median on purpose: a single viral post that brought
    leads IS the feedback worth exploiting; max_sector_share bounds it."""
    n = len(scores)
    mean = statistics.fmean(scores) if n else overall_median
    shrunk = (n * mean + prior_strength * overall_median) / (n + prior_strength)
    return max(shrunk / overall_median, MIN_PERF_WEIGHT)


def _posts_since(sector: str, window: list[tuple[str | None, str]], window_size: int) -> int:
    for index, (window_sector, _) in enumerate(window):
        if window_sector == sector:
            return index + 1
    return window_size


def recency_weight(posts_since: int, window_size: int) -> float:
    return 0.5 + min(posts_since, window_size) / window_size


def compute_sector_weights(inputs: PlannerInputs, cfg: dict) -> dict[str, float]:
    window_size = cfg["window_posts"]
    weights = {}
    for sector in inputs.sectors:
        share = sum(1 for window_sector, _ in inputs.window if window_sector == sector) / window_size
        if share >= cfg["max_sector_share"]:
            weights[sector] = 0.0
            continue
        perf = performance_weight(inputs.scores_by_sector.get(sector, []), inputs.overall_median, cfg["prior_strength"])
        weights[sector] = perf * recency_weight(_posts_since(sector, inputs.window, window_size), window_size)
    return weights


def compute_category_weights(category_mix: dict[str, float], window: list[tuple[str | None, str]]) -> dict[str, float]:
    total = len(window)
    weights = {}
    for category, target_share in category_mix.items():
        actual_share = sum(1 for _, window_category in window if window_category == category) / total if total else 0.0
        weights[category] = target_share / max(actual_share, MIN_CATEGORY_SHARE)
    return weights


def _weighted_choice(weights: dict[str, float], rng) -> str:
    names = list(weights)
    return rng.choices(names, weights=[weights[name] for name in names], k=1)[0]


def choose_assignment(inputs: PlannerInputs, cfg: dict, rng) -> Assignment:
    category = _weighted_choice(compute_category_weights(cfg["category_mix"], inputs.window), rng)
    if not inputs.sectors or rng.random() < cfg["new_sector_prob"]:
        return Assignment(sector=None, category=category, is_new_sector=True)

    weights = compute_sector_weights(inputs, cfg)
    if not any(weights.values()):
        window_size = cfg["window_posts"]
        sector = max(inputs.sectors, key=lambda s: _posts_since(s, inputs.window, window_size))
        return Assignment(sector=sector, category=category)
    return Assignment(sector=_weighted_choice(weights, rng), category=category)


def sync_seed_sectors(session: Session, names: list[str]) -> None:
    for name in names:
        get_or_create_sector(session, normalize_sector(name), source="seed")


def load_planner_inputs(session: Session, cfg: dict) -> PlannerInputs:
    scores = get_published_scores(session)
    scores_by_sector: dict[str, list[float]] = {}
    for sector, score in scores:
        if sector:
            scores_by_sector.setdefault(sector, []).append(score)
    all_scores = [score for _, score in scores]
    overall_median = statistics.median(all_scores) if all_scores else 1.0
    if overall_median <= 0:
        overall_median = 1.0
    return PlannerInputs(
        sectors=get_active_sector_names(session),
        window=get_planner_window(session, cfg["window_posts"]),
        scores_by_sector=scores_by_sector,
        overall_median=overall_median,
    )


def _settings(settings: dict | None) -> dict:
    return settings if settings is not None else load_settings()["topic_planner"]


def plan_next_post(session: Session, rng=None, settings: dict | None = None) -> Assignment:
    cfg = _settings(settings)
    sync_seed_sectors(session, cfg["seed_sectors"])
    session.flush()
    return choose_assignment(load_planner_inputs(session, cfg), cfg, rng or random.Random())


def describe_sectors(session: Session, settings: dict | None = None) -> list[dict]:
    """Read-only stats for the dashboard; does not sync seeds."""
    cfg = _settings(settings)
    inputs = load_planner_inputs(session, cfg)
    weights = compute_sector_weights(inputs, cfg)
    total_weight = sum(weights.values())
    last_post_at = get_last_post_at_by_sector(session)

    rows = []
    for sector in list_sectors(session):
        scores = inputs.scores_by_sector.get(sector.name, [])
        weight = weights.get(sector.name) if sector.active else None
        rows.append({
            "name": sector.name,
            "source": sector.source,
            "active": sector.active,
            "published_n": len(scores),
            "mean_score": statistics.fmean(scores) if scores else None,
            "median_score": statistics.median(scores) if scores else None,
            "last_post_at": last_post_at.get(sector.name),
            "weight": weight,
            "probability": (weight / total_weight) if weight is not None and total_weight > 0 else None,
        })
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/content/test_topic_planner.py -v`
Expected: all PASS. If `test_simulation_keeps_leader_frequent_but_bounded_and_covers_all_sectors` fails on the lower bound only, print `leader_share` and report it — do not loosen bounds silently.

- [ ] **Step 5: Commit**

```bash
git add src/content tests/content
git commit -m "feat: add topic planner balancing sector feedback and exploration

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Prompt assembler — sector-specific examples

**Files:**
- Modify: `src/prompt/assembler.py`
- Test: `tests/prompt/test_assembler.py`

**Interfaces:**
- Produces: `assemble_system_prompt(constitution, knowledge_base, active_genome, playbook_rules, swipe_examples, top_posts, sector_top_posts: list[str] | None = None, sector: str | None = None) -> str`

- [ ] **Step 1: Write the failing test**

Append to `tests/prompt/test_assembler.py`:

```python
def test_assemble_system_prompt_separates_sector_best_from_overall_best():
    result = assemble_system_prompt(
        constitution="C",
        knowledge_base={"niche": "automation"},
        active_genome="G",
        playbook_rules=[],
        swipe_examples=[],
        top_posts=["лучший в производстве"],
        sector_top_posts=["лучший в логистике"],
        sector="логистика и доставка",
    )

    sector_header = "## Твои лучшие посты в сфере «логистика и доставка»"
    overall_header = "## Твои лучшие посты в целом (переноси приёмы и структуру, а не тему)"
    assert sector_header in result
    assert overall_header in result
    assert result.index(sector_header) < result.index("лучший в логистике") < result.index(overall_header)
    assert result.index(overall_header) < result.index("лучший в производстве")


def test_assemble_system_prompt_without_sector_keeps_plain_best_posts_header():
    result = assemble_system_prompt(
        constitution="C",
        knowledge_base={"niche": "automation"},
        active_genome="G",
        playbook_rules=[],
        swipe_examples=[],
        top_posts=["мой лучший пост"],
    )

    assert "## Твои лучшие посты\n" in result
    assert "в сфере" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/prompt/test_assembler.py -v`
Expected: FAIL — `TypeError: assemble_system_prompt() got an unexpected keyword argument 'sector_top_posts'`

- [ ] **Step 3: Implement**

Replace `_render_examples` and `assemble_system_prompt` in `src/prompt/assembler.py`:

```python
def _render_examples(
    swipe_examples: list[str],
    top_posts: list[str],
    sector_top_posts: list[str],
    sector: str | None,
) -> str:
    parts = ["# Примеры"]
    if sector_top_posts:
        parts.append(f"## Твои лучшие посты в сфере «{sector}»")
        parts.extend(f"- {p}" for p in sector_top_posts)
    if top_posts:
        if sector:
            parts.append("## Твои лучшие посты в целом (переноси приёмы и структуру, а не тему)")
        else:
            parts.append("## Твои лучшие посты")
        parts.extend(f"- {p}" for p in top_posts)
    if swipe_examples:
        parts.append("## Зашедшие посты в нише (чужие)")
        parts.extend(f"- {p}" for p in swipe_examples)
    if len(parts) == 1:
        parts.append("(примеров пока нет)")
    return "\n".join(parts)


def assemble_system_prompt(
    constitution: str,
    knowledge_base: dict,
    active_genome: str,
    playbook_rules: list[str],
    swipe_examples: list[str],
    top_posts: list[str],
    sector_top_posts: list[str] | None = None,
    sector: str | None = None,
) -> str:
    return "\n\n".join([
        constitution,
        _render_knowledge_base(knowledge_base),
        active_genome,
        _render_playbook(playbook_rules),
        _render_examples(swipe_examples, top_posts, sector_top_posts or [], sector),
    ])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/prompt -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/prompt/assembler.py tests/prompt/test_assembler.py
git commit -m "feat: show best-in-sector posts separately from overall best in prompt

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: ContentAgent assignment + scheduler wiring

**Files:**
- Modify: `src/agents/content.py`, `src/scheduler.py`
- Test: `tests/agents/test_content.py`, `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `Assignment`, `normalize_sector`, `plan_next_post` (Task 2); `get_or_create_sector`, `get_active_sector_names`, `get_top_performers(sector=)`, `set_agent_run_output_ref` (Task 1); `assemble_system_prompt(sector_top_posts=, sector=)` (Task 3).
- Produces: `ContentAgent(llm_client=None, assignment: Assignment | None = None, **kwargs)`; `save_draft` tool accepts optional `sector`.

- [ ] **Step 1: Write the failing content-agent tests**

In `tests/agents/test_content.py`:

1. Change the imports to:

```python
import json
from unittest.mock import MagicMock

from src.agents.content import ContentAgent
from src.content.topic_planner import Assignment
from src.db.models import AgentRun, Post, Sector, StyleVariant
from src.llm.client import LLMResponse
```

2. Make `_ScriptedLLMClient` record messages — replace its `__init__` and `complete` with:

```python
    def __init__(self, script: list[str]):
        self._script = list(script)
        self.calls = []
        self.messages = []

    def complete(self, role, messages, run_id=None, step_no=None):
        self.calls.append(role)
        self.messages.append(messages)
        text = self._script.pop(0)
        return LLMResponse(text=text, tokens_in=20, tokens_out=5, cost_usd=0.0002, model="glm-4.7", finish_reason="stop")
```

3. Append these helpers and tests:

```python
_CONTENT_SETTINGS = {
    "post_length": {"min_chars": 5, "max_chars": 400, "hard_max_chars": 500},
    "publish_times": ["09:00", "14:00", "20:00"],
    "publish_timezone": "Asia/Almaty",
    "agent_limits": {"max_steps": 8, "max_tokens": 40000, "max_seconds": 120},
}


def _patch_content_env(monkeypatch, critic_calls: list | None = None):
    monkeypatch.setattr("src.agents.content.load_settings", lambda: _CONTENT_SETTINGS)

    def _critic(**kwargs):
        if critic_calls is not None:
            critic_calls.append(kwargs)
        return {"pass": True, "issues": [], "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0}

    monkeypatch.setattr("src.agents.content.run_style_critic", _critic)


def test_content_agent_puts_assignment_into_prompt_and_records_it_on_run(db_session, monkeypatch):
    _seed_active_style(db_session)
    _patch_content_env(monkeypatch)
    llm = _ScriptedLLMClient([_tool_call_json("save_draft", {"text": "пост про клиники", "category": "utp_cta"})])
    assignment = Assignment(sector="клиники и медицина", category="utp_cta")

    run = ContentAgent(llm_client=llm, assignment=assignment).run(trigger="manual")

    user_prompt = llm.messages[0][1]["content"]
    assert "Сфера бизнеса: клиники и медицина" in user_prompt
    assert "Категория: utp_cta" in user_prompt
    assert "{assignment}" not in user_prompt
    assert json.loads(run.output_ref) == {"sector": "клиники и медицина", "category": "utp_cta", "is_new_sector": False}


def test_content_agent_forces_assigned_sector_and_category_over_llm_args(db_session, monkeypatch):
    _seed_active_style(db_session)
    _patch_content_env(monkeypatch)
    script = [_tool_call_json("save_draft", {
        "text": "пост про склад", "category": "personal", "sector": "производство",
    })]
    assignment = Assignment(sector="логистика и доставка", category="utp_cta")

    ContentAgent(llm_client=_ScriptedLLMClient(script), assignment=assignment).run(trigger="manual")

    post = db_session.query(Post).filter_by(text="пост про склад").one()
    assert post.category == "utp_cta"
    assert post.sector == "логистика и доставка"
    assert db_session.query(Sector).count() == 0


def test_content_agent_inserts_llm_proposed_sector_for_new_sector_assignment(db_session, monkeypatch):
    _seed_active_style(db_session)
    db_session.add(Sector(name="производство", source="seed"))
    db_session.commit()
    critic_calls = []
    _patch_content_env(monkeypatch, critic_calls)
    llm = _ScriptedLLMClient([
        _tool_call_json("save_draft", {"text": "без сферы", "category": "educational"}),
        _tool_call_json("save_draft", {"text": "пост про автосервисы", "category": "educational", "sector": "  Автосервисы "}),
    ])
    assignment = Assignment(sector=None, category="educational", is_new_sector=True)

    run = ContentAgent(llm_client=llm, assignment=assignment).run(trigger="manual")

    assert run.status == "ok"
    assert "производство" in llm.messages[0][1]["content"]
    assert len(critic_calls) == 1  # missing-sector rejection never reaches style_critic
    assert db_session.query(Post).filter_by(text="без сферы").count() == 0
    post = db_session.query(Post).filter_by(text="пост про автосервисы").one()
    assert post.sector == "автосервисы"
    assert db_session.query(Sector).filter_by(name="автосервисы").one().source == "llm"


def test_content_agent_system_prompt_lists_best_in_sector_before_best_overall(db_session, monkeypatch):
    _seed_active_style(db_session)
    db_session.add_all([
        Post(text="лучший в логистике", category="utp_cta", status="published", sector="логистика и доставка", score=50),
        Post(text="лучший в производстве", category="utp_cta", status="published", sector="производство", score=125),
    ])
    db_session.commit()
    _patch_content_env(monkeypatch)
    llm = _ScriptedLLMClient([_tool_call_json("save_draft", {"text": "новый пост про доставку", "category": "utp_cta"})])
    assignment = Assignment(sector="логистика и доставка", category="utp_cta")

    ContentAgent(llm_client=llm, assignment=assignment).run(trigger="manual")

    system_prompt = llm.messages[0][0]["content"]
    sector_header = "## Твои лучшие посты в сфере «логистика и доставка»"
    overall_header = "## Твои лучшие посты в целом"
    assert system_prompt.index(sector_header) < system_prompt.index("лучший в логистике") < system_prompt.index(overall_header)


def test_content_agent_without_assignment_keeps_llm_category_and_no_sector(db_session, monkeypatch):
    _seed_active_style(db_session)
    _patch_content_env(monkeypatch)
    llm = _ScriptedLLMClient([_tool_call_json("save_draft", {"text": "ручной запуск", "category": "personal"})])

    run = ContentAgent(llm_client=llm).run(trigger="manual")

    post = db_session.query(Post).filter_by(text="ручной запуск").one()
    assert post.category == "personal"
    assert post.sector is None
    assert run.output_ref is None
    assert "ЗАДАНИЕ НА ЭТОТ ПОСТ" not in llm.messages[0][1]["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_content.py -v`
Expected: new tests FAIL — `TypeError: ContentAgent.__init__() got an unexpected keyword argument 'assignment'` (existing tests still PASS).

- [ ] **Step 3: Implement ContentAgent changes**

In `src/agents/content.py`:

Imports — add:

```python
from src.content.topic_planner import Assignment, normalize_sector
```

and extend the `src.db.repo` import list with `get_active_sector_names`, `get_or_create_sector`, `set_agent_run_output_ref`.

Replace the start of `TOOL_SELECTION_PROMPT` and the two tool lines:

```python
TOOL_SELECTION_PROMPT = """\
{assignment}Ты выбираешь следующее действие. Доступные инструменты:

- get_recent_posts() — последние 30 своих постов, чтобы не повторяться
- get_top_performers(sector) — 5 своих лучших постов по score (sector необязателен:
  с ним — лучшие посты в конкретной сфере)
- get_swipe_examples(topic) — зашедшие чужие посты в нише (topic необязателен)
- web_search(query) — только для category='news', поиск свежих фактов
- verify_source(url) — проверить, что источник реально существует (для news)
- save_draft(text, category, source_url, sector) — сохранить готовый пост (category один из:
  utp_cta, educational, news, personal). source_url необязателен, но ОБЯЗАТЕЛЕН
  для category='news' — сначала получите и проверьте его через verify_source(url).
  sector нужен, только если ЗАДАНИЕ просит выбрать новую сферу
```

(the rest of the prompt text after that line stays unchanged).

Add below `TOOL_SELECTION_PROMPT`:

```python
ASSIGNMENT_TEMPLATE = """\
ЗАДАНИЕ НА ЭТОТ ПОСТ (выбрано планировщиком, не меняй):
- Сфера бизнеса: {sector}
- Категория: {category}
Пиши про конкретную боль и процесс именно этой сферы, который решает автоматизация.
Приёмы из лучших постов других сфер переносить можно, их тему — нет.

"""

NEW_SECTOR_TEMPLATE = """\
ЗАДАНИЕ НА ЭТОТ ПОСТ (выбрано планировщиком, не меняй):
- Сфера бизнеса: выбери сам сферу, которой НЕТ в этом списке: {known}
- Категория: {category}
Пиши про конкретную боль и процесс выбранной сферы, который решает автоматизация.
Название сферы (1-3 слова, по-русски) обязательно передай в save_draft(sector=...).

"""
```

Replace `__init__`:

```python
    def __init__(self, llm_client: LLMClient | None = None, assignment: Assignment | None = None, **kwargs):
        super().__init__(agent_name="content", **kwargs)
        self._llm_client = llm_client or LLMClient()
        self._assignment = assignment
        self._assignment_block: str | None = None
        self._assignment_recorded = False
        self._critic_failures = 0
        self._done = False
        self._system_prompt_cache: str | None = None
        self._active_style = None
```

Replace `_tool_get_top_performers`:

```python
    def _tool_get_top_performers(self, sector: str | None = None):
        with session_scope() as session:
            return [p.text for p in get_top_performers(session, n=5, sector=sector)]
```

In `system_prompt()`, inside the `with session_scope()` block, replace the `top = ...` line and the `assemble_system_prompt(...)` call:

```python
                top = [p.text for p in get_top_performers(session, n=5)]
                sector = self._assignment.sector if self._assignment else None
                sector_top = [p.text for p in get_top_performers(session, n=3, sector=sector)] if sector else []
            self._system_prompt_cache = assemble_system_prompt(
                constitution=constitution,
                knowledge_base=kb,
                active_genome=genome,
                playbook_rules=rules,
                swipe_examples=swipe,
                top_posts=top,
                sector_top_posts=sector_top,
                sector=sector,
            )
```

Add method:

```python
    def _render_assignment(self) -> str:
        if self._assignment is None:
            return ""
        if self._assignment_block is None:
            if self._assignment.is_new_sector:
                with session_scope() as session:
                    known = ", ".join(get_active_sector_names(session)) or "(список пуст)"
                self._assignment_block = NEW_SECTOR_TEMPLATE.format(known=known, category=self._assignment.category)
            else:
                self._assignment_block = ASSIGNMENT_TEMPLATE.format(
                    sector=self._assignment.sector, category=self._assignment.category,
                )
        return self._assignment_block
```

Replace `_tool_save_draft` signature and its first lines (up to and including the `verify_source` check):

```python
    def _tool_save_draft(self, text: str, category: str, source_url: str | None = None, sector: str | None = None):
        if self._assignment is not None:
            category = self._assignment.category
            if self._assignment.is_new_sector:
                sector = normalize_sector(sector) if sector else ""
                if not sector:
                    return {"status": "rejected", "issues": ["ЗАДАНИЕ требует новую сферу: передай её в save_draft(sector=...)"]}
            else:
                sector = self._assignment.sector
        else:
            sector = normalize_sector(sector) if sector else None

        genome = self._active_style.genome if self._active_style else ""
        if category == "news" and source_url and not verify_source(source_url):
            source_url = None
```

In the rest of `_tool_save_draft`, pass `sector=sector` to both `_persist_post(...)` calls.

Replace `_persist_post`:

```python
    def _persist_post(
        self, text: str, category: str, status: str, source_url: str | None = None, sector: str | None = None,
    ) -> dict:
        style_variant_id = self._active_style.id if self._active_style else None
        with session_scope() as session:
            if sector and self._assignment is not None and self._assignment.is_new_sector:
                get_or_create_sector(session, sector, source="llm")
            post = insert_post(
                session,
                text=text,
                category=category,
                sector=sector,
                status=status,
                source_url=source_url,
                style_variant_id=style_variant_id,
                scheduled_at=self._next_publish_slot() if status == "scheduled" else None,
                model_used=self._llm_client._config["roles"]["post_writer"]["model"] if hasattr(self._llm_client, "_config") else None,
            )
            if status == "scheduled" and style_variant_id:
                increment_style_variant_posts_n(session, style_variant_id)
            post_id = post.id
        self._done = True
        return {"status": status, "post_id": post_id}
```

In `decide_next_action`, right after `if self._done: return None`, add:

```python
        if self._assignment is not None and not self._assignment_recorded and self._run_id is not None:
            with session_scope() as session:
                set_agent_run_output_ref(session, self._run_id, self._assignment.to_json())
            self._assignment_recorded = True
```

and replace the `messages = [...]` user-content line with (assignment first, then history — history JSON must not be scanned for the `{assignment}` slot):

```python
            {"role": "user", "content": TOOL_SELECTION_PROMPT.replace("{assignment}", self._render_assignment()).replace("{history}", history_json)},
```

- [ ] **Step 4: Run content tests**

Run: `pytest tests/agents/test_content.py -v`
Expected: all PASS (old and new)

- [ ] **Step 5: Write the failing scheduler test changes**

In `tests/test_scheduler.py`, add import `from src.content.topic_planner import Assignment`, then replace the body of `test_run_content_agent_if_queue_low_runs_agent_when_scheduled_count_below_queue_depth` after `monkeypatch.setattr("src.scheduler.ContentAgent", agent_class)` with:

```python
    assignment = Assignment(sector="производство", category="utp_cta")
    planner = MagicMock(return_value=assignment)
    monkeypatch.setattr("src.scheduler.plan_next_post", planner)

    run_content_agent_if_queue_low()

    planner.assert_called_once()
    agent_class.assert_called_once_with(assignment=assignment)
    agent_instance.run.assert_called_once_with(trigger="queue_low")
```

In `test_run_content_agent_if_queue_low_skips_agent_when_scheduled_count_at_or_above_queue_depth`, before `run_content_agent_if_queue_low()` add:

```python
    planner = MagicMock()
    monkeypatch.setattr("src.scheduler.plan_next_post", planner)
```

and after the existing asserts add `planner.assert_not_called()`.

- [ ] **Step 6: Run to verify failure**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL — `AttributeError: <module 'src.scheduler'> does not have the attribute 'plan_next_post'`

- [ ] **Step 7: Implement scheduler wiring**

In `src/scheduler.py` add import `from src.content.topic_planner import plan_next_post` and replace the last line of `run_content_agent_if_queue_low` (`ContentAgent().run(trigger="queue_low")`) with:

```python
    with session_scope() as session:
        assignment = plan_next_post(session)
    ContentAgent(assignment=assignment).run(trigger="queue_low")
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/test_scheduler.py tests/agents -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add src/agents/content.py src/scheduler.py tests/agents/test_content.py tests/test_scheduler.py
git commit -m "feat: content agent writes to planner-assigned sector and category

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: API — sector filter and GET /sectors

**Files:**
- Modify: `src/api/schemas.py`, `src/api/routers/posts.py`, `src/api/main.py`
- Create: `src/api/routers/sectors.py`
- Test: `tests/api/test_posts.py`, `tests/api/test_sectors.py`

**Interfaces:**
- Consumes: `describe_sectors(session)` (Task 2), `list_posts/median_post_score(sector=)` (Task 1).
- Produces: `GET /posts?sector=...`; `PostOut.sector`; `GET /sectors -> list[SectorOut]` with fields `name, source, active, published_n, mean_score, median_score, last_post_at, weight, probability`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_posts.py`:

```python
def test_get_posts_filters_by_sector_and_returns_sector_field(db_session):
    db_session.add(Post(text="prod", category="utp_cta", status="published", sector="производство", score=10))
    db_session.add(Post(text="log", category="utp_cta", status="published", sector="логистика", score=20))
    db_session.commit()

    response = client.get("/posts", params={"sector": "логистика"}, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["sector"] == "логистика"
    assert body["median_score"] == 20.0
```

Create `tests/api/test_sectors.py`:

```python
from fastapi.testclient import TestClient

from src.api.main import app
from src.db.models import Post, Sector

client = TestClient(app)

AUTH = {"Authorization": "Bearer test-token"}


def test_get_sectors_requires_bearer_token():
    response = client.get("/sectors")
    assert response.status_code == 401


def test_get_sectors_returns_stats_and_planner_probabilities(db_session):
    db_session.add_all([
        Sector(name="производство", source="seed"),
        Sector(name="horeca", source="seed"),
        Sector(name="архив", source="llm", active=False),
        Post(text="a", category="utp_cta", status="published", sector="производство", score=10),
        Post(text="b", category="utp_cta", status="published", sector="производство", score=20),
    ])
    db_session.commit()

    response = client.get("/sectors", headers=AUTH)
    assert response.status_code == 200
    rows = {row["name"]: row for row in response.json()}

    assert rows["производство"]["published_n"] == 2
    assert rows["производство"]["mean_score"] == 15.0
    assert rows["horeca"]["mean_score"] is None
    assert rows["horeca"]["weight"] > 0
    assert rows["архив"]["active"] is False
    assert rows["архив"]["probability"] is None
    assert abs(sum(r["probability"] or 0 for r in rows.values()) - 1.0) < 1e-6
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/api/test_posts.py tests/api/test_sectors.py -v`
Expected: FAIL — `KeyError: 'sector'` and `404` for `/sectors`

- [ ] **Step 3: Implement**

`src/api/schemas.py` — in `PostOut` add after `category: str`:

```python
    sector: str | None
```

and add a new schema:

```python
class SectorOut(BaseModel):
    name: str
    source: str
    active: bool
    published_n: int
    mean_score: float | None
    median_score: float | None
    last_post_at: datetime | None
    weight: float | None
    probability: float | None
```

`src/api/routers/posts.py` — add query param `sector: str | None = None` after `status`, and include it in `filters`:

```python
    filters = dict(category=category, style_variant_id=style_variant_id, model_used=model_used, status=status, sector=sector)
```

Create `src/api/routers/sectors.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import get_db, require_bearer_token
from src.api.schemas import SectorOut
from src.content.topic_planner import describe_sectors

router = APIRouter(dependencies=[Depends(require_bearer_token)])


@router.get("/sectors", response_model=list[SectorOut])
def get_sectors(db: Session = Depends(get_db)) -> list[SectorOut]:
    return describe_sectors(db)
```

`src/api/main.py` — import `sectors` alongside the other routers and add `app.include_router(sectors.router)` after `telegram`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/api -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/api tests/api/test_posts.py tests/api/test_sectors.py
git commit -m "feat: expose post sector filter and GET /sectors planner stats

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Dashboard — sector column/filter and «Сферы» page

**Files:**
- Modify: `dashboard/lib/api-types.ts`, `dashboard/lib/api-client.ts`, `dashboard/app/(dashboard)/posts/page.tsx`, `dashboard/app/(dashboard)/layout.tsx`
- Create: `dashboard/app/(dashboard)/sectors/page.tsx`

**Interfaces:**
- Consumes: `GET /posts?sector=`, `GET /sectors` (Task 5).
- Produces: `getSectors(): Promise<SectorStat[]>`, `getPosts({... sector?: string})`.

- [ ] **Step 1: Types and client**

`dashboard/lib/api-types.ts` — in `Post` add after `category: string;`:

```ts
  sector: string | null;
```

and add:

```ts
export type SectorStat = {
  name: string;
  source: string;
  active: boolean;
  published_n: number;
  mean_score: number | null;
  median_score: number | null;
  last_post_at: string | null;
  weight: number | null;
  probability: number | null;
};
```

`dashboard/lib/api-client.ts` — add `SectorStat` to both the import list and the `export type {...}` list; add `sector?: string;` to `getPosts` params; add:

```ts
export function getSectors(): Promise<SectorStat[]> {
  return apiFetch<SectorStat[]>("/sectors");
}
```

- [ ] **Step 2: Posts page**

In `dashboard/app/(dashboard)/posts/page.tsx`:
- import: `import { getPosts, getSectors } from "@/lib/api-client";`
- add `sector?: string;` to the `searchParams` type;
- replace the `const data = await getPosts({...})` statement with:

```tsx
  const [data, sectors] = await Promise.all([
    getPosts({
      category: params.category,
      status: params.status,
      model_used: params.model_used,
      sector: params.sector,
      page,
      page_size: 25,
    }),
    getSectors(),
  ]);
```

- in the form, after the status `<select>`, add:

```tsx
        <select name="sector" defaultValue={params.sector ?? ""}>
          <option value="">Все сферы</option>
          {sectors.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
```

- add `<th>Сфера</th>` after `<th>Текст</th>` and `<td>{post.sector ?? "—"}</td>` after the text cell.

- [ ] **Step 3: Sectors page**

Create `dashboard/app/(dashboard)/sectors/page.tsx`:

```tsx
import Link from "next/link";
import { getSectors } from "@/lib/api-client";

export const dynamic = "force-dynamic";

function formatNumber(value: number | null, digits = 2): string {
  return value === null ? "—" : value.toFixed(digits);
}

export default async function SectorsPage() {
  const sectors = await getSectors();
  const sorted = [...sectors].sort((a, b) => (b.probability ?? -1) - (a.probability ?? -1));

  return (
    <main>
      <h1>Сферы</h1>
      <p>Вероятность — шанс, что планировщик выберет сферу для следующего поста.</p>
      <table>
        <thead>
          <tr>
            <th>Сфера</th>
            <th>Источник</th>
            <th>Активна</th>
            <th>Опубликовано</th>
            <th>Средний score</th>
            <th>Медиана score</th>
            <th>Последний пост</th>
            <th>Вероятность</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => (
            <tr key={s.name}>
              <td>
                <Link href={`/posts?sector=${encodeURIComponent(s.name)}`}>{s.name}</Link>
              </td>
              <td>{s.source}</td>
              <td>{s.active ? "да" : "нет"}</td>
              <td>{s.published_n}</td>
              <td>{formatNumber(s.mean_score)}</td>
              <td>{formatNumber(s.median_score)}</td>
              <td>{s.last_post_at ?? "—"}</td>
              <td>{s.probability === null ? "—" : `${(s.probability * 100).toFixed(0)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
```

In `dashboard/app/(dashboard)/layout.tsx` add `<Link href="/sectors">Сферы</Link>` right after the «Посты» link.

- [ ] **Step 4: Typecheck, test, build**

Run: `cd dashboard && npm run typecheck && npm test && npm run build`
Expected: no type errors, vitest passes, build succeeds. If a vitest fixture builds a `Post` object literal, add `sector: null` to it.

- [ ] **Step 5: Commit**

```bash
git add dashboard/lib dashboard/app
git commit -m "feat: dashboard sector filter on posts and planner Сферы page

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Backfill existing posts + planner dry-run script

**Files:**
- Create: `scripts/backfill_post_sectors.py`, `scripts/simulate_topic_planner.py`
- Test: `tests/test_backfill_post_sectors.py`

**Interfaces:**
- Consumes: `normalize_sector`, `sync_seed_sectors`, `load_planner_inputs`, `choose_assignment` (Task 2); `get_active_sector_names` (Task 1); `LLMClient.complete(role, messages, run_id=None, step_no=None)`.
- Produces: `backfill_post_sectors(session, llm_client, sectors: list[str]) -> dict` with keys `updated: int`, `skipped: list[tuple[int, str]]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_backfill_post_sectors.py`:

```python
from scripts.backfill_post_sectors import backfill_post_sectors
from src.db.models import Post
from src.llm.client import LLMResponse


class _FakeLLM:
    def __init__(self, answers: list[str]):
        self._answers = list(answers)
        self.roles = []

    def complete(self, role, messages, run_id=None, step_no=None):
        self.roles.append(role)
        return LLMResponse(text=self._answers.pop(0), tokens_in=1, tokens_out=1, cost_usd=0.0, model="kimi-k2.6", finish_reason="stop")


def test_backfill_sets_valid_sector_skips_invalid_and_leaves_tagged_posts(db_session):
    workshop = Post(text="цех и сменные наряды", category="utp_cta", status="published")
    unclear = Post(text="непонятный пост", category="utp_cta", status="published")
    tagged = Post(text="уже размечен", category="utp_cta", status="published", sector="horeca")
    db_session.add_all([workshop, unclear, tagged])
    db_session.commit()
    llm = _FakeLLM(["«Производство».", "космос"])

    result = backfill_post_sectors(db_session, llm, ["производство", "horeca"])
    db_session.commit()

    assert result["updated"] == 1
    assert result["skipped"] == [(unclear.id, "космос")]
    assert llm.roles == ["classifier", "classifier"]
    db_session.refresh(workshop)
    db_session.refresh(unclear)
    db_session.refresh(tagged)
    assert workshop.sector == "производство"
    assert unclear.sector is None
    assert tagged.sector == "horeca"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_backfill_post_sectors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.backfill_post_sectors'`

- [ ] **Step 3: Implement backfill**

Create `scripts/backfill_post_sectors.py`:

```python
"""One-time backfill of posts.sector for posts created before the topic
planner existed (docs/superpowers/specs/2026-09-16-topic-planner-design.md).
Idempotent: only touches posts with sector IS NULL. Seeds the sectors table
from settings first, so it can run right after the migration.

Run manually: `python -m scripts.backfill_post_sectors`
"""
from sqlalchemy import select

from src.config import load_settings
from src.content.topic_planner import normalize_sector, sync_seed_sectors
from src.db.engine import session_scope
from src.db.models import Post
from src.db.repo import get_active_sector_names
from src.llm.client import LLMClient

CLASSIFY_PROMPT = (
    "К какой сфере бизнеса относится этот пост в Threads? Выбери ровно одну "
    "сферу из списка и ответь только её названием, без пояснений. Если ни одна "
    "не подходит — ответь «нет».\n\nСферы:\n{sectors}\n\nПост:\n{text}"
)


def backfill_post_sectors(session, llm_client, sectors: list[str]) -> dict:
    allowed = set(sectors)
    sector_list = "\n".join(f"- {s}" for s in sectors)
    updated = 0
    skipped: list[tuple[int, str]] = []

    posts = session.execute(select(Post).where(Post.sector.is_(None)).order_by(Post.id)).scalars().all()
    for post in posts:
        response = llm_client.complete(
            role="classifier",
            messages=[{"role": "user", "content": CLASSIFY_PROMPT.format(sectors=sector_list, text=post.text)}],
        )
        answer = normalize_sector(response.text.strip().strip("«»\"'.- "))
        if answer in allowed:
            post.sector = answer
            updated += 1
        else:
            skipped.append((post.id, response.text[:80]))
    return {"updated": updated, "skipped": skipped}


def main() -> None:
    cfg = load_settings()["topic_planner"]
    llm_client = LLMClient()
    with session_scope() as session:
        sync_seed_sectors(session, cfg["seed_sectors"])
        session.flush()
        result = backfill_post_sectors(session, llm_client, get_active_sector_names(session))
    print(f"Updated {result['updated']} posts.")
    for post_id, answer in result["skipped"]:
        print(f"  skipped post {post_id}: classifier answered {answer!r}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement dry-run script**

Create `scripts/simulate_topic_planner.py`:

```python
"""Dry run of the topic planner against the current DB: prints how the next
N assignments would be distributed, without creating any posts. Only writes
missing seed sectors (idempotent, same as the real planner does).

Run: `python -m scripts.simulate_topic_planner [N]`
"""
import random
import sys
from collections import Counter

from src.config import load_settings
from src.content.topic_planner import choose_assignment, load_planner_inputs, sync_seed_sectors
from src.db.engine import session_scope


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    cfg = load_settings()["topic_planner"]
    with session_scope() as session:
        sync_seed_sectors(session, cfg["seed_sectors"])
        session.flush()
        inputs = load_planner_inputs(session, cfg)

    rng = random.Random()
    sectors: Counter = Counter()
    categories: Counter = Counter()
    for i in range(n):
        assignment = choose_assignment(inputs, cfg, rng)
        label = assignment.sector or "<новая сфера>"
        sectors[label] += 1
        categories[assignment.category] += 1
        print(f"{i + 1:>3}. {label} / {assignment.category}")
        inputs.window = [(assignment.sector, assignment.category)] + inputs.window[: cfg["window_posts"] - 1]

    print("\nСферы:")
    for name, count in sectors.most_common():
        print(f"  {name}: {count} ({count / n:.0%})")
    print("Категории:")
    for name, count in categories.most_common():
        print(f"  {name}: {count} ({count / n:.0%})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_backfill_post_sectors.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_post_sectors.py scripts/simulate_topic_planner.py tests/test_backfill_post_sectors.py
git commit -m "feat: backfill posts.sector and dry-run topic planner script

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Full verification and README note

**Files:**
- Modify: `README.md` (short "Topic planner" section)

- [ ] **Step 1: Full test suite**

Run: `pytest`
Expected: all PASS. Paste the summary line into the task report.

Run: `cd dashboard && npm run typecheck && npm test`
Expected: PASS

- [ ] **Step 2: README section**

Add to `README.md` next to the other one-off scripts section:

```markdown
### Topic planner (sectors)

ContentAgent no longer picks its own topic: `src/content/topic_planner.py` assigns a
business sector + category before each run (tune in `config/settings.yaml` →
`topic_planner`). After deploying migration `0003`:

1. `python -m scripts.backfill_post_sectors` — tag existing posts with a sector (once).
2. `python -m scripts.simulate_topic_planner 30` — dry-run: shows how the next 30
   posts would be distributed across sectors/categories, creates nothing.

The dashboard «Сферы» page shows each sector's stats and its current selection probability.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document topic planner backfill and dry-run scripts

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Deployment checklist (for the human operator — not automated)**

1. Deploy the new image; worker/api apply migration `0003` on boot via `scripts/boot.sh`. Do not run `alembic upgrade head` from the host against the shared DB.
2. Inside the worker container: `python -m scripts.backfill_post_sectors`, check skipped posts.
3. `python -m scripts.simulate_topic_planner 30` — confirm «производство» ≤ ~50%, other sectors appear, categories mixed.
4. Dashboard: «Посты» sector column/filter, «Сферы» page.
5. After a few real runs: `agent_runs.output_ref` for `content` shows the assignment JSON; new posts have `sector` set.
