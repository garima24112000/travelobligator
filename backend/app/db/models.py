"""Minimal SQLAlchemy table metadata for future Postgres repositories (Step 183C).

Deliberately NOT imported by any route/service/repository -- see
`app/db/__init__.py` and `docs/14_backend_architecture.md` section 104.
This exists only so a future Step 183D repository implementation, and
Alembic's `--autogenerate`, have real ORM class metadata to work against.

It does NOT replace `PlanningState` (`app/models/planning_state.py`) --
that Pydantic model remains the single source of truth for validation and
business logic everywhere in this codebase. `PlanningStateRow.state` just
holds that model's `model_dump(mode="json")` output, mirroring exactly
what `PlanningStateRepository.save` already writes to `LocalJsonStore`
today.

Importing this module registers `TripRow`/`PlanningStateRow` on
`Base.metadata` (used by `alembic/env.py` for future autogenerate diffs)
but does NOT create any table and does NOT open a database connection --
nothing in this codebase calls `Base.metadata.create_all(engine)`; the
actual schema is created via the Alembic migration in
`backend/alembic/versions/`, which this module's column definitions
mirror (but do not share code with -- migrations stay self-contained by
design, so they remain historically accurate even if this file changes
later).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TripRow(Base):
    """Mirrors `app.repositories.trip_repository.TripRecord`. Not read or
    written by any repository yet -- that wiring is Step 183D's job."""

    __tablename__ = "trips"
    __table_args__ = (
        Index("ix_trips_status", "status"),
        Index("ix_trips_updated_at", "updated_at"),
    )

    trip_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
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
