from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.db.models import ItineraryBranchRow, PlanningStateRow
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.repositories.errors import UNSET, BranchHeadConflictError, ConcurrentStateUpdateError
from app.repositories.postgres_itinerary_lineage_repository import PostgresItineraryLineageRepository
from app.repositories.postgres_job_repository import PostgresJobRepository
from app.repositories.postgres_planning_state_repository import PostgresPlanningStateRepository

# Section 200C: statement shape + stale-writer semantics of the compare-and-set writes, against a
# scripted fake session (the same behaviour runs against real PostgreSQL in the gated suite).


class _Result:
    def __init__(self, first: Any = None, rowcount: int = 1, scalar: Any = None) -> None:
        self._first, self.rowcount, self._scalar = first, rowcount, scalar

    def first(self) -> Any:
        return self._first

    def scalar_one(self) -> Any:
        return self._scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self):
        return iter([])


class _Session:
    def __init__(self, results: list[_Result] | None = None) -> None:
        self.results = list(results or [])
        self.statements: list[Any] = []
        self.commits = self.rollbacks = 0
        self.info: dict[str, Any] = {}
        self.rows: dict[Any, Any] = {}

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return self.results.pop(0) if self.results else _Result(first=("x",))

    def get(self, model: type, pk: str, **kwargs: Any) -> Any:
        return self.rows.get((model, pk))

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _state(token: int | None) -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Lisbon, Portugal",
            start_date="2026-10-10",
            end_date="2026-10-11",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        )
    )
    state._lock_version = token
    return state


# -- PlanningState optimistic concurrency ---------------------------------------------------------------


def test_current_writer_updates_with_the_token_in_the_where_clause_and_advances_it() -> None:
    session = _Session([_Result(first=("trip",))])
    repo = PostgresPlanningStateRepository(session_factory=lambda: session)
    state = _state(4)

    repo.save(state)

    sql = _sql(session.statements[0])
    assert sql.startswith("UPDATE planning_states SET")
    assert "lock_version=(planning_states.lock_version + %(lock_version_1)s)" in sql
    assert "WHERE planning_states.trip_id = %(trip_id_1)s AND planning_states.lock_version = %(lock_version_2)s" in sql
    assert session.statements[0].compile(dialect=postgresql.dialect()).params["lock_version_2"] == 4
    assert state._lock_version == 5 and session.commits == 1


def test_stale_writer_is_rejected_writes_nothing_and_keeps_its_token() -> None:
    session = _Session([_Result(first=None)])  # zero rows matched: someone else advanced lock_version
    repo = PostgresPlanningStateRepository(session_factory=lambda: session)
    state = _state(4)

    with pytest.raises(ConcurrentStateUpdateError) as excinfo:
        repo.save(state)

    assert state._lock_version == 4 and session.commits == 0 and session.rollbacks == 1
    assert "lock_version" not in str(excinfo.value) and "UPDATE" not in str(excinfo.value)


def test_a_token_less_object_may_only_create_a_new_row_never_overwrite_one() -> None:
    session = _Session([_Result(), _Result(first=None)])  # ensure-trip, then the state INSERT finds a row
    repo = PostgresPlanningStateRepository(session_factory=lambda: session)
    state = _state(None)

    with pytest.raises(ConcurrentStateUpdateError):
        repo.save(state)

    insert_sql = _sql(session.statements[1])
    assert "ON CONFLICT (trip_id) DO NOTHING" in insert_sql and "DO UPDATE" not in insert_sql
    assert state._lock_version is None


def test_first_insert_starts_the_token_at_zero() -> None:
    session = _Session([_Result(), _Result(first=("trip",))])
    state = _state(None)
    PostgresPlanningStateRepository(session_factory=lambda: session).save(state)
    assert state._lock_version == 0


def test_inside_a_unit_of_work_the_token_advances_immediately_and_is_restored_on_rollback() -> None:
    from app.db.transactions import run_rollback_hooks

    session = _Session([_Result(first=("trip",))])
    repo = PostgresPlanningStateRepository(session=session)  # bound: no commit of its own
    state = _state(7)

    repo.save(state)
    assert state._lock_version == 8 and session.commits == 0

    run_rollback_hooks(session)  # what the unit of work does when the transaction rolls back
    assert state._lock_version == 7


def test_reads_stamp_the_row_token_on_the_loaded_state() -> None:
    template = _state(None)
    row = PlanningStateRow(
        trip_id=template.trip_id,
        planning_state_id=template.planning_state_id,
        current_version="v1",
        pipeline_status="draft",
        state=template.model_dump(mode="json"),
        lock_version=11,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session = _Session()
    session.rows[(PlanningStateRow, template.trip_id)] = row
    loaded = PostgresPlanningStateRepository(session_factory=lambda: session).get_by_trip_id(template.trip_id)
    assert loaded is not None and loaded._lock_version == 11
    assert "lock_version" not in loaded.model_dump()  # never serialized/API-visible


def test_for_update_takes_a_single_row_lock() -> None:
    session = _Session([_Result(scalar=None)])
    PostgresPlanningStateRepository(session_factory=lambda: session).get_by_trip_id("t", for_update=True)
    assert _sql(session.statements[0]).rstrip().endswith("FOR UPDATE")


def test_the_token_survives_a_deep_copy_but_not_a_snapshot_round_trip() -> None:
    state = _state(3)
    assert state.model_copy(deep=True)._lock_version == 3
    assert PlanningState.model_validate(state.model_dump(mode="json"))._lock_version is None


# -- branch head compare-and-set -----------------------------------------------------------------------------


def test_branch_head_cas_matches_the_expected_head_including_a_null_head() -> None:
    session = _Session([_Result(rowcount=1), _Result(scalar=_branch_row("b1", "rev_2"))])
    repo = PostgresItineraryLineageRepository(session_factory=lambda: session)

    branch = repo.update_branch_head("b1", "rev_2", expected_head_revision_id=None)

    sql = _sql(session.statements[0])
    assert sql.startswith("UPDATE itinerary_branches SET head_revision_id")
    assert "head_revision_id IS NOT DISTINCT FROM" in sql
    assert branch is not None and branch.head_revision_id == "rev_2"


def test_stale_branch_head_writer_is_rejected() -> None:
    session = _Session([_Result(rowcount=0)])
    session.rows[(ItineraryBranchRow, "b1")] = _branch_row("b1", "rev_9")  # exists, but head moved
    repo = PostgresItineraryLineageRepository(session_factory=lambda: session)
    with pytest.raises(BranchHeadConflictError):
        repo.update_branch_head("b1", "rev_2", expected_head_revision_id="rev_1")
    assert session.commits == 0


def test_cas_on_an_unknown_branch_reports_none_not_a_conflict() -> None:
    session = _Session([_Result(rowcount=0)])
    repo = PostgresItineraryLineageRepository(session_factory=lambda: session)
    assert repo.update_branch_head("nope", "rev", expected_head_revision_id=None) is None


def test_branch_head_conflict_is_a_concurrent_update_error() -> None:
    assert issubclass(BranchHeadConflictError, ConcurrentStateUpdateError)
    assert UNSET is not None


def _branch_row(branch_id: str, head: str | None) -> ItineraryBranchRow:
    return ItineraryBranchRow(
        branch_id=branch_id,
        trip_id="t",
        display_name="Main",
        is_default=True,
        base_revision_id=None,
        head_revision_id=head,
        created_at=datetime.now(timezone.utc),
    )


# -- job lifecycle statements ------------------------------------------------------------------------------------


def test_claim_is_one_conditional_update_that_cannot_take_a_valid_lease() -> None:
    session = _Session([_Result(scalar=None)])
    job = PostgresJobRepository(session_factory=lambda: session).claim("job_1", "inst-a", 120)
    assert job is None
    sql = _sql(session.statements[0])
    assert sql.startswith("UPDATE generation_jobs SET")
    assert "generation_jobs.status = %(status_1)s" in sql  # queued ...
    assert "generation_jobs.lease_expires_at < " in sql  # ... or a running job whose lease EXPIRED
    assert "generation_jobs.lease_expires_at IS NULL" in sql
    assert "RETURNING" in sql


def test_heartbeat_predicate_requires_the_current_owner_of_a_running_job() -> None:
    session = _Session([_Result(rowcount=0)])
    assert PostgresJobRepository(session_factory=lambda: session).heartbeat("j", "old-owner", 60) is False
    sql = _sql(session.statements[0])
    assert "generation_jobs.lease_owner = %(lease_owner_1)s" in sql and "generation_jobs.status = " in sql


def test_recovery_only_matches_expired_running_and_stale_queued_jobs() -> None:
    session = _Session()
    PostgresJobRepository(session_factory=lambda: session).recover_expired_jobs(
        stale_queued_before=datetime.now(timezone.utc)
    )
    sql = _sql(session.statements[0])
    assert "generation_jobs.lease_expires_at < " in sql and "generation_jobs.created_at < " in sql
    assert "SET status=%(status)s" in sql and "lease_owner=%(lease_owner)s" in sql
