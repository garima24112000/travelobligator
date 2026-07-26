from __future__ import annotations

from typing import Any

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


# Kept in sync with app.services.user_lock_service.LOCKABLE_ITEM_TYPES.
LOCKABLE_ITEM_TYPES = {
    "stay_area",
    "accommodation",
    "experience",
    "restaurant",
    "day_plan",
    "transport_strategy",
}


class LockRequest(BaseModel):
    locked_item_type: str
    locked_item_id: str = Field(min_length=1)
    reason: str = "user_requested_keep"

    @field_validator("locked_item_id", mode="before")
    @classmethod
    def strip_locked_item_id(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("locked_item_type")
    @classmethod
    def validate_locked_item_type(cls, value: str) -> str:
        if value not in LOCKABLE_ITEM_TYPES:
            raise ValueError(
                f"locked_item_type must be one of {sorted(LOCKABLE_ITEM_TYPES)}."
            )
        return value

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: Any) -> Any:
        if value is None:
            return "user_requested_keep"
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or "user_requested_keep"
        return value
