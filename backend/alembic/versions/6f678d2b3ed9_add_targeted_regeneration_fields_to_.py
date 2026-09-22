"""add_targeted_regeneration_fields_to_generation_jobs

Revision ID: 6f678d2b3ed9
Revises: 6fabeaa6455e
Create Date: 2026-09-21 00:00:00.000000

Section 198B: extends `generation_jobs` with the same targeted-regeneration
result fields the sync `/regenerate` route already returns
(`RegenerateResponseData`), so a completed async targeted-regeneration job
carries the exact same canonical `TargetedRegenerationDiff` (and related
status/clarification fields) instead of a second, hand-derived shape --
see `app.models.generation_job.GenerationJob` and
`app.schemas.generation_job.JobResponseData`.

Applying this migration to a real Postgres database has zero effect on
current app behavior: nothing in `app/repositories/factory.py` reads
Postgres for jobs until `PERSISTENCE_BACKEND=postgres` is explicitly set
(and even then `ASYNC_GENERATION_ENABLED`/`TARGETED_REGENERATION_ENABLED`
still default `false`). Every new column is nullable or has a server
default, so every existing row (any `generate`/legacy `regenerate` job,
or any targeted job written before this migration) reads back with the
same defaults `GenerationJob`'s own Pydantic field defaults already use
(`targeted=False`, empty lists, `None` for the rest) -- no backfill is
required or performed.

Column-by-column notes:
  * `diff` is `JSONB NULLABLE` -- it stores
    `TargetedRegenerationDiff.model_dump(mode="json")` verbatim (the same
    canonical model the sync route returns), never a second hand-derived
    diff shape. `None` for any non-targeted job or a targeted job that
    never reached a diff (e.g. it ended in clarification).
  * `targeted` is `BOOLEAN NOT NULL DEFAULT false` -- mirrors
    `GenerationJob.targeted: bool = False`, which is never `None`.
  * `affected_day_indices`/`preserved_day_indices`/
    `clarification_possible_experience_ids` are `JSONB NOT NULL DEFAULT
    '[]'` -- mirror the corresponding `list[...]` fields on
    `GenerationJob`, which are never `None`.
  * `previous_version`/`interpretation_status`/`execution_status`/
    `clarification_reason` are `TEXT NULLABLE` -- mirror the
    corresponding `str | None` fields on `GenerationJob`.

Deliberately excludes: any change to `changed_sections`/`result_version`
(unchanged from Step 186F); any provider fact/result payload column (the
actual plan lives in `planning_states.state`, untouched by this
migration).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '6f678d2b3ed9'
down_revision: Union[str, Sequence[str], None] = '6fabeaa6455e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("generation_jobs", sa.Column("previous_version", sa.Text(), nullable=True))
    op.add_column(
        "generation_jobs",
        sa.Column("targeted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "generation_jobs", sa.Column("interpretation_status", sa.Text(), nullable=True)
    )
    op.add_column("generation_jobs", sa.Column("execution_status", sa.Text(), nullable=True))
    op.add_column(
        "generation_jobs",
        sa.Column(
            "affected_day_indices",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "preserved_day_indices",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("diff", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "generation_jobs", sa.Column("clarification_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "generation_jobs",
        sa.Column(
            "clarification_possible_experience_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("generation_jobs", "clarification_possible_experience_ids")
    op.drop_column("generation_jobs", "clarification_reason")
    op.drop_column("generation_jobs", "diff")
    op.drop_column("generation_jobs", "preserved_day_indices")
    op.drop_column("generation_jobs", "affected_day_indices")
    op.drop_column("generation_jobs", "execution_status")
    op.drop_column("generation_jobs", "interpretation_status")
    op.drop_column("generation_jobs", "targeted")
    op.drop_column("generation_jobs", "previous_version")
