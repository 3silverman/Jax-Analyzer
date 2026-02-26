"""0004 — My Settings preset: add my_settings_snapshot column, extend active_preset check

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the my_settings_snapshot JSON column
    op.add_column(
        "assumptions",
        sa.Column("my_settings_snapshot", postgresql.JSONB(), nullable=True),
    )

    # Extend the active_preset CHECK constraint to include 'my_settings'.
    # Postgres requires dropping the old constraint first.
    op.execute(
        "ALTER TABLE assumptions DROP CONSTRAINT IF EXISTS assumptions_active_preset_check"
    )
    op.execute(
        "ALTER TABLE assumptions ADD CONSTRAINT assumptions_active_preset_check "
        "CHECK (active_preset IN ('conservative', 'base', 'optimistic', 'custom', 'my_settings'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE assumptions DROP CONSTRAINT IF EXISTS assumptions_active_preset_check"
    )
    op.execute(
        "ALTER TABLE assumptions ADD CONSTRAINT assumptions_active_preset_check "
        "CHECK (active_preset IN ('conservative', 'base', 'optimistic', 'custom'))"
    )
    # Reset any my_settings rows to custom before dropping column
    op.execute(
        "UPDATE assumptions SET active_preset = 'custom' "
        "WHERE active_preset = 'my_settings'"
    )
    op.drop_column("assumptions", "my_settings_snapshot")
