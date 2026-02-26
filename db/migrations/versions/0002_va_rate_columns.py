"""Add VA rate columns to assumptions table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assumptions", sa.Column(
        "current_va_rate", sa.Float(), nullable=True
    ))
    op.add_column("assumptions", sa.Column(
        "rate_fetched_at", sa.DateTime(timezone=True), nullable=True
    ))
    op.add_column("assumptions", sa.Column(
        "rate_is_stale", sa.Boolean(), nullable=False, server_default="false"
    ))
    op.add_column("assumptions", sa.Column(
        "rate_source", sa.Text(), nullable=False, server_default="fallback"
    ))


def downgrade() -> None:
    op.drop_column("assumptions", "rate_source")
    op.drop_column("assumptions", "rate_is_stale")
    op.drop_column("assumptions", "rate_fetched_at")
    op.drop_column("assumptions", "current_va_rate")
