"""Counted signups and failed sign-ins (P0 abuse limits; used only by `app/auth/abuse.py`).

Two interchangeable implementations of one small contract, resolved by
`app.repositories.factory.get_auth_attempt_repository()`:

  * `PostgresAuthAttemptRepository` -- the `auth_attempts` table: authoritative, shared by every
    backend process, and it survives a restart or a free-tier sleep.
  * `AuthAttemptRepository` -- development / hermetic tests (`PERSISTENCE_BACKEND=local_json`): a list
    in process memory, deliberately not written to the Local JSON file. No cross-process guarantee,
    like the rest of that backend.

A row carries a `kind`, two optional keyed hashes and a timestamp -- never an email or an IP address.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

SIGNUP = "signup"
LOGIN_FAILURE = "login_failure"


@dataclass(frozen=True)
class _Attempt:
    kind: str
    subject_hash: str | None
    ip_hash: str | None
    created_at: datetime


class AuthAttemptRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts: list[_Attempt] = []

    def record(
        self,
        kind: str,
        *,
        now: datetime,
        subject_hash: str | None = None,
        ip_hash: str | None = None,
        prune_before: datetime | None = None,
    ) -> None:
        with self._lock:
            if prune_before is not None:
                self._attempts = [a for a in self._attempts if a.created_at >= prune_before]
            self._attempts.append(_Attempt(kind, subject_hash, ip_hash, now))

    def count(
        self,
        kind: str,
        *,
        since: datetime,
        subject_hash: str | None = None,
        ip_hash: str | None = None,
    ) -> int:
        """Rows of `kind` later than `since`; with a hash given, only the rows carrying it."""
        with self._lock:
            return sum(
                1
                for a in self._attempts
                if a.kind == kind
                and a.created_at > since
                and (subject_hash is None or a.subject_hash == subject_hash)
                and (ip_hash is None or a.ip_hash == ip_hash)
            )

    def clear(self, kind: str, *, subject_hash: str) -> None:
        with self._lock:
            self._attempts = [
                a for a in self._attempts if not (a.kind == kind and a.subject_hash == subject_hash)
            ]

    def reset(self) -> None:
        """Test isolation only."""
        with self._lock:
            self._attempts = []


class PostgresAuthAttemptRepository:
    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        from app.db.session import get_session_factory

        self._session_factory = session_factory or get_session_factory()

    def _scope(self):
        from app.db.transactions import session_scope

        return session_scope(self._session_factory, None)

    def record(
        self,
        kind: str,
        *,
        now: datetime,
        subject_hash: str | None = None,
        ip_hash: str | None = None,
        prune_before: datetime | None = None,
    ) -> None:
        from app.db.models import AuthAttemptRow

        with self._scope() as session:
            if prune_before is not None:
                session.execute(delete(AuthAttemptRow).where(AuthAttemptRow.created_at < prune_before))
            session.add(
                AuthAttemptRow(
                    attempt_id=uuid4().hex,
                    kind=kind,
                    subject_hash=subject_hash,
                    ip_hash=ip_hash,
                    created_at=now,
                )
            )

    def count(
        self,
        kind: str,
        *,
        since: datetime,
        subject_hash: str | None = None,
        ip_hash: str | None = None,
    ) -> int:
        from app.db.models import AuthAttemptRow

        conditions = [AuthAttemptRow.kind == kind, AuthAttemptRow.created_at > since]
        if subject_hash is not None:
            conditions.append(AuthAttemptRow.subject_hash == subject_hash)
        if ip_hash is not None:
            conditions.append(AuthAttemptRow.ip_hash == ip_hash)
        with self._scope() as session:
            return session.execute(
                select(func.count()).select_from(AuthAttemptRow).where(*conditions)
            ).scalar_one()

    def clear(self, kind: str, *, subject_hash: str) -> None:
        from app.db.models import AuthAttemptRow

        with self._scope() as session:
            session.execute(
                delete(AuthAttemptRow).where(
                    AuthAttemptRow.kind == kind, AuthAttemptRow.subject_hash == subject_hash
                )
            )


auth_attempt_repository = AuthAttemptRepository()
