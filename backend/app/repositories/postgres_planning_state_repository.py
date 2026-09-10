"""Opt-in Postgres-backed planning state repository (Step 183D).

Only ever constructed when `Settings.persistence_backend == "postgres"`
(see `app/repositories/factory.py`) -- the default `local_json` backend
never imports or constructs this class, so it never opens a database
connection unless an operator has explicitly opted in.

Stores the entire `PlanningState` as one JSONB document per `trip_id`,
exactly like `app.repositories.planning_state_repository.
PlanningStateRepository` stores one JSON document per `trip_id` in
`LocalJsonStore` today -- see `app/repositories/protocols.py`'s
`PlanningStateRepositoryProtocol`. `planning_state_id`/
`metadata.current_version`/`metadata.pipeline_status` are lifted into
their own indexed columns (matching the `planning_states` table from the
Step 183C migration) purely for future queryability; `state` remains the
single source of truth read back on `get_by_trip_id`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import PlanningStateRow, TripRow
from app.db.session import get_session_factory
from app.models.planning_state import PlanningState


class PostgresPlanningStateRepository:
    """Postgres-backed equivalent of `PlanningStateRepository`.

    Opens a short-lived `Session` per method call (via the injected/
    default session factory) rather than caching states in memory --
    unlike the local JSON repository, a real database is the shared
    source of truth across processes.
    """

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def save(self, planning_state: PlanningState) -> PlanningState:
        """Upserts `planning_state` and returns it unchanged (never
        mutates the passed-in object -- matches
        `PlanningStateRepository.save`).

        `planning_states.trip_id` has a real foreign key to `trips.trip_id`
        (Step 183C migration) -- unlike the two local JSON repositories,
        which are completely independent of each other and impose no such
        ordering. To preserve that same decoupled behavior (a caller does
        not have to have called the trip repository's `create()` first),
        this ensures a minimal `trips` row exists via `ON CONFLICT DO
        NOTHING` before writing `planning_states` -- it never overwrites
        an existing trip's real `status`. See
        `test_postgres_planning_state_repository_ensures_trip_row_exists`
        for the behavior this documents.
        """
        now = datetime.now(timezone.utc)
        state_json = planning_state.model_dump(mode="json")

        with self._session_factory() as session:
            ensure_trip_stmt = pg_insert(TripRow.__table__).values(
                trip_id=planning_state.trip_id,
                status="draft",
                created_at=now,
                updated_at=now,
            )
            ensure_trip_stmt = ensure_trip_stmt.on_conflict_do_nothing(
                index_elements=["trip_id"]
            )
            session.execute(ensure_trip_stmt)

            state_stmt = pg_insert(PlanningStateRow.__table__).values(
                trip_id=planning_state.trip_id,
                planning_state_id=planning_state.planning_state_id,
                current_version=planning_state.metadata.current_version,
                pipeline_status=planning_state.metadata.pipeline_status.value,
                state=state_json,
                created_at=now,
                updated_at=now,
            )
            state_stmt = state_stmt.on_conflict_do_update(
                index_elements=["trip_id"],
                set_={
                    "planning_state_id": planning_state.planning_state_id,
                    "current_version": planning_state.metadata.current_version,
                    "pipeline_status": planning_state.metadata.pipeline_status.value,
                    "state": state_json,
                    "updated_at": now,
                    # created_at deliberately omitted from the update set --
                    # it is set once, on first insert, and never changes.
                },
            )
            session.execute(state_stmt)
            session.commit()

        return planning_state

    def get_by_trip_id(self, trip_id: str) -> PlanningState | None:
        with self._session_factory() as session:
            row = session.get(PlanningStateRow, trip_id)
            if row is None:
                return None
            # Validated back through Pydantic, exactly like
            # PlanningStateRepository.get_by_trip_id does for its JSON
            # file -- never manually reconstructed field by field, so
            # unknown/backward-compatible fields still round-trip however
            # PlanningState.model_validate already handles them.
            return PlanningState.model_validate(row.state)
