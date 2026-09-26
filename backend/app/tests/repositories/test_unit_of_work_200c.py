from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db import transactions
from app.db.transactions import (
    MAX_TRANSIENT_ATTEMPTS,
    TRANSIENT_SQLSTATES,
    add_rollback_hook,
    is_transient_concurrency_error,
    run_with_transient_retry,
)
from app.repositories import unit_of_work as uow_module
from app.repositories.errors import ConcurrentStateUpdateError
from app.repositories.unit_of_work import postgres_unit_of_work, run_atomic

# Section 200C: unit-of-work / session ownership / retry policy, with a fake session (no database).


class _Session:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.info: dict[str, Any] = {}

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *exc: object) -> bool:
        self.closed = True
        return False

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _factory(session: _Session):
    return lambda: session


def test_unit_of_work_commits_exactly_once_and_closes_the_session() -> None:
    session = _Session()
    with postgres_unit_of_work(_factory(session)) as uow:
        assert uow.session is session and uow.atomic
    assert (session.commits, session.rollbacks, session.closed) == (1, 0, True)


def test_unit_of_work_rolls_back_and_closes_on_error_and_never_commits() -> None:
    session = _Session()
    with pytest.raises(RuntimeError):
        with postgres_unit_of_work(_factory(session)):
            raise RuntimeError("boom")
    assert (session.commits, session.rollbacks, session.closed) == (0, 1, True)


def test_repositories_of_one_unit_of_work_share_its_single_session_and_never_commit() -> None:
    session = _Session()
    with postgres_unit_of_work(_factory(session)) as uow:
        for repo in (uow.planning_states, uow.trips, uow.lineage, uow.jobs):
            assert repo._session is session
    assert session.commits == 1  # only the unit of work itself committed


def test_rollback_hooks_run_on_rollback_and_are_dropped_on_commit() -> None:
    restored: list[str] = []
    session = _Session()
    with pytest.raises(RuntimeError):
        with postgres_unit_of_work(_factory(session)) as uow:
            add_rollback_hook(uow.session, lambda: restored.append("token"))
            raise RuntimeError
    assert restored == ["token"]

    committed = _Session()
    with postgres_unit_of_work(_factory(committed)) as uow:
        add_rollback_hook(uow.session, lambda: restored.append("never"))
    assert restored == ["token"] and committed.info == {}


def test_session_scope_standalone_commits_and_closes_bound_does_neither() -> None:
    standalone = _Session()
    with transactions.session_scope(_factory(standalone), None):
        pass
    assert (standalone.commits, standalone.closed) == (1, True)

    bound = _Session()
    with transactions.session_scope(None, bound):  # type: ignore[arg-type]
        pass
    assert (bound.commits, bound.rollbacks, bound.closed) == (0, 0, False)


def test_session_scope_rolls_back_a_failed_standalone_operation() -> None:
    session = _Session()
    with pytest.raises(ValueError):
        with transactions.session_scope(_factory(session), None):
            raise ValueError
    assert (session.commits, session.rollbacks, session.closed) == (0, 1, True)


# -- retry policy -----------------------------------------------------------------------------------------


class _Orig(Exception):
    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate


def _op_error(sqlstate: str) -> OperationalError:
    return OperationalError("stmt", {}, _Orig(sqlstate))


def test_only_serialization_failures_and_deadlocks_are_transient() -> None:
    assert TRANSIENT_SQLSTATES == {"40001", "40P01"}
    assert is_transient_concurrency_error(_op_error("40001"))
    assert is_transient_concurrency_error(_op_error("40P01"))
    assert not is_transient_concurrency_error(_op_error("08006"))  # connection failure
    assert not is_transient_concurrency_error(_op_error("23505"))  # unique violation
    assert not is_transient_concurrency_error(ValueError("x"))


def test_a_transient_error_is_retried_a_bounded_number_of_times(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transactions.time, "sleep", lambda s: None)
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise _op_error("40P01")
        return "ok"

    assert run_with_transient_retry(flaky) == "ok" and len(calls) == 3

    always = []

    def never_ok() -> None:
        always.append(1)
        raise _op_error("40001")

    with pytest.raises(OperationalError):
        run_with_transient_retry(never_ok)
    assert len(always) == MAX_TRANSIENT_ATTEMPTS == 3


@pytest.mark.parametrize(
    "error",
    [
        ConcurrentStateUpdateError("trip"),  # a business conflict: caller must reload, never blind-retry
        ValueError("validation"),
        _op_error("08006"),  # connection loss
        IntegrityError("stmt", {}, _Orig("23505")),
    ],
)
def test_business_and_non_transient_errors_are_never_retried(error: Exception) -> None:
    calls: list[int] = []

    def op() -> None:
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        run_with_transient_retry(op)
    assert calls == [1]


def test_run_atomic_reruns_the_whole_unit_of_work_for_a_deadlock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transactions.time, "sleep", lambda s: None)
    sessions: list[_Session] = []

    def fake_uow():
        session = _Session()
        sessions.append(session)
        return postgres_unit_of_work(_factory(session))

    monkeypatch.setattr(uow_module, "unit_of_work", fake_uow)
    attempts: list[int] = []

    def operation(uow) -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise _op_error("40P01")
        return "done"

    assert run_atomic(operation) == "done"
    assert [s.commits for s in sessions] == [0, 1] and [s.rollbacks for s in sessions] == [1, 0]


def test_local_json_unit_of_work_is_explicitly_not_atomic() -> None:
    with uow_module.local_json_unit_of_work() as uow:
        assert uow.session is None and uow.atomic is False
