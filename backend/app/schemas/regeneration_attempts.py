from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.planning_state import RegenerationAttempt


class RegenerationAttemptsResponseData(BaseModel):
    trip_id: str
    regeneration_attempts: list[RegenerationAttempt] = Field(default_factory=list)
