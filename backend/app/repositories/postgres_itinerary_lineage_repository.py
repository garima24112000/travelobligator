"""Opt-in Postgres-backed itinerary-branch/revision repository (Section
199A).

Only ever constructed when `Settings.persistence_backend == "postgres"`
(see `app/repositories/factory.py`) -- the default `local_json` backend
never imports or constructs this class, so it never opens a database
connection unless an operator has explicitly opted in.

Behavior matches `app.repositories.itinerary_lineage_repository.
ItineraryLineageRepository` (see
`app/repositories/protocols.py`'s `ItineraryLineageRepositoryProtocol`),
with two extra database-level safeguards the local-JSON repository
cannot offer: a `(branch_id, version_label)` UNIQUE constraint backing
`create_revision`'s idempotency (Task 29), and a partial unique index
backing "at most one default branch per trip."
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ItineraryBranchRow, ItineraryRevisionRow
from app.db.session import get_session_factory
from app.models.itinerary_lineage import ItineraryBranch, ItineraryRevision


def _branch_from_row(row: ItineraryBranchRow) -> ItineraryBranch:
    return ItineraryBranch.model_validate(
        {
            "branch_id": row.branch_id,
            "trip_id": row.trip_id,
            "display_name": row.display_name,
            "is_default": bool(row.is_default),
            "base_revision_id": row.base_revision_id,
            "head_revision_id": row.head_revision_id,
            "created_at": row.created_at,
        }
    )


def _branch_values(branch: ItineraryBranch) -> dict:
    return {
        "branch_id": branch.branch_id,
        "trip_id": branch.trip_id,
        "display_name": branch.display_name,
        "is_default": branch.is_default,
        "base_revision_id": branch.base_revision_id,
        "head_revision_id": branch.head_revision_id,
        "created_at": branch.created_at,
    }


def _revision_from_row(row: ItineraryRevisionRow) -> ItineraryRevision:
    return ItineraryRevision.model_validate(
        {
            "revision_id": row.revision_id,
            "trip_id": row.trip_id,
            "branch_id": row.branch_id,
            "parent_revision_id": row.parent_revision_id,
            "version_label": row.version_label,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "feedback_event_id": row.feedback_event_id,
            "version_history_item_id": row.version_history_item_id,
            "snapshot_available": bool(row.snapshot_available),
            "snapshot": row.snapshot,
        }
    )


def _revision_values(revision: ItineraryRevision) -> dict:
    return {
        "revision_id": revision.revision_id,
        "trip_id": revision.trip_id,
        "branch_id": revision.branch_id,
        "parent_revision_id": revision.parent_revision_id,
        "version_label": revision.version_label,
        "created_by": revision.created_by,
        "created_at": revision.created_at,
        "feedback_event_id": revision.feedback_event_id,
        "version_history_item_id": revision.version_history_item_id,
        "snapshot_available": revision.snapshot_available,
        "snapshot": revision.snapshot,
    }


class PostgresItineraryLineageRepository:
    """Postgres-backed equivalent of `ItineraryLineageRepository`.

    Opens a short-lived `Session` per method call (via the injected/
    default session factory) rather than caching branches/revisions in
    memory -- unlike the local JSON repository, a real database is the
    shared source of truth across processes.
    """

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    # -- branches -----------------------------------------------------------

    def create_branch(self, branch: ItineraryBranch) -> ItineraryBranch:
        """Upserts by `branch_id` (a fresh id every real call, so this
        is a plain insert in practice) -- matches every other
        `create()` in this codebase's "replace by id" convention."""
        values = _branch_values(branch)
        stmt = pg_insert(ItineraryBranchRow.__table__).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["branch_id"], set_=values)
        with self._session_factory() as session:
            session.execute(stmt)
            session.commit()
        return branch

    def get_branch(self, branch_id: str) -> ItineraryBranch | None:
        with self._session_factory() as session:
            row = session.get(ItineraryBranchRow, branch_id)
            if row is None:
                return None
            return _branch_from_row(row)

    def get_default_branch(self, trip_id: str) -> ItineraryBranch | None:
        with self._session_factory() as session:
            row = session.execute(
                select(ItineraryBranchRow).where(
                    ItineraryBranchRow.trip_id == trip_id,
                    ItineraryBranchRow.is_default.is_(True),
                )
            ).scalar_one_or_none()
            return _branch_from_row(row) if row is not None else None

    def list_branches_for_trip(self, trip_id: str) -> list[ItineraryBranch]:
        with self._session_factory() as session:
            rows = session.execute(
                select(ItineraryBranchRow)
                .where(ItineraryBranchRow.trip_id == trip_id)
                .order_by(ItineraryBranchRow.created_at, ItineraryBranchRow.branch_id)
            ).scalars()
            return [_branch_from_row(row) for row in rows]

    def update_branch_head(
        self, branch_id: str, head_revision_id: str
    ) -> ItineraryBranch | None:
        with self._session_factory() as session:
            row = session.get(ItineraryBranchRow, branch_id)
            if row is None:
                return None
            row.head_revision_id = head_revision_id
            session.commit()
            session.refresh(row)
            return _branch_from_row(row)

    # -- revisions ------------------------------------------------------------

    def create_revision(self, revision: ItineraryRevision) -> ItineraryRevision:
        """Atomically idempotent on `(branch_id, version_label)` (Task
        29/30): if a concurrent call already recorded a revision for
        this exact branch+version, `ON CONFLICT DO NOTHING` skips this
        insert and the existing row is returned instead -- never a
        second historical revision for the same real version, and never
        a raised `IntegrityError` a caller would need to handle."""
        values = _revision_values(revision)
        stmt = (
            pg_insert(ItineraryRevisionRow.__table__)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["branch_id", "version_label"],
            )
        )
        with self._session_factory() as session:
            session.execute(stmt)
            session.commit()
            existing = session.execute(
                select(ItineraryRevisionRow).where(
                    ItineraryRevisionRow.branch_id == revision.branch_id,
                    ItineraryRevisionRow.version_label == revision.version_label,
                )
            ).scalar_one()
            return _revision_from_row(existing)

    def get_revision(self, revision_id: str) -> ItineraryRevision | None:
        with self._session_factory() as session:
            row = session.get(ItineraryRevisionRow, revision_id)
            if row is None:
                return None
            return _revision_from_row(row)

    def list_revisions_for_branch(self, branch_id: str) -> list[ItineraryRevision]:
        with self._session_factory() as session:
            rows = session.execute(
                select(ItineraryRevisionRow)
                .where(ItineraryRevisionRow.branch_id == branch_id)
                .order_by(ItineraryRevisionRow.created_at, ItineraryRevisionRow.revision_id)
            ).scalars()
            return [_revision_from_row(row) for row in rows]

    def get_revision_by_branch_and_version(
        self, branch_id: str, version_label: str
    ) -> ItineraryRevision | None:
        with self._session_factory() as session:
            row = session.execute(
                select(ItineraryRevisionRow).where(
                    ItineraryRevisionRow.branch_id == branch_id,
                    ItineraryRevisionRow.version_label == version_label,
                )
            ).scalar_one_or_none()
            return _revision_from_row(row) if row is not None else None
