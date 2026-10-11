"""add_admission_limit_indexes_and_auth_attempts

Revision ID: e52b7c1a9f04
Revises: d41a7e2c9b53
Create Date: 2026-10-11 12:00:00.000000

P0 abuse / cost controls. Additive only: no existing table, column or row is changed.

  1. `ix_generation_jobs_created_at` and `ix_generation_jobs_owner_id_created_at` -- the two
     rolling-window counts the generation admission check runs for every new job (all jobs in the
     window; one owner's jobs in the window). The limits themselves need no schema: an admitted job is
     simply a `generation_jobs` row, counted by its `created_at`.
  2. `auth_attempts` -- one row per counted signup or failed sign-in, for the signup / sign-in attempt
     limits. It stores keyed hashes only (`subject_hash`, `ip_hash`): never an email address, an IP
     address or a user id. Rows are deleted by the application once they are older than the longest
     counting window, so the table stays small.

Downgrade drops the table and the two indexes; the counters it discards carry no product data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e52b7c1a9f04'
down_revision: Union[str, Sequence[str], None] = 'd41a7e2c9b53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index("ix_generation_jobs_created_at", "generation_jobs", ["created_at"])
    op.create_index(
        "ix_generation_jobs_owner_id_created_at", "generation_jobs", ["owner_id", "created_at"]
    )
    op.create_table(
        "auth_attempts",
        sa.Column("attempt_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("subject_hash", sa.Text(), nullable=True),
        sa.Column("ip_hash", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("attempt_id"),
    )
    op.create_index("ix_auth_attempts_kind_created_at", "auth_attempts", ["kind", "created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_auth_attempts_kind_created_at", table_name="auth_attempts")
    op.drop_table("auth_attempts")
    op.drop_index("ix_generation_jobs_owner_id_created_at", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_created_at", table_name="generation_jobs")
