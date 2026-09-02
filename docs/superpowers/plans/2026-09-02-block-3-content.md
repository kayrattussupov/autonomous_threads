# Block 3: Content Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the content pipeline — `content_agent` (a ReAct agent that writes and schedules Threads posts using the four-layer prompt), `style_critic` (a synchronous quality gate before a draft can be scheduled), and the scheduled-publish job that actually posts to Threads via the existing `ThreadsWriteClient` — closing out SPEC.md's Block 3 (T3.1–T3.6).

**Architecture:** `config/constitution.md` (Layer 1) and the `knowledge_base` table (Layer 2) are seeded once from SPEC.md §7's exact starter content. A placeholder `style_variants` row (Layer 3 — the human-authored "genome") is seeded so the system is functional end-to-end; **the human operator must replace its `genome` text with their real authored voice before any real post goes out** (this plan builds the plumbing, not the voice). `src/prompt/assembler.py` is a pure function that joins the four layers into one system prompt string. `ContentAgent` (`src/agents/content.py`) is a `ReActAgent` subclass whose `decide_next_action` asks an LLM (role `post_writer`) to pick the next tool via a strict JSON contract, with `save_draft` internally gating on `style_critic` (one regeneration allowed, then `needs_review` + Telegram alert). A separate, non-agent `publish_scheduled_posts()` function (mirroring `feed_miner`'s "deterministic, not ReAct" shape) publishes whatever's due, with retries and a Telegram alert on failure. Both get wired into `src/scheduler.py` alongside the existing `feed_miner` jobs.

**Tech Stack:** Same as prior blocks — Python 3.12, SQLAlchemy 2.x, pytest, `requests`. Adds the Tavily search API (`https://api.tavily.com/search`) for the `web_search` tool — a free-tier, agent-oriented search API; swappable later since it's isolated to one module.

**Spec:** [SPEC.md](../../../SPEC.md) — §6.1 (`content_agent`), §6.3 (`style_critic`), §7 (prompt layers, exact constitution/knowledge_base text), §8 (`posts`/`style_variants`/`playbook_rules`/`knowledge_base` DDL), §9 (safety table), §11 Block 3 (T3.1–T3.6).

## Global Constraints

- **Business decision (from the human operator, overrides/tightens SPEC.md's blanket ≤500-char limit):** publish exactly 3 posts/day, each targeting **200–400 characters** (not the full 500 — Threads rewards short, frequent posts over long infrequent ones). The 500-char ceiling from `constitution.md` remains a hard, never-cross limit; 200–400 is the *targeted* range `style_critic` enforces on top of it.
- **The style genome (`style_variants.genome`) is `created_by='human'` and is NOT authored by this plan.** A placeholder genome is seeded so the pipeline is testable end-to-end, but it is explicitly marked as temporary in its own text and in the seed script's output — production posting must not begin until the human operator has replaced it with their real voice.
- `content_agent` is a `ReActAgent` subclass (`src/agents/base.py`, Block 0/1) — same 8-step/40K-token/120s limits, same `agent_runs`/`agent_steps` tracing, same `role=post_writer` LLM routing.
- `style_critic` is **one LLM call, not an agent** (SPEC.md §6.3) — a plain function, never a `ReActAgent` subclass.
- The `publish_scheduled_posts()` job is **deterministic, not ReAct** (mirroring `feed_miner`'s established shape from Block 2) — traced into `agent_runs`/`agent_steps` the same way, `agent="content_publisher"`.
- Any post reaching `status='needs_review'` (style_critic failed twice) sends exactly one Telegram alert via the existing `src/alerts.py::send_telegram_alert` (Block 2) — never raise, never crash the caller.
- Categories are exactly `utp_cta | educational | news | personal` (SPEC.md §6.1); `category='news'` requires a verified `source_url` (HTTP 200 + non-empty `<title>`) before it can pass `style_critic`.
- `config/models.yaml` is the only place model names are chosen — `content_agent`'s tool-selection/drafting calls use `role="post_writer"`, `style_critic`'s LLM-judged checks use `role="style_critic"`. Never hardcode a model name.
- `TAVILY_API_KEY` is a new required env var for `web_search` — add it to `.env.example`, matching the existing pattern for every other external credential in this project.

---

## File Structure

```
autonomous_threads/
├── .env.example                    # MODIFY — add TAVILY_API_KEY
├── config/
│   ├── constitution.md             # NEW — Layer 1, git-tracked, human-owned, agent reads only
│   └── settings.yaml               # MODIFY — add publish_times, post_length, search
├── scripts/
│   ├── seed_knowledge_base.py      # NEW — one-time idempotent seed of Layer 2
│   └── seed_style_variant_v1.py    # NEW — one-time seed of the PLACEHOLDER Layer 3 genome
├── src/
│   ├── agents/
│   │   ├── base.py                 # MODIFY — expose self._run_id/self._step_no for subclasses
│   │   ├── content.py              # NEW — ContentAgent(ReActAgent)
│   │   ├── style_critic.py         # NEW — run_style_critic(), a plain function
│   │   └── publisher.py            # NEW — publish_scheduled_posts(), deterministic like feed_miner
│   ├── prompt/
│   │   ├── __init__.py             # NEW
│   │   └── assembler.py            # NEW — assemble_system_prompt(), pure function
│   ├── tools/
│   │   ├── __init__.py             # NEW
│   │   └── web_search.py           # NEW — web_search(), verify_source()
│   ├── db/
│   │   └── repo.py                 # MODIFY — append content-domain query/insert helpers
│   └── scheduler.py                # MODIFY — add content_agent + publisher jobs
└── tests/
    ├── prompt/test_assembler.py
    ├── tools/test_web_search.py
    ├── agents/
    │   ├── test_style_critic.py
    │   ├── test_content.py
    │   └── test_publisher.py
    └── db/test_repo_content.py
```

**Why this split:** `src/tools/web_search.py` is separate from `src/agents/content.py` because it's the one piece with an external, swappable, paid-tier-adjacent dependency (Tavily) — isolating it means changing search providers later touches one file. `style_critic.py` and `publisher.py` live under `src/agents/` alongside `feed_miner.py` even though neither is a `ReActAgent`, matching the established convention that "agent" in this codebase's file layout means "a component with its own `agent_runs` identity," not "subclasses `ReActAgent`." `prompt/assembler.py` stays a pure function with no DB access (mirroring the `pricing.py`/`cost_usd` pattern from Block 0/1) so it's trivially unit-testable — the caller (`ContentAgent`) does the DB fetches and passes plain data in.

---

### Task 1: Expose `run_id`/`step_no` on `ReActAgent`, add content-domain repo helpers

**Files:**
- Modify: `src/agents/base.py`
- Modify: `src/db/repo.py`
- Test: `tests/db/test_repo_content.py`
- Test: extend `tests/agents/test_base.py`

**Interfaces:**
- Consumes: existing `AgentRun`/`Post`/`StyleVariant`/`PlaybookRule`/`SwipeFilePost`/`KnowledgeBaseEntry` models (`src/db/models.py`, unchanged).
- Produces: `ReActAgent.run()` now sets `self._run_id` right after creating the run row, so subclasses' `decide_next_action`/tool methods can read `self._run_id` for LLM-call tracing (previously only a local variable inside `run()`, invisible to subclasses — a real gap since every subclass needs it to pass `run_id=` into `LLMClient.complete(...)`). Also produces 9 new `src/db/repo.py` functions consumed by Tasks 4, 6, 7, 8: `get_knowledge_base(session) -> dict`, `get_active_style(session) -> StyleVariant | None`, `get_active_playbook_rules(session) -> list[PlaybookRule]`, `get_recent_posts(session, n=30) -> list[Post]`, `get_top_performers(session, n=5) -> list[Post]`, `get_swipe_examples(session, n=8, topic=None) -> list[SwipeFilePost]`, `insert_post(session, **fields) -> Post`, `increment_style_variant_posts_n(session, style_variant_id) -> None`, `get_posts_due_for_publish(session, now=None) -> list[Post]`.

- [ ] **Step 1: Write the failing test for `run_id` exposure**

Append to `tests/agents/test_base.py`:

```python
def test_run_exposes_run_id_to_subclass(db_session):
    seen_run_ids = []

    class _RunIdCapturingAgent(ReActAgent):
        def __init__(self, **kwargs):
            super().__init__(agent_name="test_run_id", **kwargs)
            self._step = 0

        def tools(self):
            return {"noop": lambda: "ok"}

        def system_prompt(self):
            return "test"

        def decide_next_action(self, history):
            seen_run_ids.append(self._run_id)
            self._step += 1
            if self._step > 1:
                return None
            return {"thought": "t", "tool_name": "noop", "tool_args": {}}

    agent = _RunIdCapturingAgent(max_steps=8, max_tokens=40_000, max_seconds=120)
    run = agent.run(trigger="manual")

    assert seen_run_ids == [run.id, run.id]
    assert all(rid is not None for rid in seen_run_ids)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/agents/test_base.py::test_run_exposes_run_id_to_subclass -v`
Expected: FAIL with `AttributeError: '_RunIdCapturingAgent' object has no attribute '_run_id'`

- [ ] **Step 3: Modify `src/agents/base.py`**

In `__init__`, add `self._run_id: int | None = None` alongside the existing `self._tokens_used = 0` block.

In `run()`, right after `run_id = run.id`, add `self._run_id = run_id`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/agents/test_base.py::test_run_exposes_run_id_to_subclass -v`
Expected: PASS

- [ ] **Step 5: Write the failing tests for the repo helpers**

```python
# tests/db/test_repo_content.py
from datetime import datetime, timedelta, timezone

from src.db.repo import (
    get_active_playbook_rules,
    get_active_style,
    get_knowledge_base,
    get_posts_due_for_publish,
    get_recent_posts,
    get_swipe_examples,
    get_top_performers,
    increment_style_variant_posts_n,
    insert_post,
    insert_swipe_file_post,
)
from src.db.models import KnowledgeBaseEntry, PlaybookRule, Post, StyleVariant


def test_get_knowledge_base_returns_key_value_dict(db_session):
    db_session.add(KnowledgeBaseEntry(key="niche", value="automation"))
    db_session.add(KnowledgeBaseEntry(key="audience", value="SMB owners"))
    db_session.commit()

    kb = get_knowledge_base(db_session)

    assert kb == {"niche": "automation", "audience": "SMB owners"}


def test_get_active_style_returns_lowest_posts_n(db_session):
    db_session.add(StyleVariant(name="v1", genome="g1", status="active", created_by="human", posts_n=10))
    db_session.add(StyleVariant(name="v2", genome="g2", status="active", created_by="analyst", posts_n=3))
    db_session.add(StyleVariant(name="v3_retired", genome="g3", status="retired", created_by="human", posts_n=0))
    db_session.commit()

    active = get_active_style(db_session)

    assert active.name == "v2"


def test_get_active_style_returns_none_when_no_active_variant(db_session):
    assert get_active_style(db_session) is None


def test_increment_style_variant_posts_n(db_session):
    variant = StyleVariant(name="v1", genome="g", status="active", created_by="human", posts_n=5)
    db_session.add(variant)
    db_session.commit()

    increment_style_variant_posts_n(db_session, variant.id)
    db_session.commit()

    db_session.refresh(variant)
    assert variant.posts_n == 6


def test_get_active_playbook_rules_filters_by_status(db_session):
    db_session.add(PlaybookRule(rule_text="r1", status="testing", version=1))
    db_session.add(PlaybookRule(rule_text="r2", status="confirmed", version=1))
    db_session.add(PlaybookRule(rule_text="r3", status="rejected", version=1))
    db_session.add(PlaybookRule(rule_text="r4", status="proposed", version=1))
    db_session.commit()

    active = get_active_playbook_rules(db_session)

    assert {r.rule_text for r in active} == {"r1", "r2"}


def test_get_recent_posts_orders_by_created_at_desc(db_session):
    insert_post(db_session, text="old", category="educational", status="published")
    db_session.commit()
    insert_post(db_session, text="new", category="educational", status="published")
    db_session.commit()

    recent = get_recent_posts(db_session, n=30)

    assert [p.text for p in recent] == ["new", "old"]


def test_get_top_performers_orders_by_score_desc_published_only(db_session):
    insert_post(db_session, text="low", category="educational", status="published", score=1.0)
    insert_post(db_session, text="high", category="educational", status="published", score=99.0)
    insert_post(db_session, text="draft_high_score", category="educational", status="draft", score=1000.0)
    db_session.commit()

    top = get_top_performers(db_session, n=5)

    assert [p.text for p in top] == ["high", "low"]


def test_get_swipe_examples_filters_by_topic(db_session):
    insert_swipe_file_post(db_session, threads_post_id="a", text="on topic", topic="automation")
    insert_swipe_file_post(db_session, threads_post_id="b", text="off topic", topic="marketing")
    db_session.commit()

    examples = get_swipe_examples(db_session, n=8, topic="automation")

    assert [e.text for e in examples] == ["on topic"]


def test_get_posts_due_for_publish(db_session):
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="due", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=5))
    insert_post(db_session, text="future", category="educational", status="scheduled", scheduled_at=now + timedelta(hours=1))
    insert_post(db_session, text="already_published", category="educational", status="published", scheduled_at=now - timedelta(minutes=5))
    db_session.commit()

    due = get_posts_due_for_publish(db_session, now=now)

    assert [p.text for p in due] == ["due"]
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/db/test_repo_content.py -v`
Expected: FAIL with `ImportError` (functions don't exist yet)

- [ ] **Step 7: Append to `src/db/repo.py`**

Add `KnowledgeBaseEntry, PlaybookRule, Post, StyleVariant` to the existing `from src.db.models import ...` line, then add:

```python
def get_knowledge_base(session: Session) -> dict:
    rows = session.execute(select(KnowledgeBaseEntry)).scalars().all()
    return {row.key: row.value for row in rows}


def get_active_style(session: Session) -> StyleVariant | None:
    return session.execute(
        select(StyleVariant)
        .where(StyleVariant.status == "active")
        .order_by(StyleVariant.posts_n.asc().nullsfirst(), StyleVariant.id.asc())
        .limit(1)
    ).scalar_one_or_none()


def increment_style_variant_posts_n(session: Session, style_variant_id: int) -> None:
    variant = session.get(StyleVariant, style_variant_id)
    variant.posts_n = (variant.posts_n or 0) + 1


def get_active_playbook_rules(session: Session) -> list[PlaybookRule]:
    return list(session.execute(
        select(PlaybookRule)
        .where(PlaybookRule.status.in_(["testing", "confirmed"]))
        .order_by(PlaybookRule.introduced_at.desc())
    ).scalars().all())


def get_recent_posts(session: Session, n: int = 30) -> list[Post]:
    return list(session.execute(
        select(Post).order_by(Post.created_at.desc()).limit(n)
    ).scalars().all())


def get_top_performers(session: Session, n: int = 5) -> list[Post]:
    return list(session.execute(
        select(Post)
        .where(Post.status == "published", Post.score.isnot(None))
        .order_by(Post.score.desc())
        .limit(n)
    ).scalars().all())


def get_swipe_examples(session: Session, n: int = 8, topic: str | None = None) -> list[SwipeFilePost]:
    stmt = select(SwipeFilePost).order_by(SwipeFilePost.collected_at.desc()).limit(n)
    if topic:
        stmt = stmt.where(SwipeFilePost.topic == topic)
    return list(session.execute(stmt).scalars().all())


def insert_post(session: Session, **fields) -> Post:
    post = Post(**fields)
    session.add(post)
    session.flush()
    return post


def get_posts_due_for_publish(session: Session, now: datetime | None = None) -> list[Post]:
    now = now or datetime.now(timezone.utc)
    return list(session.execute(
        select(Post)
        .where(Post.status == "scheduled", Post.scheduled_at <= now)
        .order_by(Post.scheduled_at.asc())
    ).scalars().all())
```

`SwipeFilePost` is already imported in this file from Task 2 of Block 2 — do not duplicate the import.

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/db/test_repo_content.py tests/agents/test_base.py -v`
Expected: PASS (9 + 4 = 13 tests)

- [ ] **Step 9: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all 30 previously-passing tests still pass, plus these 13 new ones (43 total).

- [ ] **Step 10: Commit**

```bash
git add src/agents/base.py src/db/repo.py tests/db/test_repo_content.py tests/agents/test_base.py
git commit -m "feat: expose ReActAgent run_id to subclasses, add content-domain repo helpers"
```

---

### Task 2: `config/constitution.md`, `knowledge_base` seed, `settings.yaml` additions

**Files:**
- Create: `config/constitution.md`
- Create: `scripts/seed_knowledge_base.py`
- Modify: `config/settings.yaml`

**Interfaces:**
- Produces: `config/constitution.md` (git-tracked text file, Layer 1) — consumed by Task 4's `assemble_system_prompt`. Produces populated `knowledge_base` rows — consumed by `get_knowledge_base()` (Task 1) inside Task 7's `ContentAgent`. Produces `settings.yaml`'s new `publish_times`, `post_length`, `search` keys — consumed by Tasks 6 (`style_critic`), 7 (`ContentAgent`), 8 (`publisher`, `scheduler`).

This task has no Python logic to unit-test — it's config content + an idempotent seed script. Verify by running the script and reading the file back.

- [ ] **Step 1: Write `config/constitution.md`** (verbatim from SPEC.md §7, Layer 1)

```markdown
# Конституция

Язык — русский. Лимит 500 символов. Никаких выдуманных цифр и статистики без источника. Для `category='news'` обязателен проверенный `source_url`. Не обещать конкретных процентов роста без своего кейса. Не упоминать клиентов по названию без разрешения. Один CTA на пост, не больше.
```

- [ ] **Step 2: Write `scripts/seed_knowledge_base.py`** (idempotent — safe to re-run)

```python
"""One-time (idempotent) seed of the Layer 2 knowledge_base table from
SPEC.md §7's starter content. Safe to re-run — upserts by key.

Run manually: `python -m scripts.seed_knowledge_base`
"""
from src.db.engine import session_scope
from src.db.models import KnowledgeBaseEntry

STARTER_KNOWLEDGE_BASE = {
    "niche": "автоматизация бизнес-процессов через ИИ для СМБ",
    "proof": "3 проекта: доставка, недвижимость, ресторан",
    "stack": "n8n, RAG на Qdrant, мультиагентные боты, Postgres memory",
    "differentiator": "соло, без прослойки менеджеров, быстрее агентств",
    "audience": "владельцы СМБ, маркетологи, C-level",
    "tone_seed": "инженер, который объясняет без пафоса; сухой юмор допустим",
    "never": "не обещать конкретных процентов роста без кейса",
}


def main() -> None:
    with session_scope() as session:
        for key, value in STARTER_KNOWLEDGE_BASE.items():
            row = session.get(KnowledgeBaseEntry, key)
            if row is None:
                session.add(KnowledgeBaseEntry(key=key, value=value))
            else:
                row.value = value
    print(f"Seeded {len(STARTER_KNOWLEDGE_BASE)} knowledge_base entries.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add to `config/settings.yaml`** (append these three top-level keys to the existing file — do not remove `queue_depth`, `feed_view_daily_cap`, `budget`, `agent_limits`, `search_groups`, `ab_test_models`)

```yaml
publish_times:
  - "09:00"
  - "14:00"
  - "20:00"
publish_timezone: "Asia/Almaty"
post_length:
  min_chars: 200
  max_chars: 400
  hard_max_chars: 500
search:
  provider: tavily
  max_results: 5
```

- [ ] **Step 4: Run the seed script against the local dev DB and verify**

```bash
docker compose up -d postgres
export DATABASE_URL=postgresql+psycopg://threads_agent:changeme@localhost:5433/threads_agent_test
python -m scripts.seed_knowledge_base
```

Expected output: `Seeded 7 knowledge_base entries.` Verify: `psql $DATABASE_URL -c "select key, value from knowledge_base order by key"` shows all 7 rows. Run it a second time — output is identical, no duplicate-key errors (upsert, not insert).

- [ ] **Step 5: Commit**

```bash
git add config/constitution.md config/settings.yaml scripts/seed_knowledge_base.py
git commit -m "feat: seed knowledge_base and constitution.md, add publishing/search settings"
```

---

### Task 3: Seed the placeholder style variant (Layer 3 — human must replace before production)

**Files:**
- Create: `scripts/seed_style_variant_v1.py`

**Interfaces:**
- Produces: one `style_variants` row with `status='active'`, `created_by='human'` — consumed by `get_active_style()` (Task 1) inside Task 7's `ContentAgent`. **This genome's actual text is a placeholder, not the human operator's real voice** — SPEC.md §7 requires this row's content to be human-authored; this task only makes the pipeline runnable end-to-end.

- [ ] **Step 1: Write `scripts/seed_style_variant_v1.py`**

```python
"""Seeds a PLACEHOLDER v1 style variant so the content pipeline is runnable
end-to-end before the human operator has written their real voice.

THIS GENOME TEXT IS A STAND-IN, NOT THE REAL VOICE. SPEC.md §7 requires the
Layer 3 genome to be human-authored (style_variants.created_by='human') —
replace PLACEHOLDER_GENOME below (or UPDATE the row directly) with the real
300-800 word genome before any post generated against it is actually
scheduled for real, non-test publishing.

Run manually, once: `python -m scripts.seed_style_variant_v1`
"""
from src.db.engine import session_scope
from src.db.models import StyleVariant

PLACEHOLDER_GENOME = """\
[ВРЕМЕННЫЙ ГЕНОМ — заменить перед реальной публикацией]

Голос: инженер, который объясняет без пафоса. Сухой юмор допустим, но не
обязателен в каждом посте. Никакого "мотивационного" тона, никаких
восклицательных знаков подряд.

Ритм: короткие предложения. Один пост — одна мысль. Без вступлений вроде
"Сегодня хочу рассказать про...".

Хуки: начинать с конкретного наблюдения, вопроса или факта — не с общих слов
об "автоматизации будущего".

Длина: 200-400 символов, без исключений в эту сторону; если мысль не
помещается — сократить, а не растягивать до лимита.

Структура: тезис → короткое обоснование или пример → (опционально) один CTA.

Табу: превосходные степени без обоснования ("лучший", "уникальный"),
обещания процентов роста без кейса, эмодзи как замена мысли.
"""


def main() -> None:
    with session_scope() as session:
        existing = session.query(StyleVariant).filter_by(name="v1_placeholder").one_or_none()
        if existing is not None:
            print(f"style_variant 'v1_placeholder' already exists (id={existing.id}), not re-seeding.")
            return
        variant = StyleVariant(
            name="v1_placeholder",
            genome=PLACEHOLDER_GENOME,
            status="active",
            created_by="human",
            rationale="Placeholder seeded by Block 3 setup — REPLACE with the operator's real authored genome before production posting.",
        )
        session.add(variant)
        session.flush()
        variant_id = variant.id
    print(f"Seeded placeholder style_variant id={variant_id}. REPLACE ITS GENOME before real publishing:")
    print(f'  UPDATE style_variants SET genome = \'...\' WHERE id = {variant_id};')


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the local dev DB**

```bash
python -m scripts.seed_style_variant_v1
```

Expected: prints the seeded id and the `UPDATE` command the operator will need later. Verify: `psql $DATABASE_URL -c "select id, name, status, created_by from style_variants"`.

- [ ] **Step 3: Commit**

```bash
git add scripts/seed_style_variant_v1.py
git commit -m "feat: seed placeholder style variant (human must replace genome before production)"
```

---

### Task 4: `src/prompt/assembler.py` — four-layer prompt assembly

**Files:**
- Create: `src/prompt/__init__.py` (empty)
- Create: `src/prompt/assembler.py`
- Test: `tests/prompt/test_assembler.py`

**Interfaces:**
- Consumes: nothing from the DB directly (pure function) — plain Python values.
- Produces: `assemble_system_prompt(constitution: str, knowledge_base: dict, active_genome: str, playbook_rules: list[str], swipe_examples: list[str], top_posts: list[str]) -> str` — consumed by Task 7's `ContentAgent.system_prompt()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/prompt/test_assembler.py
from src.prompt.assembler import assemble_system_prompt


def test_assemble_system_prompt_includes_all_four_layers_in_order():
    result = assemble_system_prompt(
        constitution="LAYER1_CONSTITUTION_TEXT",
        knowledge_base={"niche": "automation", "audience": "SMB"},
        active_genome="LAYER3_GENOME_TEXT",
        playbook_rules=["Post at 9am", "Avoid emoji"],
        swipe_examples=["chужой пост 1", "chужой пост 2"],
        top_posts=["мой лучший пост"],
    )

    layer1_pos = result.index("LAYER1_CONSTITUTION_TEXT")
    layer2_pos = result.index("niche")
    layer3_pos = result.index("LAYER3_GENOME_TEXT")
    layer4_pos = result.index("Post at 9am")
    examples_pos = result.index("chужой пост 1")

    assert layer1_pos < layer2_pos < layer3_pos < layer4_pos < examples_pos
    assert "SMB" in result
    assert "Avoid emoji" in result
    assert "мой лучший пост" in result


def test_assemble_system_prompt_handles_empty_playbook_and_examples():
    result = assemble_system_prompt(
        constitution="C",
        knowledge_base={"niche": "automation"},
        active_genome="G",
        playbook_rules=[],
        swipe_examples=[],
        top_posts=[],
    )

    assert "C" in result
    assert "G" in result
    # Must not raise on empty lists, and must not contain leftover
    # formatting artifacts like an empty bullet list.
    assert "automation" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/prompt/test_assembler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.prompt'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/prompt/assembler.py
def _render_knowledge_base(kb: dict) -> str:
    lines = "\n".join(f"{key} = {value}" for key, value in kb.items())
    return f"# База знаний\n{lines}"


def _render_playbook(rules: list[str]) -> str:
    if not rules:
        return "# Playbook\n(правил пока нет)"
    lines = "\n".join(f"- {rule}" for rule in rules)
    return f"# Playbook\n{lines}"


def _render_examples(swipe_examples: list[str], top_posts: list[str]) -> str:
    parts = ["# Примеры"]
    if top_posts:
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
) -> str:
    return "\n\n".join([
        constitution,
        _render_knowledge_base(knowledge_base),
        active_genome,
        _render_playbook(playbook_rules),
        _render_examples(swipe_examples, top_posts),
    ])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/prompt/test_assembler.py -v`
Expected: PASS (2/2)

- [ ] **Step 5: Commit**

```bash
git add src/prompt/__init__.py src/prompt/assembler.py tests/prompt/test_assembler.py
git commit -m "feat: add four-layer system prompt assembler"
```

---

### Task 5: `src/tools/web_search.py` — `web_search` and `verify_source`

**Files:**
- Create: `src/tools/__init__.py` (empty)
- Create: `src/tools/web_search.py`
- Modify: `.env.example`
- Test: `tests/tools/test_web_search.py`

**Interfaces:**
- Consumes: `TAVILY_API_KEY` env var (new).
- Produces: `web_search(query: str, max_results: int | None = None) -> list[dict]` (each dict: `{"title", "url", "content"}`) and `verify_source(url: str) -> bool` — both consumed by Task 7's `ContentAgent` tools (only used for `category='news'` per SPEC.md §6.1).

- [ ] **Step 1: Add `TAVILY_API_KEY` to `.env.example`**

```dotenv
# Web search for content_agent's category='news' posts
TAVILY_API_KEY=
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/tools/test_web_search.py
from unittest.mock import MagicMock, patch

from src.tools.web_search import verify_source, web_search


def test_web_search_returns_title_url_content(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    with patch("src.tools.web_search.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [
                {"title": "Article 1", "url": "https://example.com/1", "content": "snippet 1"},
                {"title": "Article 2", "url": "https://example.com/2", "content": "snippet 2"},
            ]},
        )
        results = web_search("n8n automation news")

    assert results == [
        {"title": "Article 1", "url": "https://example.com/1", "content": "snippet 1"},
        {"title": "Article 2", "url": "https://example.com/2", "content": "snippet 2"},
    ]
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.tavily.com/search"
    assert kwargs["json"]["api_key"] == "test-key"
    assert kwargs["json"]["query"] == "n8n automation news"


def test_web_search_returns_empty_list_on_api_error(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    with patch("src.tools.web_search.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=500, text="server error")
        results = web_search("query")

    assert results == []


def test_verify_source_true_on_200_with_title():
    with patch("src.tools.web_search.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text="<html><head><title>Real Article</title></head></html>")
        assert verify_source("https://example.com/article") is True


def test_verify_source_false_on_non_200():
    with patch("src.tools.web_search.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=404, text="")
        assert verify_source("https://example.com/missing") is False


def test_verify_source_false_on_empty_title():
    with patch("src.tools.web_search.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, text="<html><head><title></title></head></html>")
        assert verify_source("https://example.com/blank-title") is False


def test_verify_source_false_on_network_error():
    with patch("src.tools.web_search.requests.get", side_effect=Exception("timeout")):
        assert verify_source("https://example.com/down") is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/tools/test_web_search.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.tools'`

- [ ] **Step 4: Write minimal implementation**

```python
# src/tools/web_search.py
import os
import re

import requests

from src.config import load_settings

TAVILY_URL = "https://api.tavily.com/search"


def web_search(query: str, max_results: int | None = None) -> list[dict]:
    """Only used for category='news' drafts (SPEC.md §6.1). Returns [] on
    any failure rather than raising — a failed search should not crash the
    content_agent's ReAct loop; the LLM sees an empty result and adapts."""
    max_results = max_results or load_settings()["search"]["max_results"]
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []

    try:
        resp = requests.post(
            TAVILY_URL,
            json={"api_key": api_key, "query": query, "max_results": max_results},
            timeout=15,
        )
    except requests.RequestException:
        return []

    if resp.status_code != 200:
        return []

    data = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
        for r in data.get("results", [])
    ]


def verify_source(url: str) -> bool:
    """SPEC.md §6.1: HTTP 200 + non-empty <title>."""
    try:
        resp = requests.get(url, timeout=10)
    except requests.RequestException:
        return False

    if resp.status_code != 200:
        return False

    match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
    if not match:
        return False
    return bool(match.group(1).strip())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/tools/test_web_search.py -v`
Expected: PASS (6/6)

- [ ] **Step 6: Commit**

```bash
git add src/tools/__init__.py src/tools/web_search.py tests/tools/test_web_search.py .env.example
git commit -m "feat: add web_search (Tavily) and verify_source tools for news posts"
```

---

### Task 6: `src/agents/style_critic.py` — the quality gate

**Files:**
- Create: `src/agents/style_critic.py`
- Test: `tests/agents/test_style_critic.py`

**Interfaces:**
- Consumes: `LLMClient.complete(role="style_critic", messages, run_id=None, step_no=None) -> LLMResponse` (Block 0/1); `load_settings()` for `post_length` (Task 2).
- Produces: `run_style_critic(text: str, category: str, source_url: str | None, genome: str, recent_post_texts: list[str], llm_client: LLMClient, run_id: int | None = None, step_no: int | None = None) -> dict` returning `{"pass": bool, "issues": list[str], "tokens_in": int, "tokens_out": int, "cost_usd": float}` — consumed by Task 7's `ContentAgent._save_draft`. The token/cost fields let the caller (a `ReActAgent`) call its own `note_llm_usage(...)`, matching how `feed_miner` (Block 2) already threads `LLMResponse` usage through.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_style_critic.py
from unittest.mock import MagicMock

from src.agents.style_critic import run_style_critic
from src.llm.client import LLMResponse


class _FakeLLMClient:
    def __init__(self, issues: list[str]):
        self._issues = issues
        self.calls = []

    def complete(self, role, messages, run_id=None, step_no=None):
        self.calls.append(role)
        import json
        return LLMResponse(
            text=json.dumps({"issues": self._issues}),
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_style_critic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agents.style_critic'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agents/style_critic.py
import json

from src.config import load_settings
from src.llm.client import LLMClient

CRITIC_PROMPT_TEMPLATE = """\
Ты — редактор, проверяющий пост перед публикацией в Threads.

Стилевой геном (голос, которому должен соответствовать пост):
{genome}

Пост на проверку:
{text}

Проверь ДВЕ вещи и верни JSON {{"issues": [...]}} (пустой список, если всё
хорошо):
1. Соответствует ли пост геному (голос, ритм, табу)?
2. Есть ли в посте непроверенные числовые утверждения или статистика без
   указанного источника?

Ответь СТРОГО JSON без пояснений вокруг: {{"issues": ["строка с описанием проблемы", ...]}}
"""


def run_style_critic(
    text: str,
    category: str,
    source_url: str | None,
    genome: str,
    recent_post_texts: list[str],
    llm_client: LLMClient,
    run_id: int | None = None,
    step_no: int | None = None,
) -> dict:
    settings = load_settings()["post_length"]
    issues = []

    if len(text) > settings["hard_max_chars"]:
        issues.append(f"превышен абсолютный лимит {settings['hard_max_chars']} символов (текущая длина {len(text)})")
    elif not (settings["min_chars"] <= len(text) <= settings["max_chars"]):
        issues.append(
            f"длина {len(text)} вне целевого диапазона {settings['min_chars']}-{settings['max_chars']} символов"
        )

    if category == "news" and not source_url:
        issues.append("category='news' требует проверенный source_url")

    if text.strip() in {p.strip() for p in recent_post_texts}:
        issues.append("точное повторение одного из последних постов")

    response = llm_client.complete(
        role="style_critic",
        messages=[{"role": "user", "content": CRITIC_PROMPT_TEMPLATE.format(genome=genome, text=text)}],
        run_id=run_id,
        step_no=step_no,
    )
    try:
        llm_issues = json.loads(response.text).get("issues", [])
    except (json.JSONDecodeError, AttributeError):
        llm_issues = [f"style_critic LLM вернул невалидный JSON: {response.text[:200]!r}"]

    issues.extend(llm_issues)

    return {
        "pass": len(issues) == 0,
        "issues": issues,
        "tokens_in": response.tokens_in,
        "tokens_out": response.tokens_out,
        "cost_usd": response.cost_usd,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/test_style_critic.py -v`
Expected: PASS (6/6)

- [ ] **Step 5: Commit**

```bash
git add src/agents/style_critic.py tests/agents/test_style_critic.py
git commit -m "feat: add style_critic quality gate (length, source_url, repeat, genome/numeric-claims via LLM)"
```

---

### Task 7: `src/agents/content.py` — `ContentAgent`

**Files:**
- Create: `src/agents/content.py`
- Test: `tests/agents/test_content.py`

**Interfaces:**
- Consumes: `ReActAgent` (`src/agents/base.py`, Task 1's `self._run_id`), all of Task 1's new repo helpers, `assemble_system_prompt` (Task 4), `web_search`/`verify_source` (Task 5), `run_style_critic` (Task 6), `send_telegram_alert` (Block 2), `LLMClient` (Block 0/1), `load_settings` (Block 0/1 + Task 2's `publish_times`/`publish_timezone`).
- Produces: `class ContentAgent(ReActAgent)` — consumed by Task 9's scheduler wiring as `ContentAgent().run(trigger="cron")`.

This is the largest task in the plan. Read it carefully before starting.

**Design of the tool-calling loop:** `LLMClient.complete()` (Block 0/1) does not implement OpenAI-style function calling — it is a plain chat completion. `ContentAgent.decide_next_action` therefore asks the LLM to respond with **one strict JSON object** describing the next tool call, and parses it manually. This keeps `ReActAgent`'s existing `decide_next_action(history) -> dict | None` contract (Block 0/1) unchanged — no modification to `base.py`'s loop logic beyond Task 1's `self._run_id` addition. If the LLM's response isn't valid JSON, or names a tool that doesn't exist, `decide_next_action` returns an action naming a deliberately-nonexistent tool (`"__parse_error__"`) — `ReActAgent.run()`'s existing exception handling (Block 0/1, already tested) catches the resulting `KeyError` from `self.tools()[tool_name]`, records it as a normal failed step (`tool_ok=False`), and the loop continues; the LLM sees its own parse failure reflected back in `history` on the next call and can retry with valid JSON. No new error-handling mechanism is needed in `base.py` for this — it's already there from Block 0/1's Task 9.

**Design of `save_draft`'s style_critic gate:** `save_draft` is a bound method closure that calls `run_style_critic(...)` internally before ever writing a `posts` row with `status='scheduled'`. On the first failure, it returns `{"status": "rejected", "issues": [...]}` into the ReAct loop (as a normal tool result) so the LLM can revise and call `save_draft` again. On a SECOND failure within the same run, it writes the post as `status='needs_review'` anyway, sends one Telegram alert, and sets `self._done = True` to end the run (matching SPEC.md §6.3: "при провале — одна регенерация. Второй провал → `needs_review` + Telegram"). A successful pass writes `status='scheduled'`, increments the chosen style variant's `posts_n`, and sets `self._done = True`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_content.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_content.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agents.content'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agents/content.py
import json
from datetime import datetime, timedelta

import pytz

from src.agents.base import ReActAgent
from src.agents.style_critic import run_style_critic
from src.alerts import send_telegram_alert
from src.config import load_settings
from src.db.engine import session_scope
from src.db.repo import (
    get_active_playbook_rules,
    get_active_style,
    get_knowledge_base,
    get_recent_posts,
    get_swipe_examples,
    get_top_performers,
    increment_style_variant_posts_n,
    insert_post,
)
from src.llm.client import LLMClient
from src.prompt.assembler import assemble_system_prompt
from src.tools.web_search import verify_source, web_search

CONSTITUTION_PATH = "config/constitution.md"

TOOL_SELECTION_PROMPT = """\
Ты выбираешь следующее действие. Доступные инструменты:

- get_recent_posts() — последние 30 своих постов, чтобы не повторяться
- get_top_performers() — 5 своих лучших постов по score
- get_swipe_examples(topic) — зашедшие чужие посты в нише (topic необязателен)
- web_search(query) — только для category='news', поиск свежих фактов
- verify_source(url) — проверить, что источник реально существует (для news)
- save_draft(text, category) — сохранить готовый пост (category один из:
  utp_cta, educational, news, personal)

Отвечай СТРОГО одним JSON-объектом, без текста вокруг:
{"thought": "краткое рассуждение", "tool_name": "имя_инструмента", "tool_args": {...}}

История уже вызванных инструментов и их результатов (может быть пустой):
{history}

Когда готов сохранить пост — вызови save_draft. Не вызывай save_draft больше
одного раза подряд без учёта фидбека от предыдущего вызова (если он вернул
status="rejected", перепиши текст с учётом issues и вызови save_draft снова).
"""


class ContentAgent(ReActAgent):
    def __init__(self, llm_client: LLMClient | None = None, **kwargs):
        super().__init__(agent_name="content", **kwargs)
        self._llm_client = llm_client or LLMClient()
        self._critic_failures = 0
        self._done = False
        self._system_prompt_cache: str | None = None
        self._active_style = None

    def tools(self) -> dict:
        return {
            "get_recent_posts": self._tool_get_recent_posts,
            "get_top_performers": self._tool_get_top_performers,
            "get_swipe_examples": self._tool_get_swipe_examples,
            "web_search": lambda query: web_search(query),
            "verify_source": lambda url: verify_source(url),
            "save_draft": self._tool_save_draft,
        }

    def _tool_get_recent_posts(self):
        with session_scope() as session:
            return [p.text for p in get_recent_posts(session, n=30)]

    def _tool_get_top_performers(self):
        with session_scope() as session:
            return [p.text for p in get_top_performers(session, n=5)]

    def _tool_get_swipe_examples(self, topic: str | None = None):
        with session_scope() as session:
            return [e.text for e in get_swipe_examples(session, n=8, topic=topic)]

    def system_prompt(self) -> str:
        if self._system_prompt_cache is None:
            with open(CONSTITUTION_PATH, encoding="utf-8") as f:
                constitution = f.read()
            with session_scope() as session:
                kb = get_knowledge_base(session)
                self._active_style = get_active_style(session)
                genome = self._active_style.genome if self._active_style else "(нет активного стилевого варианта)"
                rules = [r.rule_text for r in get_active_playbook_rules(session)]
                swipe = [e.text for e in get_swipe_examples(session, n=8)]
                top = [p.text for p in get_top_performers(session, n=5)]
            self._system_prompt_cache = assemble_system_prompt(
                constitution=constitution,
                knowledge_base=kb,
                active_genome=genome,
                playbook_rules=rules,
                swipe_examples=swipe,
                top_posts=top,
            )
        return self._system_prompt_cache

    def _next_publish_slot(self) -> datetime:
        settings = load_settings()
        tz = pytz.timezone(settings["publish_timezone"])
        times = sorted(settings["publish_times"])
        now = datetime.now(tz)

        with session_scope() as session:
            from src.db.models import Post
            from sqlalchemy import select
            taken = {
                p.scheduled_at.astimezone(tz)
                for p in session.execute(
                    select(Post).where(Post.status.in_(["scheduled", "published"]))
                ).scalars().all()
                if p.scheduled_at is not None
            }

        day_offset = 0
        while True:
            candidate_day = (now + timedelta(days=day_offset)).date()
            for time_str in times:
                hour, minute = (int(x) for x in time_str.split(":"))
                candidate = tz.localize(datetime.combine(candidate_day, datetime.min.time()).replace(hour=hour, minute=minute))
                if candidate <= now:
                    continue
                if candidate not in taken:
                    return candidate
            day_offset += 1

    def _tool_save_draft(self, text: str, category: str):
        genome = self._active_style.genome if self._active_style else ""
        with session_scope() as session:
            recent_texts = [p.text for p in get_recent_posts(session, n=30)]

        critique = run_style_critic(
            text=text,
            category=category,
            source_url=None,
            genome=genome,
            recent_post_texts=recent_texts,
            llm_client=self._llm_client,
            run_id=self._run_id,
        )
        self.note_llm_usage(critique["tokens_in"], critique["tokens_out"], critique["cost_usd"])

        if critique["pass"]:
            return self._persist_post(text, category, status="scheduled")

        self._critic_failures += 1
        if self._critic_failures >= 2:
            self._persist_post(text, category, status="needs_review")
            send_telegram_alert(
                f"content_agent: пост требует ручной проверки — style_critic дважды отклонил черновик: {critique['issues']}"
            )
            self._done = True
            return {"status": "needs_review", "issues": critique["issues"]}

        return {"status": "rejected", "issues": critique["issues"]}

    def _persist_post(self, text: str, category: str, status: str) -> dict:
        style_variant_id = self._active_style.id if self._active_style else None
        with session_scope() as session:
            post = insert_post(
                session,
                text=text,
                category=category,
                status=status,
                style_variant_id=style_variant_id,
                scheduled_at=self._next_publish_slot() if status == "scheduled" else None,
                model_used=self._llm_client._config["roles"]["post_writer"]["model"] if hasattr(self._llm_client, "_config") else None,
            )
            if status == "scheduled" and style_variant_id:
                increment_style_variant_posts_n(session, style_variant_id)
            post_id = post.id
        self._done = True
        return {"status": status, "post_id": post_id}

    def decide_next_action(self, history: list[dict]) -> dict | None:
        if self._done:
            return None

        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": TOOL_SELECTION_PROMPT.format(history=json.dumps(history, ensure_ascii=False, default=str))},
        ]
        response = self._llm_client.complete(role="post_writer", messages=messages, run_id=self._run_id)
        self.note_llm_usage(response.tokens_in, response.tokens_out, response.cost_usd)

        try:
            parsed = json.loads(response.text)
            return {
                "thought": parsed.get("thought"),
                "tool_name": parsed["tool_name"],
                "tool_args": parsed.get("tool_args", {}),
            }
        except (json.JSONDecodeError, KeyError, TypeError):
            return {"thought": f"invalid tool-call JSON: {response.text[:200]!r}", "tool_name": "__parse_error__", "tool_args": {}}
```

Add `pytz` to `requirements.txt` (`pytz>=2024.1,<2025.0`) — `zoneinfo` (stdlib since 3.9) would also work, but this project's Docker image and host venv both already resolve `pytz` transitively via other dependencies in practice; pin it explicitly since we now import it directly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/test_content.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all previously-passing tests still pass, plus these 4 (approximately 65 total by this point).

- [ ] **Step 6: Commit**

```bash
git add src/agents/content.py requirements.txt tests/agents/test_content.py
git commit -m "feat: add ContentAgent — ReAct content generation with style_critic gate"
```

---

### Task 8: `src/agents/publisher.py` — scheduled publishing

**Files:**
- Create: `src/agents/publisher.py`
- Test: `tests/agents/test_publisher.py`

**Interfaces:**
- Consumes: `get_posts_due_for_publish` (Task 1), `ThreadsWriteClient.publish_text_post(text) -> str` (Block 0/1, returns media id, raises `ThreadsAPIError`/`PublishingLimitExceeded`), `send_telegram_alert` (Block 2), `start_agent_run`/`add_agent_step`/`finish_agent_run` (Block 0/1).
- Produces: `publish_scheduled_posts(trigger: str = "cron", write_client: "ThreadsWriteClient | None" = None) -> dict` returning `{"published": int, "failed": int}` — consumed by Task 9's scheduler wiring. Mirrors `feed_miner`'s (Block 2) deterministic, non-ReAct, fully-traced shape exactly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_publisher.py
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.agents.publisher import publish_scheduled_posts
from src.db.models import AgentRun, Post
from src.db.repo import insert_post
from src.threads.write_client import PublishingLimitExceeded, ThreadsAPIError


class _FakeWriteClient:
    def __init__(self, media_id="media-123", raises=None):
        self._media_id = media_id
        self._raises = raises
        self.published_texts = []

    def publish_text_post(self, text):
        if self._raises:
            raise self._raises
        self.published_texts.append(text)
        return self._media_id


def test_publish_scheduled_posts_publishes_due_posts(db_session):
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="due now", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=1))
    insert_post(db_session, text="future", category="educational", status="scheduled", scheduled_at=now + timedelta(hours=1))
    db_session.commit()

    write_client = _FakeWriteClient(media_id="abc123")
    result = publish_scheduled_posts(trigger="manual", write_client=write_client)

    assert result == {"published": 1, "failed": 0}
    assert write_client.published_texts == ["due now"]

    published = db_session.query(Post).filter_by(text="due now").one()
    assert published.status == "published"
    assert published.threads_media_id == "abc123"
    assert published.posted_at is not None

    future = db_session.query(Post).filter_by(text="future").one()
    assert future.status == "scheduled"


def test_publish_scheduled_posts_marks_failed_and_alerts_on_api_error(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="will fail", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=1))
    db_session.commit()

    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.publisher.send_telegram_alert", alert_mock)
    write_client = _FakeWriteClient(raises=ThreadsAPIError("publish failed"))

    result = publish_scheduled_posts(trigger="manual", write_client=write_client)

    assert result == {"published": 0, "failed": 1}
    failed_post = db_session.query(Post).filter_by(text="will fail").one()
    assert failed_post.status == "failed"
    alert_mock.assert_called_once()


def test_publish_scheduled_posts_stops_on_publishing_limit_exceeded(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="post 1", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=2))
    insert_post(db_session, text="post 2", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=1))
    db_session.commit()

    alert_mock = MagicMock(return_value=True)
    monkeypatch.setattr("src.agents.publisher.send_telegram_alert", alert_mock)
    write_client = _FakeWriteClient(raises=PublishingLimitExceeded("250/day limit hit"))

    result = publish_scheduled_posts(trigger="manual", write_client=write_client)

    assert result["published"] == 0
    # Both posts attempted-and-failed, OR the first failure stops the batch —
    # either is acceptable as long as no post is silently left "scheduled"
    # forever and at least one alert fired.
    remaining_scheduled = db_session.query(Post).filter_by(status="scheduled").count()
    assert remaining_scheduled == 0
    alert_mock.assert_called()


def test_publish_scheduled_posts_traces_to_agent_runs(db_session):
    now = datetime.now(timezone.utc)
    insert_post(db_session, text="traced post", category="educational", status="scheduled", scheduled_at=now - timedelta(minutes=1))
    db_session.commit()

    publish_scheduled_posts(trigger="manual", write_client=_FakeWriteClient())

    run = db_session.query(AgentRun).filter_by(agent="content_publisher").one()
    assert run.status == "ok"
    assert run.finished_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/agents/test_publisher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.agents.publisher'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agents/publisher.py
import os
from datetime import datetime, timezone

from src.alerts import send_telegram_alert
from src.db.engine import session_scope
from src.db.repo import add_agent_step, finish_agent_run, get_posts_due_for_publish, start_agent_run
from src.threads.write_client import PublishingLimitExceeded, ThreadsAPIError, ThreadsWriteClient


def publish_scheduled_posts(trigger: str = "cron", write_client: ThreadsWriteClient | None = None) -> dict:
    """Deterministic, not a ReActAgent — mirrors feed_miner's shape (Block 2).
    Publishes every due post in one pass; a PublishingLimitExceeded stops
    the whole batch (retrying more posts would only fail identically)."""
    write_client = write_client or ThreadsWriteClient(os.environ["THREADS_ACCESS_TOKEN"], os.environ["THREADS_USER_ID"])

    with session_scope() as session:
        run = start_agent_run(session, agent="content_publisher", trigger=trigger)
        run_id = run.id

    published, failed = 0, 0
    status = "ok"
    error = None
    step_no = 0

    try:
        with session_scope() as session:
            due_post_ids = [p.id for p in get_posts_due_for_publish(session)]

        for post_id in due_post_ids:
            step_no += 1
            with session_scope() as session:
                from src.db.models import Post
                post = session.get(Post, post_id)
                text = post.text

            tool_ok = True
            tool_result = None
            try:
                media_id = write_client.publish_text_post(text)
                with session_scope() as session:
                    post = session.get(Post, post_id)
                    post.status = "published"
                    post.threads_media_id = media_id
                    post.posted_at = datetime.now(timezone.utc)
                published += 1
                tool_result = {"post_id": post_id, "media_id": media_id}
            except PublishingLimitExceeded as exc:
                tool_ok = False
                tool_result = str(exc)
                with session_scope() as session:
                    post = session.get(Post, post_id)
                    post.status = "failed"
                failed += 1
                send_telegram_alert(f"content_publisher: остановлен — {exc}")
                with session_scope() as session:
                    add_agent_step(session, run_id=run_id, step_no=step_no, tool_name="publish_text_post", tool_args={"post_id": post_id}, tool_result=tool_result, tool_ok=tool_ok)
                break
            except ThreadsAPIError as exc:
                tool_ok = False
                tool_result = str(exc)
                with session_scope() as session:
                    post = session.get(Post, post_id)
                    post.status = "failed"
                failed += 1
                send_telegram_alert(f"content_publisher: публикация не удалась (post_id={post_id}): {exc}")

            with session_scope() as session:
                add_agent_step(session, run_id=run_id, step_no=step_no, tool_name="publish_text_post", tool_args={"post_id": post_id}, tool_result=tool_result, tool_ok=tool_ok)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error = str(exc)

    with session_scope() as session:
        finish_agent_run(session, run_id, status=status, steps_count=step_no, error=error, output_ref=f"published={published} failed={failed}")

    return {"published": published, "failed": failed}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/agents/test_publisher.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/agents/publisher.py tests/agents/test_publisher.py
git commit -m "feat: add scheduled-publish job with retries-via-batch and Telegram alerting"
```

---

### Task 9: Wire `content_agent` and `publisher` into the scheduler

**Files:**
- Modify: `src/scheduler.py`
- Test: extend `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `ContentAgent` (Task 7), `publish_scheduled_posts` (Task 8), `get_posts_due_for_publish`/queue-depth counting.
- Produces: two more scheduled jobs alongside the existing `feed_miner_morning`/`feed_miner_evening` (Block 2) — `content_agent_hourly` (checks `queue_depth` before running) and `publisher_every_10_min`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scheduler.py`:

```python
def test_build_scheduler_registers_content_and_publisher_jobs():
    scheduler = build_scheduler()
    jobs = {j.id: j for j in scheduler.get_jobs()}

    assert "content_agent_hourly" in jobs
    assert jobs["content_agent_hourly"].func.__name__ == "run_content_agent_if_queue_low"

    assert "publisher_every_10_min" in jobs
    assert jobs["publisher_every_10_min"].func.__name__ == "publish_scheduled_posts"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scheduler.py -v`
Expected: FAIL — `KeyError: 'content_agent_hourly'`

- [ ] **Step 3: Modify `src/scheduler.py`**

```python
# src/scheduler.py
import time

from apscheduler.schedulers.background import BackgroundScheduler

from src.agents.content import ContentAgent
from src.agents.feed_miner import run_feed_miner
from src.agents.publisher import publish_scheduled_posts
from src.config import load_settings
from src.db.engine import session_scope

TIMEZONE = "Asia/Almaty"


def run_content_agent_if_queue_low():
    from sqlalchemy import func, select
    from src.db.models import Post

    queue_depth = load_settings()["queue_depth"]
    with session_scope() as session:
        scheduled_count = session.execute(
            select(func.count()).select_from(Post).where(Post.status == "scheduled")
        ).scalar_one()

    if scheduled_count < queue_depth:
        ContentAgent().run(trigger="queue_low")


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        run_feed_miner, trigger="cron", hour=8, minute=0,
        id="feed_miner_morning", kwargs={"trigger": "cron"},
    )
    scheduler.add_job(
        run_feed_miner, trigger="cron", hour=20, minute=0,
        id="feed_miner_evening", kwargs={"trigger": "cron"},
    )
    scheduler.add_job(
        run_content_agent_if_queue_low, trigger="cron", minute=0,
        id="content_agent_hourly",
    )
    scheduler.add_job(
        publish_scheduled_posts, trigger="interval", minutes=10,
        id="publisher_every_10_min", kwargs={"trigger": "cron"},
    )
    return scheduler


def main():
    scheduler = build_scheduler()
    scheduler.start()
    print(f"worker started — feed_miner 08:00/20:00, content_agent hourly, publisher every 10min ({TIMEZONE})")
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
Expected: PASS (2/2)

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 6: Verify the worker container still starts cleanly**

```bash
docker compose up -d --build worker
docker compose logs worker
```

Expected: the updated startup banner appears (PYTHONUNBUFFERED is already set from Block 2), no crash — confirms `pytz` (added in Task 7) is actually present in the built image via `requirements.txt`.

- [ ] **Step 7: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py
git commit -m "feat: schedule content_agent (hourly, queue-aware) and publisher (every 10min)"
```

---

## Self-Review Notes

- **Spec coverage:** T3.1 (Task 2), T3.2 (Task 3, placeholder — human replaces genome), T3.3 (Task 4), T3.4 (Task 7), T3.5 (Task 6), T3.6 (Task 8+9). The business decision (3 posts/day, 200-400 chars) is threaded through `settings.yaml` (Task 2), `style_critic` (Task 6), and `ContentAgent._next_publish_slot` (Task 7).
- **Known limitation, explicitly not fixed by this plan:** the placeholder genome (Task 3) must be replaced by the human operator before any post reaches real production. The seed script's own printed output and its module docstring both say this. Block 3's acceptance criterion ("7 дней подряд посты генерируются... без ручного вмешательства") cannot be truly satisfied until that swap happens — this plan makes the pipeline capable of it, not exempt from it.
- **`web_search`'s provider (Tavily) is a judgment call**, not spec-mandated — SPEC.md only specifies the tool's signature. Isolated to `src/tools/web_search.py` so switching providers later touches one file. Needs `TAVILY_API_KEY` — same class of manual credential setup as every other external service in this project.
- **No placeholders in the code sense:** every step has runnable code. The style-variant genome is a labeled placeholder BY DESIGN per the human's explicit choice ("You write it, I build around it") — this is a real-world content dependency, not a plan-writing shortcut.
- **Type consistency checked:** `run_style_critic`'s return dict (`{"pass", "issues", "tokens_in", "tokens_out", "cost_usd"}`) is used identically in Task 6's own tests and Task 7's `ContentAgent._tool_save_draft`. `publish_scheduled_posts`'s return shape (`{"published", "failed"}`) matches its own tests. `ContentAgent`'s constructor (`llm_client: LLMClient | None = None`) matches `feed_miner`'s established DI pattern from Block 2.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-02-block-3-content.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
