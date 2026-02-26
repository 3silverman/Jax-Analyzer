"""0003 — schema additions: deal_card_json, sv_urls, outcome fields, crime cache, presets

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── deal_scores: add deal_card_json ───────────────────────────────────────
    op.add_column("deal_scores",
        sa.Column("deal_card_json", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'")))

    # ── neighborhood_scores: add URL columns ──────────────────────────────────
    op.add_column("neighborhood_scores",
        sa.Column("street_view_url", sa.Text(), nullable=True))
    op.add_column("neighborhood_scores",
        sa.Column("satellite_url", sa.Text(), nullable=True))
    op.add_column("neighborhood_scores",
        sa.Column("maps_link", sa.Text(), nullable=True))

    # ── outcomes: add outcome tracking columns ────────────────────────────────
    op.add_column("outcomes",
        sa.Column("viewed_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("outcomes",
        sa.Column("offer_price", sa.Float(), nullable=True))
    op.add_column("outcomes",
        sa.Column("under_contract", sa.Boolean(), nullable=False,
                  server_default=sa.text("FALSE")))
    op.add_column("outcomes",
        sa.Column("closed_price", sa.Float(), nullable=True))

    # ── assumptions: add active_preset ───────────────────────────────────────
    op.add_column("assumptions",
        sa.Column("active_preset", sa.Text(), nullable=False,
                  server_default=sa.text("'custom'")))

    # ── crime_grade_cache table ───────────────────────────────────────────────
    op.create_table(
        "crime_grade_cache",
        sa.Column("zip_code",       sa.Text(),    primary_key=True),
        sa.Column("grade",          sa.Text(),    nullable=False),
        sa.Column("passes_gate",    sa.Boolean(), nullable=False),
        sa.Column("low_confidence", sa.Boolean(), nullable=False,
                  server_default=sa.text("FALSE")),
        sa.Column("scraped_at",     sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )

    # ── crime_grade_overrides table ───────────────────────────────────────────
    op.create_table(
        "crime_grade_overrides",
        sa.Column("zip_code",    sa.Text(),    primary_key=True),
        sa.Column("grade",       sa.Text(),    nullable=False),
        sa.Column("passes_gate", sa.Boolean(), nullable=False),
        sa.Column("set_by",      sa.Text(),    nullable=False,
                  server_default=sa.text("'manual'")),
        sa.Column("set_at",      sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("crime_grade_overrides")
    op.drop_table("crime_grade_cache")
    op.drop_column("assumptions", "active_preset")
    op.drop_column("outcomes", "closed_price")
    op.drop_column("outcomes", "under_contract")
    op.drop_column("outcomes", "offer_price")
    op.drop_column("outcomes", "viewed_at")
    op.drop_column("neighborhood_scores", "maps_link")
    op.drop_column("neighborhood_scores", "satellite_url")
    op.drop_column("neighborhood_scores", "street_view_url")
    op.drop_column("deal_scores", "deal_card_json")
