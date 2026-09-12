from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.storage.local_json_store import LocalJsonStore, get_local_json_store

_COLLECTION = "trips"


class TripRecord(BaseModel):
    trip_id: str
    # Step 184D: `None` means "no owner" -- either a trip created before
    # this step existed (local dev scratch data from Section 183's own
    # verification, or any pre-184D local_json/Postgres trip), or,
    # in principle, a future admin-created trip. Deliberately never
    # backfilled/assigned automatically to whichever user happens to
    # request it first -- `app/auth/ownership.py`'s `require_trip_owner`
    # treats `owner_id=None` as "inaccessible to any authenticated user"
    # (403), the same as a trip owned by someone else. `PlanningState`
    # itself never gets an `owner_id` field -- ownership lives on `trips`
    # only (see docs/14_backend_architecture.md section 110).
    owner_id: str | None = None
    status: str = "draft"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TripRepository:
    """Local-file-backed trip repository for development.

    Trip records are cached in memory for the lifetime of this instance
    (loaded from disk at construction time) and persisted to a local JSON
    file (see app.storage.local_json_store) on every write, so they survive
    backend process restarts during local development. Not for production
    use: no multi-worker/multi-process coordination, no migrations -- see
    ARCHITECTURE.md.
    """

    def __init__(self, store: LocalJsonStore | None = None) -> None:
        self._store = store or get_local_json_store(
            get_settings().resolved_local_storage_path()
        )
        self._trips: dict[str, TripRecord] = {
            trip_id: TripRecord.model_validate(record)
            for trip_id, record in self._store.read_collection(_COLLECTION).items()
        }

    def _persist(self) -> None:
        self._store.write_collection(
            _COLLECTION,
            {
                trip_id: record.model_dump(mode="json")
                for trip_id, record in self._trips.items()
            },
        )

    def create(self, trip_id: str, owner_id: str | None = None) -> TripRecord:
        """Always replaces whatever record existed for `trip_id` with a
        brand-new one (fresh `created_at`/`updated_at`, matching this
        method's pre-existing "overwrite, don't reject" behavior on a
        duplicate `trip_id` -- see `PostgresTripRepository.create`'s own
        docstring, which mirrors this exactly). `owner_id` defaults to
        `None` for backward compatibility with any caller not yet passing
        one; as of Step 184D, `PlanningOrchestrator.create_trip` always
        passes the real authenticated user's id.
        """
        record = TripRecord(trip_id=trip_id, owner_id=owner_id)
        self._trips[trip_id] = record
        self._persist()
        return record

    def get(self, trip_id: str) -> TripRecord | None:
        return self._trips.get(trip_id)

    def update_status(self, trip_id: str, status: str) -> TripRecord | None:
        record = self._trips.get(trip_id)
        if record is None:
            return None

        record.status = status
        record.updated_at = datetime.now(timezone.utc)
        self._persist()
        return record

    def list_by_owner_id(self, owner_id: str) -> list[TripRecord]:
        """Returns every trip owned by `owner_id` -- `GET /trips`'s "My
        Trips" data source (Step 184D). Never returns a trip with a
        `None` or different `owner_id`."""
        return [record for record in self._trips.values() if record.owner_id == owner_id]


trip_repository = TripRepository()
