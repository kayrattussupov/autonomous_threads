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
