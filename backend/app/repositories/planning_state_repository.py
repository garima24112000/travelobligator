from __future__ import annotations

from app.models.planning_state import PlanningState


class PlanningStateRepository:
    """In-memory planning state repository for development.

    Stores only the latest PlanningState per trip_id. Not for production
    use: state is lost on process restart and is not shared across
    multiple backend workers.
    """

    def __init__(self) -> None:
        self._states: dict[str, PlanningState] = {}

    def save(self, planning_state: PlanningState) -> PlanningState:
        self._states[planning_state.trip_id] = planning_state
        return planning_state

    def get_by_trip_id(self, trip_id: str) -> PlanningState | None:
        return self._states.get(trip_id)


planning_state_repository = PlanningStateRepository()
