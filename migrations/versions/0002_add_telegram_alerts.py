"""add telegram_alerts

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "telegram_alerts",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_detail", sa.Text),
        sa.Column("chat_id", sa.Text),
        sa.Column("message_id", sa.BigInteger),
        sa.Column("retry_count", sa.Integer, server_default="1"),
    )
    op.create_index("ix_telegram_alerts_sent_at", "telegram_alerts", ["sent_at"])


def downgrade():
    op.drop_table("telegram_alerts")
