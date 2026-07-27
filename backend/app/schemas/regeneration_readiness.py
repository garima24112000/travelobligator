from __future__ import annotations

from pydantic import BaseModel

from app.models.planning_state import RegenerationReadiness


class RegenerationReadinessResponseData(BaseModel):
    trip_id: str
    regeneration_readiness: RegenerationReadiness
