"""Opt-in Postgres-backed trip repository (Step 183D, extended in Step
184D with `owner_id`/`list_by_owner_id`).

Only ever constructed when `Settings.persistence_backend == "postgres"`
(see `app/repositories/factory.py`) -- the default `local_json` backend
never imports or constructs this class, so it never opens a database
connection unless an operator has explicitly opted in.

Behavior is written to match `app.repositories.trip_repository.
TripRepository` exactly (same method names/signatures/return semantics --
see `app/repositories/protocols.py`'s `TripRepositoryProtocol`), including
`create`'s exact "overwrite, don't reject" behavior on a duplicate
`trip_id`: the local repository's `create()` always replaces whatever
`TripRecord` existed in memory with a brand-new one (fresh
`created_at`/`updated_at`), so this repository's `create()` upserts the
same way rather than rejecting a duplicate -- now including `owner_id` in
that overwrite.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import TripRow
from app.db.session import get_session_factory
from app.repositories.trip_repository import TripRecord


def _row_to_record(row: TripRow) -> TripRecord:
    return TripRecord(
        trip_id=row.trip_id,
        owner_id=row.owner_id,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresTripRepository:
    """Postgres-backed equivalent of `TripRepository`.

    Opens a short-lived `Session` per method call (via the injected/
    default session factory) rather than caching records in memory --
    unlike the local JSON repository, a real database is the shared
    source of truth across processes, so there is no in-memory cache to
    keep consistent.
    """

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    def create(self, trip_id: str, owner_id: str | None = None) -> TripRecord:
        now = datetime.now(timezone.utc)
        stmt = pg_insert(TripRow.__table__).values(
            trip_id=trip_id,
            status="draft",
            owner_id=owner_id,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["trip_id"],
            set_={"status": "draft", "owner_id": owner_id, "created_at": now, "updated_at": now},
        )
        with self._session_factory() as session:
            session.execute(stmt)
            session.commit()

        return TripRecord(
            trip_id=trip_id, owner_id=owner_id, status="draft", created_at=now, updated_at=now
        )

    def get(self, trip_id: str) -> TripRecord | None:
        with self._session_factory() as session:
            row = session.get(TripRow, trip_id)
            if row is None:
                return None
            return _row_to_record(row)

    def update_status(self, trip_id: str, status: str) -> TripRecord | None:
        with self._session_factory() as session:
            row = session.get(TripRow, trip_id)
            if row is None:
                return None

            row.status = status
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(row)
            return _row_to_record(row)

    def list_by_owner_id(self, owner_id: str) -> list[TripRecord]:
        """Returns every trip owned by `owner_id` -- `GET /trips`'s "My
        Trips" data source (Step 184D)."""
        with self._session_factory() as session:
            rows = session.execute(
                select(TripRow).where(TripRow.owner_id == owner_id)
            ).scalars().all()
            return [_row_to_record(row) for row in rows]
