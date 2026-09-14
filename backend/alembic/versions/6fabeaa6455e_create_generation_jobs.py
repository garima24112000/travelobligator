"""create_generation_jobs

Revision ID: 6fabeaa6455e
Revises: 835e5782d7a5
Create Date: 2026-09-13 00:00:00.000000

Step 186F: the third Postgres schema migration for the opt-in Postgres
persistence backend (`Settings.persistence_backend`, still `"local_json"`
by default -- see docs/14_backend_architecture.md section 116-118).
Applying this migration to a real Postgres database has zero effect on
current app behavior: nothing in `app/repositories/factory.py` reads
Postgres for jobs until `PERSISTENCE_BACKEND=postgres` is explicitly set
(and even then `ASYNC_GENERATION_ENABLED` still defaults `false`).

Mirrors `app.models.generation_job.GenerationJob` exactly -- one row per
job, storing job *control* state (identity, ownership, lifecycle, error)
only. No provider fact, no stack trace, no secret/password/session
token/API key is ever stored here, matching that model's own docstring.

Column-by-column notes:
  * `result_version` is `TEXT`, not `INTEGER` -- it mirrors
    `GenerationJob.result_version: str | None`, which holds a version
    *label* like `"v2"` (see `VersionHistoryItem.version_label`), never a
    numeric id.
  * `owner_id` is `NOT NULL` with `ON DELETE CASCADE`, unlike
    `trips.owner_id` (nullable + `SET NULL`, Step 184C). That column is
    nullable only for backward compatibility with pre-184C trip rows and
    a trip's planning data must outlive its owner; a `GenerationJob`
    always has a real `owner_id` from creation (`GenerationJob.owner_id:
    str`, never optional -- copied from the authenticated trip owner at
    job-creation time, Step 186C), so there is no legacy-row case to stay
    nullable for, and a job record has no independent value once its
    owner is gone.
  * `trip_id` uses `ON DELETE CASCADE` (matching `planning_states.trip_id`
    from the Step 183C migration) -- a job is meaningless without its
    trip.
  * `changed_sections` is `JSONB NOT NULL`, defaulting to an empty JSON
    array `[]` -- mirrors `GenerationJob.changed_sections: list[str] =
    []`, which is never `None`.

Deliberately excludes: any provider fact/result payload column (the
actual plan lives in `planning_states.state`, untouched by this
migration); any change to `provider_cache` (stays SQLite, independent,
see `app/storage/provider_cache_store.py`).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '6fabeaa6455e'
down_revision: Union[str, Sequence[str], None] = '835e5782d7a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "generation_jobs",
        sa.Column("job_id", sa.Text(), nullable=False),
        sa.Column("trip_id", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("progress_stage", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_version", sa.Text(), nullable=True),
        sa.Column(
            "changed_sections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.PrimaryKeyConstraint("job_id"),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.trip_id"], name="fk_generation_jobs_trip_id_trips", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.user_id"], name="fk_generation_jobs_owner_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_generation_jobs_trip_id", "generation_jobs", ["trip_id"])
    op.create_index("ix_generation_jobs_owner_id", "generation_jobs", ["owner_id"])
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])
    op.create_index(
        "ix_generation_jobs_trip_id_status", "generation_jobs", ["trip_id", "status"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_generation_jobs_trip_id_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_owner_id", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_trip_id", table_name="generation_jobs")
    op.drop_table("generation_jobs")
