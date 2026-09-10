"""Repository contracts (Step 183D).

Structural (`typing.Protocol`) contracts documenting the exact public
surface every persistence backend must satisfy -- both the existing
`LocalJsonStore`-backed repositories and the new opt-in Postgres-backed
ones (`postgres_trip_repository.py`, `postgres_planning_state_repository.py`).
`TripRepository`/`PlanningStateRepository` already satisfy these
structurally without any change; this file adds no behavior, only a
type-checkable description of the contract for
`app/repositories/factory.py`'s return types.
"""

from __future__ import annotations

from typing import Protocol

from app.models.planning_state import PlanningState
from app.repositories.trip_repository import TripRecord


class TripRepositoryProtocol(Protocol):
    def create(self, trip_id: str) -> TripRecord: ...

    def get(self, trip_id: str) -> TripRecord | None: ...

    def update_status(self, trip_id: str, status: str) -> TripRecord | None: ...


class PlanningStateRepositoryProtocol(Protocol):
    def save(self, planning_state: PlanningState) -> PlanningState: ...

    def get_by_trip_id(self, trip_id: str) -> PlanningState | None: ...
