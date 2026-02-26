"""Initial schema — all tables.

Revision ID: 0001
Revises:
Create Date: 2026-02-26

This migration applies the full schema from db/schema.sql.
For the initial deploy, it is simpler to run schema.sql directly via
Supabase SQL editor. This file exists for Alembic revision tracking.
"""

from __future__ import annotations

from alembic import op

# revision identifiers
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The full DDL is in db/schema.sql — run it manually for initial deploy.
    # For subsequent migrations, add incremental ALTER TABLE statements here.
    op.execute("SELECT 1")   # no-op placeholder


def downgrade() -> None:
    op.execute("SELECT 1")   # destructive drop intentionally omitted
