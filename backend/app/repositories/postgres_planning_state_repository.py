"""Opt-in Postgres-backed planning state repository (Step 183D; Section 200C
optimistic concurrency + transaction sharing).

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

Section 200C -- optimistic concurrency. Every read stamps the returned
`PlanningState` with the row's `lock_version` (a private attribute, never
serialized); every write is
`UPDATE ... WHERE trip_id = :id AND lock_version = :token` +
`lock_version = lock_version + 1`. Zero rows updated means another writer
committed first: `ConcurrentStateUpdateError`, and NOTHING is written. An object
with no token (never loaded/saved) may only INSERT a brand-new row; overwriting
an existing row without having read it is refused, so a blind last-write-wins
save is no longer possible.

Session ownership: constructed without a session, each method opens/commits/
closes its own short-lived session (old behaviour). Constructed with a bound
`session` (by `app.repositories.unit_of_work`), every method uses that session and
NEVER commits -- the outer unit of work commits once.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import PlanningStateRow, TripRow
from app.db.session import get_session_factory
from app.db.transactions import add_rollback_hook, record_conflict, session_scope
from app.models.planning_state import PlanningState
from app.repositories.errors import ConcurrentStateUpdateError


class PostgresPlanningStateRepository:
    """Postgres-backed equivalent of `PlanningStateRepository`."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        session: Session | None = None,
    ) -> None:
        self._session = session
        self._session_factory = session_factory or (
            None if session is not None else get_session_factory()
        )

    def save(self, planning_state: PlanningState) -> PlanningState:
        """Compare-and-set write; returns `planning_state` (never mutating its content).

        Its private concurrency token advances to the new `lock_version` only once the
        write is durable (standalone: after commit; inside a unit of work: immediately,
        with a rollback hook that restores it if the transaction rolls back).

        `planning_states.trip_id` has a real foreign key to `trips.trip_id`; to keep the
        old decoupled behaviour a first INSERT ensures a minimal `trips` row exists via
        `ON CONFLICT DO NOTHING` (never overwriting a real trip's `status`).
        """
        now = datetime.now(timezone.utc)
        state_json = planning_state.model_dump(mode="json")
        token = planning_state._lock_version
        bound = self._session is not None

        with session_scope(self._session_factory, self._session) as session:
            if token is None:
                ensure_trip_stmt = pg_insert(TripRow.__table__).values(
                    trip_id=planning_state.trip_id,
                    status="draft",
                    created_at=now,
                    updated_at=now,
                )
                session.execute(ensure_trip_stmt.on_conflict_do_nothing(index_elements=["trip_id"]))

                insert_stmt = pg_insert(PlanningStateRow.__table__).values(
                    trip_id=planning_state.trip_id,
                    planning_state_id=planning_state.planning_state_id,
                    current_version=planning_state.metadata.current_version,
                    pipeline_status=planning_state.metadata.pipeline_status.value,
                    state=state_json,
                    lock_version=0,
                    created_at=now,
                    updated_at=now,
                )
                # RETURNING (not rowcount: INSERT..ON CONFLICT reports -1): a row comes back only
                # when this call actually created the state.
                written = session.execute(
                    insert_stmt.on_conflict_do_nothing(index_elements=["trip_id"]).returning(
                        PlanningStateRow.trip_id
                    )
                ).first()
                new_token = 0
            else:
                written = session.execute(
                    update(PlanningStateRow)
                    .where(
                        PlanningStateRow.trip_id == planning_state.trip_id,
                        PlanningStateRow.lock_version == token,
                    )
                    .values(
                        planning_state_id=planning_state.planning_state_id,
                        current_version=planning_state.metadata.current_version,
                        pipeline_status=planning_state.metadata.pipeline_status.value,
                        state=state_json,
                        lock_version=PlanningStateRow.lock_version + 1,
                        updated_at=now,
                        # created_at deliberately omitted -- set once, on first insert.
                    )
                    .returning(PlanningStateRow.trip_id)
                ).first()
                new_token = token + 1

            if written is None:
                # Stale writer (or an unguarded overwrite of an existing row): nothing written.
                record_conflict("state")
                raise ConcurrentStateUpdateError(planning_state.trip_id)

            if bound:
                planning_state._lock_version = new_token
                add_rollback_hook(session, lambda: setattr(planning_state, "_lock_version", token))

        if not bound:
            planning_state._lock_version = new_token
        return planning_state

    def get_by_trip_id(self, trip_id: str, *, for_update: bool = False) -> PlanningState | None:
        """Loads the state stamped with its `lock_version`. `for_update=True` takes a row
        lock (`SELECT ... FOR UPDATE`, one row, held only until the surrounding
        transaction ends) so a guard-then-write sequence can be serialised."""
        with session_scope(self._session_factory, self._session) as session:
            if for_update:
                row = session.execute(
                    select(PlanningStateRow)
                    .where(PlanningStateRow.trip_id == trip_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).scalar_one_or_none()
            else:
                row = session.get(PlanningStateRow, trip_id, populate_existing=True)
            if row is None:
                return None
            # Validated back through Pydantic, exactly like PlanningStateRepository does for
            # its JSON file -- never manually reconstructed field by field.
            state = PlanningState.model_validate(row.state)
            state._lock_version = int(row.lock_version or 0)
            return state
