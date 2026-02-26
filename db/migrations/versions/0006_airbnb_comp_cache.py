"""0006 — Add airbnb_comp_cache table for weekly STR comp deduplication

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision      = "0006"
down_revision = "0005"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        "airbnb_comp_cache",
        sa.Column("zip_code",            sa.Text(),    nullable=False),
        sa.Column("bedrooms",            sa.Integer(), nullable=False),
        sa.Column("week_number",         sa.Integer(), nullable=False),
        sa.Column("comp_count",          sa.Integer(), nullable=False, server_default="0"),
        sa.Column("median_adr",          sa.Float(),   nullable=True),
        sa.Column("estimated_occupancy", sa.Float(),   nullable=True),
        sa.Column("gross_monthly",       sa.Float(),   nullable=True),
        sa.Column("net_monthly",         sa.Float(),   nullable=True),
        sa.Column("confidence",          sa.Text(),    nullable=False, server_default="'LOW'"),
        sa.Column("str_validated",       sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("comps_json",          sa.Text(),    nullable=True),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("zip_code", "bedrooms", "week_number"),
    )


def downgrade() -> None:
    op.drop_table("airbnb_comp_cache")
