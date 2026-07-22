from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.storage.local_json_store import LocalJsonStore, get_local_json_store

_COLLECTION = "trips"


class TripRecord(BaseModel):
    trip_id: str
    status: str = "draft"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TripRepository:
    """Local-file-backed trip repository for development.

    Trip records are cached in memory for the lifetime of this instance
    (loaded from disk at construction time) and persisted to a local JSON
    file (see app.storage.local_json_store) on every write, so they survive
    backend process restarts during local development. Not for production
    use: no auth, no multi-worker/multi-process coordination, no
    migrations -- see ARCHITECTURE.md.
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

    def create(self, trip_id: str) -> TripRecord:
        record = TripRecord(trip_id=trip_id)
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


trip_repository = TripRepository()
