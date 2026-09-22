"""create_itinerary_branches_and_revisions

Revision ID: 7cdda8ff3506
Revises: 6f678d2b3ed9
Create Date: 2026-09-22 00:00:00.000000

Section 199A: the persistence/domain foundation for TRUE itinerary forks
(NOT fork creation itself -- see `app.services.revision_lineage_service`'s
own module docstring for the full scope boundary). Creates two new
tables:

  * `itinerary_branches` -- one row per named line of revisions for a
    trip. Every existing trip gets exactly one ("Main", `is_default=
    true`) once 199A's lineage bookkeeping is first initialized for it.
  * `itinerary_revisions` -- one row per immutable, addressable revision.
    `snapshot` is `JSONB NULLABLE`, storing the entire serialized
    `PlanningState` verbatim (mirrors `planning_states.state` from the
    Step 183C migration) -- `NULL` with `snapshot_available=false` for a
    historical, pre-199A version this section found recorded as
    `VersionHistoryItem` metadata but never captured a full state for
    (never fabricated/backfilled -- see Task 2/17/18 of this section's
    own spec).

Applying this migration to a real Postgres database has zero effect on
current app behavior: nothing in `app/repositories/factory.py` reads
Postgres at all until `PERSISTENCE_BACKEND=postgres` is explicitly set,
and even then, no route/service writes to these two new tables until
`RevisionLineageService`'s call sites are reached by real generation/
regeneration traffic -- this migration only creates empty tables.

Column-by-column notes:
  * `itinerary_branches.head_revision_id`/`base_revision_id` are `TEXT
    NULLABLE` with deliberately NO foreign-key constraint (documented in
    `app/db/models.py`'s `ItineraryBranchRow` docstring too) -- a branch
    is always inserted before its first revision exists (head starts
    `NULL`, then advances via `UPDATE` once that revision is created),
    so the ordering is already safe at the application level without a
    circular FK; omitting it also means a future cleanup path could
    remove a revision without that being blocked by the branch row that
    still names it.
  * `itinerary_revisions.parent_revision_id` IS a real, self-referential
    foreign key (`ON DELETE SET NULL` -- removing an ancestor should
    never cascade-delete its descendants), since both the child and
    parent are always genuine, already-existing rows in the SAME table
    by the time a new revision is inserted.
  * `itinerary_revisions.branch_id`/`trip_id` use `ON DELETE CASCADE`
    (matching every other trip-scoped table's own convention since Step
    183C/186F) -- a revision is meaningless without its trip/branch.
  * A UNIQUE constraint on `itinerary_revisions(branch_id, version_label)`
    enforces Task 29's idempotency requirement (no duplicate revision for
    the same real version) at the database level, not just in
    `RevisionLineageService`'s own pre-check.
  * A partial UNIQUE index on `itinerary_branches(trip_id)` WHERE
    `is_default = true` enforces "at most one default branch per trip"
    at the database level too.
  * `feedback_event_id`/`version_history_item_id` on `itinerary_revisions`
    are plain `TEXT NULLABLE`, no FK -- both live inside
    `planning_states.state`'s JSONB blob (`FeedbackEvent`/
    `VersionHistoryItem` are not their own tables), so there is no real
    table row to reference.

Deliberately excludes: any change to `generation_jobs`/`planning_states`/
`trips`/`users` (untouched by this migration); any snapshot storage
mechanism other than one JSONB column per revision (Task 7's explicit
"avoid the recursive-snapshot anti-pattern" -- a revision's `snapshot`
never itself contains a nested revision/branch collection).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7cdda8ff3506'
down_revision: Union[str, Sequence[str], None] = '6f678d2b3ed9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "itinerary_branches",
        sa.Column("branch_id", sa.Text(), nullable=False),
        sa.Column("trip_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False, server_default="Main"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("base_revision_id", sa.Text(), nullable=True),
        sa.Column("head_revision_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("branch_id"),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.trip_id"],
            name="fk_itinerary_branches_trip_id_trips",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_itinerary_branches_trip_id", "itinerary_branches", ["trip_id"]
    )
    op.create_index(
        "uq_itinerary_branches_default_per_trip",
        "itinerary_branches",
        ["trip_id"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )

    op.create_table(
        "itinerary_revisions",
        sa.Column("revision_id", sa.Text(), nullable=False),
        sa.Column("trip_id", sa.Text(), nullable=False),
        sa.Column("branch_id", sa.Text(), nullable=False),
        sa.Column("parent_revision_id", sa.Text(), nullable=True),
        sa.Column("version_label", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("feedback_event_id", sa.Text(), nullable=True),
        sa.Column("version_history_item_id", sa.Text(), nullable=True),
        sa.Column("snapshot_available", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.PrimaryKeyConstraint("revision_id"),
        sa.ForeignKeyConstraint(
            ["trip_id"],
            ["trips.trip_id"],
            name="fk_itinerary_revisions_trip_id_trips",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["itinerary_branches.branch_id"],
            name="fk_itinerary_revisions_branch_id_branches",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["itinerary_revisions.revision_id"],
            name="fk_itinerary_revisions_parent_revision_id_revisions",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "branch_id",
            "version_label",
            name="uq_itinerary_revisions_branch_id_version_label",
        ),
    )
    op.create_index(
        "ix_itinerary_revisions_trip_id", "itinerary_revisions", ["trip_id"]
    )
    op.create_index(
        "ix_itinerary_revisions_branch_id", "itinerary_revisions", ["branch_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_itinerary_revisions_branch_id", table_name="itinerary_revisions")
    op.drop_index("ix_itinerary_revisions_trip_id", table_name="itinerary_revisions")
    op.drop_table("itinerary_revisions")

    op.drop_index("uq_itinerary_branches_default_per_trip", table_name="itinerary_branches")
    op.drop_index("ix_itinerary_branches_trip_id", table_name="itinerary_branches")
    op.drop_table("itinerary_branches")
