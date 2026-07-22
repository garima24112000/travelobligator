from __future__ import annotations

from app.core.config import get_settings
from app.models.planning_state import PlanningState
from app.storage.local_json_store import LocalJsonStore, get_local_json_store

_COLLECTION = "planning_states"


class PlanningStateRepository:
    """Local-file-backed planning state repository for development.

    Stores only the latest PlanningState per trip_id, cached in memory for
    the lifetime of this instance (loaded from disk at construction time)
    and persisted to a local JSON file (see app.storage.local_json_store) on
    every save, so generated plans survive backend process restarts during
    local development. Not for production use: no multi-worker/
    multi-process coordination, no migrations -- see ARCHITECTURE.md.
    """

    def __init__(self, store: LocalJsonStore | None = None) -> None:
        self._store = store or get_local_json_store(
            get_settings().resolved_local_storage_path()
        )
        self._states: dict[str, PlanningState] = {
            trip_id: PlanningState.model_validate(record)
            for trip_id, record in self._store.read_collection(_COLLECTION).items()
        }

    def save(self, planning_state: PlanningState) -> PlanningState:
        self._states[planning_state.trip_id] = planning_state
        self._store.write_collection(
            _COLLECTION,
            {
                trip_id: state.model_dump(mode="json")
                for trip_id, state in self._states.items()
            },
        )
        return planning_state

    def get_by_trip_id(self, trip_id: str) -> PlanningState | None:
        return self._states.get(trip_id)


planning_state_repository = PlanningStateRepository()
