from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import UserRow
from app.models.user import UserRecord
from app.repositories.postgres_user_repository import PostgresUserRepository
from app.repositories.protocols import UserAlreadyExistsError

# Unit tests for PostgresUserRepository (Step 184C) against a fake
# session -- never a live Postgres/Docker. Mirrors
# test_postgres_trip_repository.py's approach.


class _FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeSession:
    def __init__(
        self,
        get_result: UserRow | None = None,
        execute_result: UserRow | None = None,
        raise_on_commit: Exception | None = None,
    ) -> None:
        self.get_result = get_result
        self.execute_result = execute_result
        self.raise_on_commit = raise_on_commit
        self.added: list[object] = []
        self.executed_statements: list[object] = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def add(self, row: object) -> None:
        self.added.append(row)

    def get(self, model: type, pk: str) -> UserRow | None:
        assert model is UserRow
        return self.get_result

    def execute(self, stmt: object) -> _FakeResult:
        self.executed_statements.append(stmt)
        return _FakeResult(self.execute_result)

    def commit(self) -> None:
        if self.raise_on_commit is not None:
            raise self.raise_on_commit
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def _user(email: str = "user@example.com", password_hash: str = "hash") -> UserRecord:
    return UserRecord(email=email, password_hash=password_hash)


def test_create_user_adds_row_and_commits() -> None:
    session = _FakeSession()
    repo = PostgresUserRepository(session_factory=lambda: session)
    user = _user()

    created = repo.create_user(user)

    assert created is user
    assert session.committed is True
    assert len(session.added) == 1
    added_row = session.added[0]
    assert added_row.user_id == user.user_id
    assert added_row.email == "user@example.com"
    assert added_row.password_hash == "hash"


def test_create_user_raises_user_already_exists_on_integrity_error() -> None:
    integrity_error = IntegrityError("INSERT ...", {}, Exception("duplicate key"))
    session = _FakeSession(raise_on_commit=integrity_error)
    repo = PostgresUserRepository(session_factory=lambda: session)

    with pytest.raises(UserAlreadyExistsError) as exc_info:
        repo.create_user(_user(email="user@example.com"))

    assert exc_info.value.email == "user@example.com"
    assert session.rolled_back is True


def test_get_by_user_id_returns_none_when_missing() -> None:
    session = _FakeSession(get_result=None)
    repo = PostgresUserRepository(session_factory=lambda: session)

    assert repo.get_by_user_id("does_not_exist") is None


def test_get_by_user_id_returns_record_when_present() -> None:
    now = datetime.now(timezone.utc)
    row = UserRow(
        user_id="user_abc", email="user@example.com", password_hash="hash", created_at=now, updated_at=now
    )
    session = _FakeSession(get_result=row)
    repo = PostgresUserRepository(session_factory=lambda: session)

    record = repo.get_by_user_id("user_abc")

    assert record == UserRecord(
        user_id="user_abc", email="user@example.com", password_hash="hash", created_at=now, updated_at=now
    )


def test_get_by_email_returns_none_when_missing() -> None:
    session = _FakeSession(execute_result=None)
    repo = PostgresUserRepository(session_factory=lambda: session)

    assert repo.get_by_email("nobody@example.com") is None


def test_get_by_email_returns_record_when_present() -> None:
    now = datetime.now(timezone.utc)
    row = UserRow(
        user_id="user_abc", email="user@example.com", password_hash="hash", created_at=now, updated_at=now
    )
    session = _FakeSession(execute_result=row)
    repo = PostgresUserRepository(session_factory=lambda: session)

    record = repo.get_by_email("USER@Example.com")

    assert record is not None
    assert record.user_id == "user_abc"


def test_get_by_email_queries_with_normalized_email() -> None:
    from sqlalchemy.dialects import postgresql

    session = _FakeSession(execute_result=None)
    repo = PostgresUserRepository(session_factory=lambda: session)

    repo.get_by_email("  USER@Example.com  ")

    assert len(session.executed_statements) == 1
    compiled = session.executed_statements[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    assert "user@example.com" in str(compiled)
    assert "USER@Example.com" not in str(compiled)
