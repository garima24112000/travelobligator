from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.db.models import PlanningStateRow, TripRow
from app.models.planning_state import PipelineStatus, PlanningState, TravelGroupType, TripRequest
from app.repositories.postgres_planning_state_repository import PostgresPlanningStateRepository

# Unit tests for PostgresPlanningStateRepository (Step 183D) against a
# fake session -- never a live Postgres/Docker. Mirrors
# test_postgres_trip_repository.py's approach: statement shape is
# verified by compiling the built INSERT constructs (pure string
# generation), and read-path behavior is verified against real (but
# never persisted) PlanningStateRow instances.


class _FakeSession:
    def __init__(self, get_result: PlanningStateRow | None = None) -> None:
        self.get_result = get_result
        self.executed_statements: list[object] = []
        self.committed = False

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, model: type, pk: str) -> object | None:
        return self.get_result if model is PlanningStateRow else None

    def execute(self, stmt: object) -> None:
        self.executed_statements.append(stmt)

    def commit(self) -> None:
        self.committed = True


def _compiled_sql(stmt: object) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _planning_state() -> PlanningState:
    trip_request = TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-11",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    return PlanningState(trip_request=trip_request)


def test_save_never_mutates_the_passed_in_planning_state() -> None:
    planning_state = _planning_state()
    before = planning_state.model_dump(mode="json")
    session = _FakeSession()
    repo = PostgresPlanningStateRepository(session_factory=lambda: session)

    result = repo.save(planning_state)

    assert result is planning_state
    assert planning_state.model_dump(mode="json") == before


def test_save_executes_ensure_trip_then_upsert_planning_state() -> None:
    planning_state = _planning_state()
    session = _FakeSession()
    repo = PostgresPlanningStateRepository(session_factory=lambda: session)

    repo.save(planning_state)

    assert session.committed is True
    assert len(session.executed_statements) == 2

    trip_sql = _compiled_sql(session.executed_statements[0])
    assert "INSERT INTO trips" in trip_sql
    assert "ON CONFLICT" in trip_sql
    assert "DO NOTHING" in trip_sql

    # The `state` column's JSONB value can't be rendered as a literal
    # without a real DBAPI (no live connection needed to compile it as a
    # parameterized statement, though) -- so this checks the SQL shape
    # without literal_binds, and the actual bound values via `.params`.
    state_stmt = session.executed_statements[1]
    state_sql = str(state_stmt.compile(dialect=postgresql.dialect()))
    assert "INSERT INTO planning_states" in state_sql
    assert "ON CONFLICT" in state_sql
    assert "DO UPDATE SET" in state_sql

    params = state_stmt.compile(dialect=postgresql.dialect()).params
    assert params["trip_id"] == planning_state.trip_id
    assert params["planning_state_id"] == planning_state.planning_state_id


def test_save_lifts_metadata_fields_into_columns() -> None:
    planning_state = _planning_state()
    planning_state.metadata.current_version = "v3"
    planning_state.metadata.pipeline_status = PipelineStatus.VALIDATED
    session = _FakeSession()
    repo = PostgresPlanningStateRepository(session_factory=lambda: session)

    repo.save(planning_state)

    state_stmt = session.executed_statements[1]
    compiled = state_stmt.compile(dialect=postgresql.dialect())
    params = compiled.params

    assert params["planning_state_id"] == planning_state.planning_state_id
    assert params["current_version"] == "v3"
    assert params["pipeline_status"] == "validated"
    assert params["state"] == planning_state.model_dump(mode="json")


def test_get_by_trip_id_returns_none_when_row_missing() -> None:
    session = _FakeSession(get_result=None)
    repo = PostgresPlanningStateRepository(session_factory=lambda: session)

    assert repo.get_by_trip_id("does_not_exist") is None


def test_get_by_trip_id_validates_state_through_pydantic() -> None:
    planning_state = _planning_state()
    row = PlanningStateRow(
        trip_id=planning_state.trip_id,
        planning_state_id=planning_state.planning_state_id,
        current_version=planning_state.metadata.current_version,
        pipeline_status=planning_state.metadata.pipeline_status.value,
        state=planning_state.model_dump(mode="json"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session = _FakeSession(get_result=row)
    repo = PostgresPlanningStateRepository(session_factory=lambda: session)

    reloaded = repo.get_by_trip_id(planning_state.trip_id)

    assert reloaded is not None
    assert reloaded.trip_id == planning_state.trip_id
    assert reloaded.trip_request.primary_destination == "Lisbon, Portugal"
    # Round-tripping through JSON must not fabricate any generated content
    # that was never set -- matches test_persistence.py's own local-JSON
    # equivalent assertion.
    assert reloaded.destination_context is None
    assert reloaded.experience_plan is None


def test_get_by_trip_id_raises_when_state_json_is_invalid() -> None:
    """Invalid/corrupt JSONB content must surface as a real validation
    error, never be silently discarded or fabricated into a default
    PlanningState."""
    row = PlanningStateRow(
        trip_id="trip_bad",
        planning_state_id="does_not_matter",
        current_version="v1",
        pipeline_status="draft",
        state={"this": "is not a valid PlanningState payload"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session = _FakeSession(get_result=row)
    repo = PostgresPlanningStateRepository(session_factory=lambda: session)

    with pytest.raises(ValidationError):
        repo.get_by_trip_id("trip_bad")
