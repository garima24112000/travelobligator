"""create_trips_and_planning_states

Revision ID: 2fe86f95d81e
Revises:
Create Date: 2026-09-09 22:14:39.312644

Step 183C: the first Postgres schema migration for the opt-in Postgres
persistence backend (`Settings.persistence_backend`, still `"local_json"`
by default -- see docs/14_backend_architecture.md section 104). Nothing
in `app/api`, `app/services`, or `app/repositories` reads or writes these
tables yet; that wiring is Step 183D's job. Applying this migration to a
real Postgres database has zero effect on current app behavior.

Mirrors the two existing local-JSON repositories exactly
(`app/repositories/trip_repository.py`'s `TripRecord`,
`app/repositories/planning_state_repository.py`'s whole-`PlanningState`
document) -- no normalized feedback/version/lock/regeneration/narrative
tables, no `provider_cache` table (that stays SQLite, see
`app/storage/provider_cache_store.py`), and no `user_id`/`owner_id`/auth
column anywhere (auth/user isolation is unimplemented today and out of
scope for this step).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '2fe86f95d81e'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "trips",
        sa.Column("trip_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trip_id"),
    )
    op.create_index("ix_trips_status", "trips", ["status"])
    op.create_index("ix_trips_updated_at", "trips", ["updated_at"])

    op.create_table(
        "planning_states",
        sa.Column("trip_id", sa.Text(), nullable=False),
        sa.Column("planning_state_id", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Text(), nullable=False),
        sa.Column("pipeline_status", sa.Text(), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.trip_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("trip_id"),
    )
    op.create_index("ix_planning_states_current_version", "planning_states", ["current_version"])
    op.create_index("ix_planning_states_pipeline_status", "planning_states", ["pipeline_status"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_planning_states_pipeline_status", table_name="planning_states")
    op.drop_index("ix_planning_states_current_version", table_name="planning_states")
    op.drop_table("planning_states")

    op.drop_index("ix_trips_updated_at", table_name="trips")
    op.drop_index("ix_trips_status", table_name="trips")
    op.drop_table("trips")
