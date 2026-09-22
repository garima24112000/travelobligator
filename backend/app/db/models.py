"""Minimal SQLAlchemy table metadata for future Postgres repositories
(Step 183C, extended in Step 184C, Step 186F).

Deliberately NOT imported by any route/service/repository directly --
only by the opt-in `Postgres*Repository` classes (constructed only when
`Settings.persistence_backend == "postgres"`, see
`app/repositories/factory.py`) and `alembic/env.py` -- see
`app/db/__init__.py` and `docs/14_backend_architecture.md` section 104.

It does NOT replace `PlanningState` (`app/models/planning_state.py`) --
that Pydantic model remains the single source of truth for validation and
business logic everywhere in this codebase. `PlanningStateRow.state` just
holds that model's `model_dump(mode="json")` output, mirroring exactly
what `PlanningStateRepository.save` already writes to `LocalJsonStore`
today. Likewise `UserRow` does not replace `app.models.user.UserRecord`,
and `GenerationJobRow` does not replace
`app.models.generation_job.GenerationJob`.

Importing this module registers `TripRow`/`PlanningStateRow`/`UserRow`/
`GenerationJobRow` on `Base.metadata` (used by `alembic/env.py` for
future autogenerate diffs) but does NOT create any table and does NOT
open a database connection --
nothing in this codebase calls `Base.metadata.create_all(engine)`; the
actual schema is created via the Alembic migrations in
`backend/alembic/versions/`, which this module's column definitions
mirror (but do not share code with -- migrations stay self-contained by
design, so they remain historically accurate even if this file changes
later).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserRow(Base):
    """Mirrors `app.models.user.UserRecord`. Not read or written by any
    repository unless `Settings.persistence_backend == "postgres"` --
    see `app/repositories/postgres_user_repository.py`."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TripRow(Base):
    """Mirrors `app.repositories.trip_repository.TripRecord`, plus
    `owner_id` (Step 184C -- nullable for backward compatibility with
    rows created before Section 184, and `ON DELETE SET NULL` so deleting
    a user never cascade-deletes their trips' data). Not read or written
    by any repository unless `Settings.persistence_backend == "postgres"`.

    `owner_id` existing here is schema-only in this step -- no route sets
    it on trip creation and no route enforces it yet (Step 184D's job,
    see `docs/14_backend_architecture.md` section 109)."""

    __tablename__ = "trips"
    __table_args__ = (
        Index("ix_trips_status", "status"),
        Index("ix_trips_updated_at", "updated_at"),
        Index("ix_trips_owner_id", "owner_id"),
    )

    trip_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    owner_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PlanningStateRow(Base):
    """Mirrors `app.repositories.planning_state_repository` -- stores the
    entire serialized `PlanningState` as one JSONB document per `trip_id`,
    exactly like `PlanningStateRepository` stores one JSON document per
    `trip_id` in `LocalJsonStore` today. Not read or written by any
    repository yet -- that wiring is Step 183D's job.

    `planning_state_id`/`current_version`/`pipeline_status` are lifted out
    of `state` into their own indexed columns purely for future
    queryability (e.g. "find trips stuck in a given pipeline status"
    without scanning JSONB) -- `state` remains the single source of truth
    for the full plan; these three columns are a read-optimization, never
    an independent copy a future repository would write to separately.
    """

    __tablename__ = "planning_states"
    __table_args__ = (
        Index("ix_planning_states_current_version", "current_version"),
        Index("ix_planning_states_pipeline_status", "pipeline_status"),
    )

    trip_id: Mapped[str] = mapped_column(
        Text, ForeignKey("trips.trip_id", ondelete="CASCADE"), primary_key=True
    )
    planning_state_id: Mapped[str] = mapped_column(Text, nullable=False)
    current_version: Mapped[str] = mapped_column(Text, nullable=False)
    pipeline_status: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GenerationJobRow(Base):
    """Mirrors `app.models.generation_job.GenerationJob` (Step 186F). Not
    read or written by any repository unless
    `Settings.persistence_backend == "postgres"` -- see
    `app/repositories/postgres_job_repository.py`.

    This is job *control* state (identity, ownership, lifecycle, error)
    only -- never a source of travel facts, and never a claim about the
    resulting plan's quality (see `GenerationJob`'s own docstring). No
    secret, password, session token, or API key is ever stored here.

    `owner_id` is `NOT NULL` with `ON DELETE CASCADE`, unlike
    `TripRow.owner_id` (nullable + `SET NULL`, for backward compatibility
    with pre-184C trip rows). `GenerationJob.owner_id` is always a real,
    required value from job-creation time (Step 186C) -- there is no
    legacy-row case to preserve, and a job record has no independent
    value once its owner is gone. `result_version` is `Text`, not an
    integer -- it mirrors `GenerationJob.result_version: str | None`,
    which holds a version *label* like `"v2"`, never a numeric id.
    """

    __tablename__ = "generation_jobs"
    __table_args__ = (
        Index("ix_generation_jobs_trip_id", "trip_id"),
        Index("ix_generation_jobs_owner_id", "owner_id"),
        Index("ix_generation_jobs_status", "status"),
        Index("ix_generation_jobs_trip_id_status", "trip_id", "status"),
    )

    job_id: Mapped[str] = mapped_column(Text, primary_key=True)
    trip_id: Mapped[str] = mapped_column(
        Text, ForeignKey("trips.trip_id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    progress_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_sections: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    # Section 198B: carries the same canonical targeted-regeneration
    # result an async job's sync `/regenerate` counterpart returns
    # (`RegenerateResponseData`) -- see `app/schemas/generation_job.py`.
    # All nullable/defaulted so existing rows (pre-migration, or from a
    # `generate`/legacy `regenerate` job) still read back unchanged.
    previous_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    targeted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    interpretation_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    affected_day_indices: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    preserved_day_indices: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    diff: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    clarification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_possible_experience_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )


class ItineraryBranchRow(Base):
    """Mirrors `app.models.itinerary_lineage.ItineraryBranch` (Section
    199A). Not read or written by any repository unless
    `Settings.persistence_backend == "postgres"` -- see
    `app/repositories/postgres_itinerary_lineage_repository.py`.

    `head_revision_id`/`base_revision_id` semantically reference
    `itinerary_revisions.revision_id`, but deliberately carry NO foreign
    -key constraint (neither here nor in the Alembic migration) -- a
    branch is always inserted before its first revision exists (head
    starts `NULL`, then advances via `UPDATE` once that revision is
    created), so a real FK would only ever constrain an already-safe
    write order; omitting it also means a future cleanup path could
    remove a revision without that being blocked by the branch row that
    still names it. `itinerary_revisions.branch_id`/`trip_id`/
    `parent_revision_id` DO carry real FKs (see `ItineraryRevisionRow`
    below) -- only this one circular direction is intentionally left
    unconstrained.
    """

    __tablename__ = "itinerary_branches"
    __table_args__ = (
        Index("ix_itinerary_branches_trip_id", "trip_id"),
        # Task 29/30: at most one default branch per trip, enforced at
        # the database level too (a partial unique index, since
        # `is_default` is `False` for every future Section 199B fork
        # branch and only ever `True` for the one per-trip default) --
        # not just in `RevisionLineageService.ensure_default_branch`'s
        # own read-check-then-create.
        Index(
            "uq_itinerary_branches_default_per_trip",
            "trip_id",
            unique=True,
            postgresql_where=text("is_default = true"),
        ),
    )

    branch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    trip_id: Mapped[str] = mapped_column(
        Text, ForeignKey("trips.trip_id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="Main")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    base_revision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_revision_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ItineraryRevisionRow(Base):
    """Mirrors `app.models.itinerary_lineage.ItineraryRevision` (Section
    199A). Not read or written by any repository unless
    `Settings.persistence_backend == "postgres"`.

    `snapshot` is `JSONB NULLABLE` -- it stores
    `revision_snapshot_service.serialize_planning_state_snapshot`'s
    output verbatim (the entire `PlanningState`, exactly like
    `PlanningStateRow.state` already stores the live one), `NULL` only
    for a historical, pre-199A version-history entry this section found
    but never captured a full state for (`snapshot_available=False` --
    Task 2/17/18: never fabricated, never backfilled). A UNIQUE
    constraint on `(branch_id, version_label)` enforces Task 29's
    idempotency requirement (no duplicate revision for the same real
    version) at the database level too, not just in
    `RevisionLineageService`'s own pre-check.
    """

    __tablename__ = "itinerary_revisions"
    __table_args__ = (
        Index("ix_itinerary_revisions_trip_id", "trip_id"),
        Index("ix_itinerary_revisions_branch_id", "branch_id"),
        UniqueConstraint(
            "branch_id", "version_label", name="uq_itinerary_revisions_branch_id_version_label"
        ),
    )

    revision_id: Mapped[str] = mapped_column(Text, primary_key=True)
    trip_id: Mapped[str] = mapped_column(
        Text, ForeignKey("trips.trip_id", ondelete="CASCADE"), nullable=False
    )
    branch_id: Mapped[str] = mapped_column(
        Text, ForeignKey("itinerary_branches.branch_id", ondelete="CASCADE"), nullable=False
    )
    # Self-referential FK (ON DELETE SET NULL -- removing an ancestor
    # should never cascade-delete its descendants) -- `None` only for a
    # branch's first-ever revision.
    parent_revision_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("itinerary_revisions.revision_id", ondelete="SET NULL"),
        nullable=True,
    )
    version_label: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feedback_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    version_history_item_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
