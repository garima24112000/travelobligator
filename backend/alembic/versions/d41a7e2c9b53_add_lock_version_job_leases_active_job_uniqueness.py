"""add_lock_version_job_leases_active_job_uniqueness

Revision ID: d41a7e2c9b53
Revises: cc7238a1bca3
Create Date: 2026-09-26 12:00:00.000000

Section 200C: database-level concurrency control for the 200A Postgres
schema. Three additive changes, no table created/dropped:

  1. `planning_states.lock_version INTEGER NOT NULL DEFAULT 0` -- the
     optimistic-concurrency token. Every successful `PlanningState` write is
     `UPDATE ... WHERE trip_id = :id AND lock_version = :expected SET
     lock_version = lock_version + 1`, so a stale writer updates zero rows
     and is rejected instead of silently overwriting newer state. Existing
     rows start at 0 (the server default backfills them).
  2. `generation_jobs.lease_owner / lease_expires_at / heartbeat_at` (all
     nullable) -- DB-backed job ownership so several backend instances can
     share one database: a job is claimed with a conditional UPDATE, kept
     alive by heartbeats from its owner, and becomes reclaimable only when
     its lease has expired. Existing rows read back with NULL lease fields
     (an ownerless legacy job is treated as reclaimable once it is stale).
  3. A partial UNIQUE index `uq_generation_jobs_one_active_per_trip` on
     `generation_jobs(trip_id) WHERE status IN ('queued', 'running')` -- the
     database itself guarantees at most one active job per trip, closing the
     read-then-create race in `check_no_duplicate_running_job`.

Safe on an existing 200A database: before the unique index is created, any
trip that (through that old race) already has more than one active job keeps
its OLDEST active job and the others are closed as failed/JOB_INTERRUPTED --
the same honest terminal state startup recovery already uses -- so the index
can always be built. No plan data is touched.

Downgrade removes the index and the four columns; `lock_version` and lease
values are discarded (they carry no product data).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd41a7e2c9b53'
down_revision: Union[str, Sequence[str], None] = 'cc7238a1bca3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE_JOB_PREDICATE = "status IN ('queued', 'running')"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "planning_states",
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_planning_states_lock_version_non_negative",
        "planning_states",
        "lock_version >= 0",
    )

    op.add_column("generation_jobs", sa.Column("lease_owner", sa.Text(), nullable=True))
    op.add_column(
        "generation_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "generation_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Close any pre-existing duplicate active jobs (keep the oldest per trip) so the
    # unique index below can always be created on a 200A-era database.
    op.execute(
        sa.text(
            """
            UPDATE generation_jobs
               SET status = 'failed',
                   error_code = 'JOB_INTERRUPTED',
                   error_message = 'This background job was interrupted before it completed '
                                   '(a duplicate active job existed for the trip). Start generation again.',
                   message = 'Job failed.',
                   finished_at = now()
             WHERE status IN ('queued', 'running')
               AND job_id NOT IN (
                   SELECT DISTINCT ON (trip_id) job_id
                     FROM generation_jobs
                    WHERE status IN ('queued', 'running')
                    ORDER BY trip_id, created_at, job_id
               )
            """
        )
    )
    op.create_index(
        "uq_generation_jobs_one_active_per_trip",
        "generation_jobs",
        ["trip_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_JOB_PREDICATE),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_generation_jobs_one_active_per_trip", table_name="generation_jobs")
    op.drop_column("generation_jobs", "heartbeat_at")
    op.drop_column("generation_jobs", "lease_expires_at")
    op.drop_column("generation_jobs", "lease_owner")
    op.drop_constraint(
        "ck_planning_states_lock_version_non_negative", "planning_states", type_="check"
    )
    op.drop_column("planning_states", "lock_version")
