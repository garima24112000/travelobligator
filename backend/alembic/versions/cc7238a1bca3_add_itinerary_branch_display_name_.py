"""add_itinerary_branch_display_name_uniqueness

Revision ID: cc7238a1bca3
Revises: 7cdda8ff3506
Create Date: 2026-09-22 12:00:00.000000

Section 199B: adds ONE new database-level safeguard on top of the 199A
`itinerary_branches`/`itinerary_revisions` schema -- a case-insensitive
unique index on `itinerary_branches(trip_id, lower(display_name))`,
enforcing Task 11/48's "at most one branch with a given display name per
trip" decision at the database level (real, atomic protection against two
concurrent fork-creation requests racing with the same name), not just in
`ItineraryForkService.create_fork`'s own read-check-then-create.

No new column, no new table -- `PlanningState.metadata.active_branch_id`
(Section 199B's other schema-relevant addition) required NO migration at
all, since `PlanningStateRow.state` already stores the entire
`PlanningState` as one JSONB blob (see `app.models.planning_state.
PlanningMetadata.active_branch_id`'s own docstring for why this section
deliberately put it there instead of a new `trips` column).

Applying this migration to a real Postgres database has zero effect on
current app behavior: nothing writes to `itinerary_branches` at all until
`PERSISTENCE_BACKEND=postgres` is explicitly set AND a real fork-creation
request reaches `ItineraryForkService`.

`ItineraryForkService.create_fork` always trims `display_name` before
storing it, so this index's `lower(display_name)` expression alone
(no separate trim) is sufficient -- no stored value is ever untrimmed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc7238a1bca3'
down_revision: Union[str, Sequence[str], None] = '7cdda8ff3506'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "uq_itinerary_branches_trip_id_display_name_lower",
        "itinerary_branches",
        ["trip_id", sa.text("lower(display_name)")],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_itinerary_branches_trip_id_display_name_lower",
        table_name="itinerary_branches",
    )
