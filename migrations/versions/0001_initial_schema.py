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
