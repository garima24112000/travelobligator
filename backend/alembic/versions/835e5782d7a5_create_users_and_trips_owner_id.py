"""create_users_and_trips_owner_id

Revision ID: 835e5782d7a5
Revises: 2fe86f95d81e
Create Date: 2026-09-11 23:09:31.369519

Step 184C: the second Postgres schema migration for the opt-in Postgres
persistence backend (`Settings.persistence_backend`, still `"local_json"`
by default -- see docs/14_backend_architecture.md section 109). Nothing
in `app/api/routes/trips.py` reads or writes `owner_id` yet, and no
`/trips/*` route requires a session -- that enforcement is Step 184D's
job. Applying this migration to a real Postgres database has zero effect
on current app behavior.

Mirrors `app.repositories.user_repository.UserRepository`'s local-JSON
`"users"` collection exactly (`app.models.user.UserRecord`) -- plus a
single nullable `owner_id` column on the existing `trips` table (Step
183C). Deliberately excludes: any change to `planning_states` (ownership
lives on `trips` only -- `planning_states.trip_id` already has a 1:1 FK
to `trips.trip_id`, so ownership is derivable by joining through `trips`
without duplicating an `owner_id` there too); a `sessions` table (this
app's sessions are stateless signed cookies, see
`app/auth/sessions.py` -- there is no server-side session state to
persist); any role/admin/permission column (out of scope for this
section); and `provider_cache` (stays SQLite, independent, see
`app/storage/provider_cache_store.py`).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '835e5782d7a5'
down_revision: Union[str, Sequence[str], None] = '2fe86f95d81e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "users",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    # Nullable for backward compatibility -- every `trips` row that
    # already exists (from Section 183's own live verification, or any
    # local development database) has no owner, and this migration must
    # not fail or invalidate those rows. `ON DELETE SET NULL` (not
    # CASCADE): deleting a user must never cascade-delete their trips'
    # planning data.
    op.add_column("trips", sa.Column("owner_id", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_trips_owner_id_users",
        "trips",
        "users",
        ["owner_id"],
        ["user_id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_trips_owner_id", "trips", ["owner_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_trips_owner_id", table_name="trips")
    op.drop_constraint("fk_trips_owner_id_users", "trips", type_="foreignkey")
    op.drop_column("trips", "owner_id")

    op.drop_table("users")
