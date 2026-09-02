from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import AgentRun, AgentStep, DailyLimit, DailySpend, KnowledgeBaseEntry, LlmCall, PlaybookRule, Post, StyleVariant, SwipeFilePost


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


def get_daily_limit(session: Session, counter: str, today: date | None = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    row = session.get(DailyLimit, {"date": today, "counter": counter})
    return row.value if row else 0


def increment_daily_limit(session: Session, counter: str, by: int = 1, today: date | None = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    row = session.get(DailyLimit, {"date": today, "counter": counter})
    if row is None:
        row = DailyLimit(date=today, counter=counter, value=0)
        session.add(row)
    row.value = (row.value or 0) + by
    session.flush()
    return row.value


def start_agent_run(session: Session, agent: str, trigger: str) -> AgentRun:
    run = AgentRun(agent=agent, trigger=trigger, started_at=datetime.now(timezone.utc), status="running")
    session.add(run)
    session.flush()
    return run


def add_agent_step(session: Session, run_id: int, step_no: int, **fields) -> AgentStep:
    step = AgentStep(run_id=run_id, step_no=step_no, **fields)
    session.add(step)
    session.flush()
    return step


def finish_agent_run(session: Session, run_id: int, status: str, **fields) -> None:
    run = session.get(AgentRun, run_id)
    run.status = status
    run.finished_at = datetime.now(timezone.utc)
    for key, value in fields.items():
        setattr(run, key, value)


def swipe_file_post_exists(session: Session, threads_post_id: str) -> bool:
    return session.execute(
        select(SwipeFilePost.id).where(SwipeFilePost.threads_post_id == threads_post_id)
    ).scalar_one_or_none() is not None


def insert_swipe_file_post(session: Session, **fields) -> SwipeFilePost:
    post = SwipeFilePost(**fields)
    session.add(post)
    session.flush()
    return post


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


def count_scheduled_posts(session: Session) -> int:
    return session.execute(
        select(func.count()).select_from(Post).where(Post.status == "scheduled")
    ).scalar_one()
