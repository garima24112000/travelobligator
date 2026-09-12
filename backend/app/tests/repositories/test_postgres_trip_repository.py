from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.db.models import TripRow
from app.repositories.postgres_trip_repository import PostgresTripRepository
from app.repositories.trip_repository import TripRecord

# Unit tests for PostgresTripRepository (Step 183D) against a fake
# session -- never a live Postgres/Docker. `create`/`update_status`'s
# actual SQL is verified by compiling the statements they build (pure
# string generation, no connection needed); `get`/`update_status`'s
# row-to-TripRecord mapping is verified against real (but never
# persisted/queried) TripRow instances constructed directly in memory.


class _FakeScalars:
    def __init__(self, rows: list[TripRow]) -> None:
        self._rows = rows

    def all(self) -> list[TripRow]:
        return self._rows


class _FakeExecuteResult:
    def __init__(self, rows: list[TripRow]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(
        self, get_result: TripRow | None = None, execute_rows: list[TripRow] | None = None
    ) -> None:
        self.get_result = get_result
        self.execute_rows = execute_rows or []
        self.executed_statements: list[object] = []
        self.committed = False
        self.refreshed: list[object] = []

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, model: type, pk: str) -> TripRow | None:
        assert model is TripRow
        return self.get_result

    def execute(self, stmt: object) -> _FakeExecuteResult:
        self.executed_statements.append(stmt)
        return _FakeExecuteResult(self.execute_rows)

    def commit(self) -> None:
        self.committed = True

    def refresh(self, row: object) -> None:
        self.refreshed.append(row)


def _compiled_sql(stmt: object) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_create_builds_an_upsert_into_trips() -> None:
    session = _FakeSession()
    repo = PostgresTripRepository(session_factory=lambda: session)

    record = repo.create("trip_abc")

    assert isinstance(record, TripRecord)
    assert record.trip_id == "trip_abc"
    assert record.status == "draft"
    assert record.owner_id is None
    assert session.committed is True
    assert len(session.executed_statements) == 1

    sql = _compiled_sql(session.executed_statements[0])
    assert "INSERT INTO trips" in sql
    assert "ON CONFLICT" in sql
    assert "DO UPDATE SET" in sql
    assert "trip_abc" in sql


def test_create_persists_owner_id_in_the_upsert() -> None:
    session = _FakeSession()
    repo = PostgresTripRepository(session_factory=lambda: session)

    record = repo.create("trip_abc", owner_id="user_owner_123")

    assert record.owner_id == "user_owner_123"
    params = session.executed_statements[0].compile(dialect=postgresql.dialect()).params
    assert params["owner_id"] == "user_owner_123"


def test_get_returns_none_when_row_missing() -> None:
    session = _FakeSession(get_result=None)
    repo = PostgresTripRepository(session_factory=lambda: session)

    assert repo.get("does_not_exist") is None


def test_get_returns_trip_record_when_row_present() -> None:
    now = datetime.now(timezone.utc)
    row = TripRow(trip_id="trip_xyz", status="generating", created_at=now, updated_at=now)
    session = _FakeSession(get_result=row)
    repo = PostgresTripRepository(session_factory=lambda: session)

    record = repo.get("trip_xyz")

    assert record == TripRecord(
        trip_id="trip_xyz", status="generating", created_at=now, updated_at=now
    )


def test_get_returns_owner_id_when_present() -> None:
    now = datetime.now(timezone.utc)
    row = TripRow(
        trip_id="trip_xyz",
        status="draft",
        owner_id="user_owner_123",
        created_at=now,
        updated_at=now,
    )
    session = _FakeSession(get_result=row)
    repo = PostgresTripRepository(session_factory=lambda: session)

    record = repo.get("trip_xyz")

    assert record is not None
    assert record.owner_id == "user_owner_123"


def test_update_status_returns_none_when_row_missing() -> None:
    session = _FakeSession(get_result=None)
    repo = PostgresTripRepository(session_factory=lambda: session)

    assert repo.update_status("does_not_exist", "generating") is None
    assert session.committed is False


def test_update_status_mutates_row_and_commits() -> None:
    original_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = TripRow(trip_id="trip_xyz", status="draft", created_at=original_time, updated_at=original_time)
    session = _FakeSession(get_result=row)
    repo = PostgresTripRepository(session_factory=lambda: session)

    record = repo.update_status("trip_xyz", "generating")

    assert record is not None
    assert record.status == "generating"
    assert record.created_at == original_time
    assert record.updated_at > original_time
    assert row.status == "generating"
    assert session.committed is True
    assert session.refreshed == [row]


def test_list_by_owner_id_returns_matching_rows_as_records() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        TripRow(trip_id="trip_a1", status="draft", owner_id="user_a", created_at=now, updated_at=now),
        TripRow(trip_id="trip_a2", status="draft", owner_id="user_a", created_at=now, updated_at=now),
    ]
    session = _FakeSession(execute_rows=rows)
    repo = PostgresTripRepository(session_factory=lambda: session)

    records = repo.list_by_owner_id("user_a")

    assert {record.trip_id for record in records} == {"trip_a1", "trip_a2"}
    assert all(record.owner_id == "user_a" for record in records)


def test_list_by_owner_id_queries_with_the_given_owner_id() -> None:
    session = _FakeSession(execute_rows=[])
    repo = PostgresTripRepository(session_factory=lambda: session)

    repo.list_by_owner_id("user_a")

    assert len(session.executed_statements) == 1
    params = session.executed_statements[0].compile(dialect=postgresql.dialect()).params
    assert params["owner_id_1"] == "user_a"


def test_list_by_owner_id_returns_empty_list_when_no_matches() -> None:
    session = _FakeSession(execute_rows=[])
    repo = PostgresTripRepository(session_factory=lambda: session)

    assert repo.list_by_owner_id("user_a") == []
