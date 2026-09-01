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
