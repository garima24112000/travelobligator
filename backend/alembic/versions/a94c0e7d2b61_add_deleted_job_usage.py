"""add_deleted_job_usage

Revision ID: a94c0e7d2b61
Revises: e52b7c1a9f04
Create Date: 2026-10-11 16:00:00.000000

P1-A (trip and account deletion). Additive only: no existing table, column or row is changed.

The generation admission limits count `generation_jobs` rows inside a rolling window. Deleting a trip
(or an account) deletes its jobs, which would hand the allowance back: generate, delete, generate again.
`deleted_job_usage` keeps what the limits need from a deleted job and nothing else:

  * `created_at`  -- when the deleted job had been admitted;
  * `owner_id`    -- the account, while it exists (so its own allowance still counts). It is set to NULL
                     when the account is deleted: the row then only counts towards the deployment-wide
                     limit and refers to nobody. Deliberately NOT a foreign key, so deleting the user
                     cannot delete the count.

No trip id, job id, destination, request text or error is kept. Rows are removed by the application once
they are older than the counting window.

Downgrade drops the table; the counts it discards carry no product data.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a94c0e7d2b61'
down_revision: Union[str, Sequence[str], None] = 'e52b7c1a9f04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "deleted_job_usage",
        sa.Column("usage_id", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("usage_id"),
    )
    op.create_index("ix_deleted_job_usage_created_at", "deleted_job_usage", ["created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_deleted_job_usage_created_at", table_name="deleted_job_usage")
    op.drop_table("deleted_job_usage")
