"""Unit of work: several repository operations, ONE database transaction (Section 200C).

    with unit_of_work() as uow:
        uow.planning_states.save(state)        # compare-and-set on lock_version
        uow.lineage.create_revision(revision)
        uow.lineage.update_branch_head(..., expected_head_revision_id=...)
    # committed exactly once here; an exception anywhere rolls EVERYTHING back

PostgreSQL mode: one SQLAlchemy `Session` (one transaction) is shared by repositories
constructed with that bound session. They never commit on their own; leaving the
`with` block commits once, an exception rolls back, and the session is ALWAYS closed
(its connection returns to the pool). In-memory concurrency tokens advanced by a save
inside the block are restored on rollback (rollback hooks), so a failed attempt never
leaves a `PlanningState` object with a token the database does not have.

Local JSON mode (explicit development backend): `unit_of_work()` yields the same
attribute surface over the Local JSON repositories but is NOT transactional -- each
write persists immediately and nothing is rolled back. Production-grade atomicity and
concurrency control require PostgreSQL.

What must never happen inside the block: any provider / AI / Redis / network call. Do
the external work first, build the result, then open the (short) unit of work only to
persist it.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator, TypeVar

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.transactions import (
    MAX_TRANSIENT_ATTEMPTS,
    discard_rollback_hooks,
    record_transaction,
    rollback_cause,
    run_rollback_hooks,
    run_with_transient_retry,
)

T = TypeVar("T")


@dataclass
class UnitOfWork:
    """The repositories of one transaction. `session` is `None` in Local JSON mode."""

    planning_states: Any
    trips: Any
    lineage: Any
    jobs: Any
    session: Session | None = None

    @property
    def atomic(self) -> bool:
        """True only when the operations share one real database transaction."""
        return self.session is not None


@contextmanager
def postgres_unit_of_work(session_factory: sessionmaker[Session] | None = None) -> Iterator[UnitOfWork]:
    from app.db.session import get_session_factory
    from app.repositories.postgres_itinerary_lineage_repository import (
        PostgresItineraryLineageRepository,
    )
    from app.repositories.postgres_job_repository import PostgresJobRepository
    from app.repositories.postgres_planning_state_repository import (
        PostgresPlanningStateRepository,
    )
    from app.repositories.postgres_trip_repository import PostgresTripRepository

    factory = session_factory or get_session_factory()
    started = time.perf_counter()
    with factory() as session:
        try:
            yield UnitOfWork(
                planning_states=PostgresPlanningStateRepository(session=session),
                trips=PostgresTripRepository(session=session),
                lineage=PostgresItineraryLineageRepository(session=session),
                jobs=PostgresJobRepository(session=session),
                session=session,
            )
            session.commit()
            discard_rollback_hooks(session)
        except BaseException as exc:
            session.rollback()
            run_rollback_hooks(session)
            record_transaction("unit_of_work", "rollback", started, rollback_cause(exc))
            raise
    record_transaction("unit_of_work", "commit", started)


@contextmanager
def local_json_unit_of_work() -> Iterator[UnitOfWork]:
    from app.repositories.factory import (
        get_job_repository,
        get_lineage_repository,
        get_planning_state_repository,
        get_trip_repository,
    )

    yield UnitOfWork(
        planning_states=get_planning_state_repository(),
        trips=get_trip_repository(),
        lineage=get_lineage_repository(),
        jobs=get_job_repository(),
        session=None,
    )


def unit_of_work():
    """Context manager for the ACTIVE persistence backend (`PERSISTENCE_BACKEND`)."""
    if get_settings().persistence_backend == "postgres":
        return postgres_unit_of_work()
    return local_json_unit_of_work()


def run_atomic(operation: Callable[[UnitOfWork], T], *, max_attempts: int = MAX_TRANSIENT_ATTEMPTS) -> T:
    """Run `operation(uow)` in one unit of work.

    Retry policy (deliberately narrow -- see `app.db.transactions`): the whole unit of work
    is re-run from scratch ONLY for PostgreSQL serialization failures / deadlocks, at most
    `max_attempts` times. `operation` must therefore be a pure database step (no provider /
    AI / Redis call, no side effect outside the transaction). Business conflicts, validation
    errors and integrity errors propagate on the first occurrence.
    """

    def once() -> T:
        with unit_of_work() as uow:
            return operation(uow)

    return run_with_transient_retry(once, max_attempts=max_attempts)
