from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import AgentRun, AgentStep, DailyLimit, DailySpend, Lead, LlmCall, PlaybookRule, Post, Reply, StyleVariant


class InvalidStateTransition(Exception):
    """Raised when approve/reject targets a row that isn't in its expected pending state."""


class RetirementBlocked(Exception):
    """Raised when approving a style variant would retire another one with posts_n < 20."""


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


def list_agent_runs(session: Session, limit: int = 50) -> list[AgentRun]:
    stmt = select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


def list_agent_steps(session: Session, run_id: int) -> list[AgentStep]:
    stmt = select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.step_no.asc())
    return list(session.execute(stmt).scalars().all())


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


def _months_ago_start(months: int, today: date | None = None) -> datetime:
    today = today or datetime.now(timezone.utc).date()
    total_months = today.year * 12 + (today.month - 1) - (months - 1)
    year, month = divmod(total_months, 12)
    return datetime(year, month + 1, 1, tzinfo=timezone.utc)


def get_funnel(session: Session, months: int = 6) -> list[dict]:
    since = _months_ago_start(months)

    def _key(dt: datetime) -> str:
        return dt.strftime("%Y-%m")

    months_map: dict[str, dict] = {}

    def _bucket(month_key: str) -> dict:
        return months_map.setdefault(
            month_key, {"posts": 0, "views": 0, "replies": 0, "conversations": 0, "leads": 0}
        )

    posts_month = func.date_trunc("month", Post.posted_at)
    posts_rows = session.execute(
        select(
            posts_month.label("month"),
            func.count(Post.id).label("posts"),
            func.coalesce(func.sum(Post.views), 0).label("views"),
            func.coalesce(func.sum(Post.replies_count), 0).label("replies"),
        )
        .where(Post.posted_at.is_not(None), Post.posted_at >= since)
        .group_by(posts_month)
    ).all()
    for row in posts_rows:
        bucket = _bucket(_key(row.month))
        bucket["posts"] = row.posts
        bucket["views"] = int(row.views)
        bucket["replies"] = int(row.replies)

    replies_month = func.date_trunc("month", Reply.received_at)
    conversations_rows = session.execute(
        select(
            replies_month.label("month"),
            func.count(Reply.id).label("conversations"),
        )
        .where(
            Reply.kind.in_(["question", "objection"]),
            Reply.responded_at.is_not(None),
            Reply.received_at.is_not(None),
            Reply.received_at >= since,
        )
        .group_by(replies_month)
    ).all()
    for row in conversations_rows:
        _bucket(_key(row.month))["conversations"] = row.conversations

    leads_month = func.date_trunc("month", Lead.created_at)
    leads_rows = session.execute(
        select(
            leads_month.label("month"),
            func.count(Lead.id).label("leads"),
        )
        .where(Lead.created_at >= since)
        .group_by(leads_month)
    ).all()
    for row in leads_rows:
        _bucket(_key(row.month))["leads"] = row.leads

    return [{"month": month, **data} for month, data in sorted(months_map.items())]
