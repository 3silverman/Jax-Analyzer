"""0005 — Add rentcast_cache table for 7-day per-property API call deduplication

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rentcast_cache",
        sa.Column("canonical_id", sa.Text(), primary_key=True),
        sa.Column("zip_code",     sa.Text(), nullable=False),
        sa.Column("ltr_estimate", sa.Float(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("rentcast_cache")
