from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class TripRecord(BaseModel):
    trip_id: str
    status: str = "draft"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TripRepository:
    """In-memory trip repository for development.

    Not for production use: state is lost on process restart and is not
    shared across multiple backend workers.
    """

    def __init__(self) -> None:
        self._trips: dict[str, TripRecord] = {}

    def create(self, trip_id: str) -> TripRecord:
        record = TripRecord(trip_id=trip_id)
        self._trips[trip_id] = record
        return record

    def get(self, trip_id: str) -> TripRecord | None:
        return self._trips.get(trip_id)

    def update_status(self, trip_id: str, status: str) -> TripRecord | None:
        record = self._trips.get(trip_id)
        if record is None:
            return None

        record.status = status
        record.updated_at = datetime.now(timezone.utc)
        return record


trip_repository = TripRepository()
