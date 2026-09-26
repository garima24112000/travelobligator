"""Transaction helpers for the PostgreSQL repositories (Section 200C).

Isolation: every session uses PostgreSQL's default READ COMMITTED. Correctness for
multi-row transitions comes from (a) ONE short transaction per product operation,
(b) compare-and-set / row-count checks (`planning_states.lock_version`, branch head),
(c) narrow `SELECT ... FOR UPDATE` only where a guard-then-write sequence must be
serialised (branch activation), and (d) database constraints. SERIALIZABLE is
deliberately NOT used.

`session_scope` is the one place a repository decides whether it owns the session:

  * standalone call (no bound session): open a session, commit on success, roll back
    on error, ALWAYS close -- exactly the old per-method behaviour;
  * bound session (inside a unit of work): use it as-is; the OUTER unit of work commits
    or rolls back once. A repository never commits inside someone else's transaction.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.core import ops_events
from app.core.metrics import registry

logger = logging.getLogger("app.persistence")

T = TypeVar("T")

# Retry policy (Task 22). ONLY these database-level, transient, retry-safe conditions are
# ever retried, and only by `run_with_transient_retry` around a closure that builds its
# whole transaction from scratch: serialization_failure (40001) and deadlock_detected
# (40P01). Validation errors, business conflicts (ConcurrentStateUpdateError, 409s),
# integrity errors, connection failures and anything containing an external call are
# never retried. The attempt count is small and fixed.
TRANSIENT_SQLSTATES = frozenset({"40001", "40P01"})
MAX_TRANSIENT_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 0.05

_ROLLBACK_HOOKS_KEY = "rollback_hooks"


def add_rollback_hook(session: Any, hook: Callable[[], None]) -> None:
    """Register `hook` to run if the surrounding transaction rolls back (used to restore
    in-memory concurrency tokens that a save had optimistically advanced)."""
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        info.setdefault(_ROLLBACK_HOOKS_KEY, []).append(hook)


def run_rollback_hooks(session: Any) -> None:
    info = getattr(session, "info", None)
    if not isinstance(info, dict):
        return
    for hook in reversed(info.pop(_ROLLBACK_HOOKS_KEY, [])):
        try:
            hook()
        except Exception:  # pragma: no cover - hooks are trivial attribute restores
            logger.warning("A rollback hook failed.", extra={"error_class": "HookError"})


def discard_rollback_hooks(session: Any) -> None:
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        info.pop(_ROLLBACK_HOOKS_KEY, None)


def record_transaction(scope: str, outcome: str, started: float, cause: str | None = None) -> None:
    """Count one finished transaction (never raises). `scope` is `single` (one repository call that
    owned its session -- includes read-only calls) or `unit_of_work`; `outcome` commit|rollback.
    A rollback caused by an expected business conflict is labelled `conflict`, not a database
    failure (`db_transactions_total` carries no cause; the log event does)."""
    registry.inc("travelobligator_db_transactions_total", {"scope": scope, "outcome": outcome})
    registry.observe("travelobligator_db_transaction_duration_seconds", time.perf_counter() - started, {"scope": scope})
    if outcome == "commit":
        ops_events.log_event(
            logger, logging.DEBUG, ops_events.TRANSACTION_COMMITTED, "Transaction committed.", scope=scope
        )
    else:
        ops_events.log_event(
            logger,
            logging.DEBUG if cause == "conflict" else logging.WARNING,
            ops_events.TRANSACTION_ROLLBACK,
            "Transaction rolled back.",
            scope=scope,
            error_kind=cause or "error",
        )


def rollback_cause(exc: BaseException) -> str:
    from app.repositories.errors import ConcurrentStateUpdateError, JobAlreadyActiveError

    if isinstance(exc, (ConcurrentStateUpdateError, JobAlreadyActiveError)):
        return "conflict"
    return "error"


def record_conflict(kind: str) -> None:
    """A stale writer was rejected by a compare-and-set (expected under contention)."""
    registry.inc("travelobligator_db_concurrency_conflicts_total", {"kind": kind})
    ops_events.log_event(
        logger, logging.WARNING, ops_events.TRANSACTION_CONFLICT, "A stale write was rejected.", kind=kind
    )


@contextmanager
def session_scope(session_factory: Callable[[], Session], bound_session: Session | None) -> Iterator[Session]:
    """See module docstring: own the transaction when standalone, borrow it when bound."""
    if bound_session is not None:
        yield bound_session
        return
    started = time.perf_counter()
    with session_factory() as session:
        try:
            yield session
            session.commit()
            discard_rollback_hooks(session)
        except BaseException as exc:
            session.rollback()
            run_rollback_hooks(session)
            record_transaction("single", "rollback", started, rollback_cause(exc))
            raise
    record_transaction("single", "commit", started)


def sqlstate_of(exc: BaseException) -> str | None:
    """The PostgreSQL SQLSTATE of a driver error, when it has one (never the message)."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code if isinstance(code, str) else None


def is_transient_concurrency_error(exc: BaseException) -> bool:
    return isinstance(exc, (OperationalError, DBAPIError)) and sqlstate_of(exc) in TRANSIENT_SQLSTATES


def run_with_transient_retry(operation: Callable[[], T], *, max_attempts: int = MAX_TRANSIENT_ATTEMPTS) -> T:
    """Run `operation` (which must open and finish its own transaction and be safe to
    repeat from scratch), retrying only on serialization failures / deadlocks."""
    attempt = 1
    while True:
        try:
            return operation()
        except (OperationalError, DBAPIError) as exc:
            if not is_transient_concurrency_error(exc) or attempt >= max_attempts:
                raise
            state = sqlstate_of(exc)
            reason = "deadlock" if state == "40P01" else "serialization_failure"
            registry.inc("travelobligator_db_transaction_retries_total", {"reason": reason})
            if state == "40P01":
                ops_events.log_event(
                    logger, logging.WARNING, ops_events.TRANSACTION_DEADLOCK, "Deadlock detected; retrying.",
                    sqlstate=state, retry_count=attempt,
                )
            ops_events.log_event(
                logger,
                logging.WARNING,
                ops_events.TRANSACTION_RETRY,
                "Retrying a transaction after a transient database concurrency condition.",
                error_class=type(exc).__name__,
                sqlstate=state,
                attempt=attempt,
                retry_count=attempt,
                error_kind=reason,
            )
            time.sleep(_RETRY_BACKOFF_SECONDS * attempt)
            attempt += 1
