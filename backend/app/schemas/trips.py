from __future__ import annotations

from pydantic import BaseModel

from app.models.planning_state import PlanningState


class TripResponseData(BaseModel):
    trip_id: str
    planning_state: PlanningState
