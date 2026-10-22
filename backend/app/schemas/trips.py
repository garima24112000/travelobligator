from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.core.config import get_settings
from app.models.planning_state import PlanningState, TripRequest


_BOUNDED_TEXT_LISTS = {
    "interests": "interests",
    "must_visit": "must-visit places",
    "must_avoid": "places to avoid",
    "constraints": "constraints",
}
_BOUNDED_CHOICE_LISTS = ("preferred_accommodation_types", "transport_preferences")


class TripCreateRequest(TripRequest):
    """The body of `POST /trips`: a `TripRequest` plus the size limits of a NEW request.

    The limits live here, at the request boundary, and not on `TripRequest` itself: `TripRequest` is
    also how a stored trip is read back, and a trip saved before a limit existed (or under a larger
    configured one) must still load. Every limit comes from `Settings`; a violation is an ordinary
    HTTP 422 naming the field, worded for the traveller. Nothing here changes how a trip is planned.
    """

    @field_validator("end_date", mode="after")
    @classmethod
    def _limit_trip_length(cls, value: date, info: ValidationInfo) -> date:
        start = info.data.get("start_date")
        max_days = get_settings().max_trip_days
        # Calendar days including both the start and the end date.
        if isinstance(start, date) and (value - start).days + 1 > max_days:
            raise ValueError(
                f"A trip can be at most {max_days} days long, counting the first and the last day. "
                "Choose an earlier end date."
            )
        return value

    @field_validator(*_BOUNDED_TEXT_LISTS, mode="after")
    @classmethod
    def _limit_text_lists(cls, value: list[str], info: ValidationInfo) -> list[str]:
        settings = get_settings()
        label = _BOUNDED_TEXT_LISTS[info.field_name or ""]
        if len(value) > settings.trip_request_max_list_items:
            raise ValueError(
                f"Please list at most {settings.trip_request_max_list_items} {label}."
            )
        if any(len(item) > settings.trip_request_max_item_length for item in value):
            raise ValueError(
                f"Each entry in {label} can be at most "
                f"{settings.trip_request_max_item_length} characters long."
            )
        return value

    @field_validator(*_BOUNDED_CHOICE_LISTS, mode="after")
    @classmethod
    def _limit_choice_lists(cls, value: list[Any]) -> list[Any]:
        if len(value) > get_settings().trip_request_max_list_items:
            raise ValueError("Too many options were selected.")
        return value

    def to_trip_request(self) -> TripRequest:
        """The plain `TripRequest` that is stored and planned from."""
        return TripRequest.model_validate(self.model_dump())


class TripResponseData(BaseModel):
    trip_id: str
    planning_state: PlanningState


class TripDeletedResponseData(BaseModel):
    """`DELETE /trips/{trip_id}` (P1-A): the trip and everything stored for it are gone."""

    trip_id: str
    deleted: bool = True


class TripListItem(BaseModel):
    """One row of `GET /trips`'s "My Trips" list (Step 184D) --
    deliberately small: never the full `PlanningState` (that stays behind
    `GET /trips/{trip_id}` and friends, each still owner-checked
    individually). `primary_destination`/`origin_city`/`start_date`/
    `end_date` are `None` only in the on-paper-impossible case of a
    `TripRecord` with no matching `PlanningState` -- normal trips created
    via `POST /trips` always have both.
    """

    trip_id: str
    status: str
    primary_destination: str | None = None
    origin_city: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    created_at: datetime
    updated_at: datetime


class TripListResponseData(BaseModel):
    trips: list[TripListItem]


class RegenerateRequest(BaseModel):
    """Request body for `POST /trips/{trip_id}/regenerate` (Step 174B).

    Fully optional -- an absent body behaves identically to an explicit
    `{"confirm": false}`, both producing today's exact
    `REGENERATION_NOT_AVAILABLE` refusal (docs/17_regeneration_manual_qa.md).
    `scope` is accepted now so a later step doesn't need a breaking
    request-shape change, but `"affected_stages"` (whole-plan/stage-level
    regeneration only) is the only value anything reads today -- Section
    174 does not implement day-level or item-level regeneration scopes.
    """

    confirm: bool = False
    scope: str = "affected_stages"


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
