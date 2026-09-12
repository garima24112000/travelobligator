"""Opt-in Postgres-backed user repository (Step 184C).

Only ever constructed when `Settings.persistence_backend == "postgres"`
(see `app/repositories/factory.py`) -- the default `local_json` backend
never imports or constructs this class, so it never opens a database
connection unless an operator has explicitly opted in.

Behavior is written to match `app.repositories.user_repository.
UserRepository` exactly (same method names/signatures/return semantics --
see `app/repositories/protocols.py`'s `UserRepositoryProtocol`), including
duplicate-email handling: both raise the same backend-agnostic
`UserAlreadyExistsError` -- here in response to the `users.email` UNIQUE
constraint's `IntegrityError` rather than an explicit pre-check, since a
real database can enforce that atomically without a race window a
check-then-insert would have.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import UserRow
from app.db.session import get_session_factory
from app.models.user import UserRecord
from app.repositories.protocols import UserAlreadyExistsError


def _row_to_record(row: UserRow) -> UserRecord:
    return UserRecord(
        user_id=row.user_id,
        email=row.email,
        password_hash=row.password_hash,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresUserRepository:
    """Postgres-backed equivalent of `UserRepository`.

    Opens a short-lived `Session` per method call (via the injected/
    default session factory) rather than caching records in memory --
    unlike the local JSON repository, a real database is the shared
    source of truth across processes.
    """

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def create_user(self, user: UserRecord) -> UserRecord:
        """Raises `UserAlreadyExistsError` if `user.email` already has an
        account -- detected via the `users.email` UNIQUE constraint, not
        a separate lookup, so this stays correct under concurrent signup
        attempts for the same email."""
        with self._session_factory() as session:
            row = UserRow(
                user_id=user.user_id,
                email=user.email,
                password_hash=user.password_hash,
                created_at=user.created_at,
                updated_at=user.updated_at,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise UserAlreadyExistsError(user.email) from exc

        return user

    def get_by_user_id(self, user_id: str) -> UserRecord | None:
        with self._session_factory() as session:
            row = session.get(UserRow, user_id)
            if row is None:
                return None
            return _row_to_record(row)

    def get_by_email(self, email: str) -> UserRecord | None:
        normalized = email.strip().lower()
        with self._session_factory() as session:
            row = session.execute(
                select(UserRow).where(UserRow.email == normalized)
            ).scalar_one_or_none()
            if row is None:
                return None
            return _row_to_record(row)
