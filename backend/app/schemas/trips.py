from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.models.planning_state import PlanningState


class TripResponseData(BaseModel):
    trip_id: str
    planning_state: PlanningState


class FeedbackRequest(BaseModel):
    feedback_text: str = Field(max_length=3000)

    @field_validator("feedback_text")
    @classmethod
    def validate_feedback_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("feedback_text must not be empty or blank.")
        return stripped
