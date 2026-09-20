from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.models.ai_itinerary_reasoning import TravelerContextSummary

# Section 196 (docs/14_backend_architecture.md, following section 146):
# contract-only models for a natural-language itinerary-feedback
# interpreter -- a fourth AI-facing contract in this repo, alongside
# `ai_candidate_proposal`/`ai_itinerary_reasoning`/`ai_itinerary_repair`.
# Deliberately does NOT import `app.models.planning_state` (same reason
# every other leaf AI-contract module in this repo doesn't: that module
# is what will eventually import from this one, not the reverse; see
# `TravelerContextSummary`'s own docstring in `ai_itinerary_reasoning.py`
# for the identical rationale, reused verbatim here).
#
# Core rule (unchanged from every earlier AI-facing contract in this
# repo, restated for feedback interpretation specifically): this
# interpreter answers "what change is the user asking for?", never "what
# should the new itinerary be?". It never regenerates anything, never
# calls provider discovery, never mutates a `PlanningState`, and never
# produces a place that isn't already part of the current, final,
# already-provider-backed itinerary context it was given --
# `NewPlaceRequest` is the one, explicit, structurally-incapable-of-
# fabricating-identity escape hatch for "the user asked for somewhere
# not currently in the plan" (Task 6/15): it can carry a search query
# string, never a coordinate, provider id, price, rating, opening hour,
# or availability claim.

_FORBIDDEN_TEXT_PATTERNS: tuple[str, ...] = (
    "rating",
    "price",
    "opening_hours",
    "route_time",
    "route_distance",
    "booking_url",
    "review_count",
    "ticket_price",
    "availability",
    "safety_score",
    "reservation",
    "book now",
    "open until",
    "highly rated",
    "cheap",
    "guaran" + "teed",
    "exact travel time",
    "09:",
    "12:",
    "17-minute",
    "optimal",
    "verified",
    "travel-ready",
    "booking-ready",
    "safest",
    "the best",
    "is closed",
    "is unsafe",
)


def _find_forbidden_pattern(text: str) -> str | None:
    lowered = text.lower()
    for pattern in _FORBIDDEN_TEXT_PATTERNS:
        if pattern in lowered:
            return pattern
    return None


def _require_non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank.")
    return value


def _check_forbidden(value: str, field_name: str) -> str:
    forbidden = _find_forbidden_pattern(value)
    if forbidden is not None:
        raise ValueError(f"{field_name} must not contain forbidden pattern: {forbidden!r}")
    return value


class AIFeedbackItineraryItemContext(BaseModel):
    """One current, final, already-scheduled itinerary item, reduced to
    only the fields the interpreter needs to resolve a user's reference
    to it (Task 3/4). `experience_id` is the one stable reference the
    interpreter's own structured output must use -- never an array
    index, never a name-only match. `candidate_id` is included only when
    the real `ExperienceItem` actually carries provider identity (only
    ever true for an AI-directed promoted candidate today -- a plain
    broad-pool-scheduled item's `provider_place_id`/`provider_source`
    stay `None`, so this is `None` too; "where available" per Task 3, not
    a guarantee).
    """

    experience_id: str
    candidate_id: str | None = None
    name: str
    category: str | None = None
    day_index: int = Field(ge=1)

    @field_validator("experience_id", "name")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)


class AIFeedbackLockContext(BaseModel):
    """One active `UserLock`, reduced to the two fields that identify
    what it locks -- mirrors `UserLock.locked_item_type`/`locked_item_id`
    exactly (`app.models.planning_state`), never re-derives or guesses a
    lock's meaning.
    """

    locked_item_type: str
    locked_item_id: str


# Task 13: `TripPace`'s own three real values, restated as a plain
# string tuple (this module never imports the `planning_state` enum
# itself -- see this module's own top docstring).
_SUPPORTED_PACE_VALUES = ("relaxed", "balanced", "packed")

# Task 20's fixed, deterministic interpreter instructions -- the single
# source every provider adapter reuses rather than writing its own
# provider-specific prompt copy, mirroring
# `DEFAULT_ITINERARY_REASONING_INSTRUCTIONS`/`DEFAULT_ITINERARY_REPAIR_INSTRUCTIONS`.
DEFAULT_FEEDBACK_INTERPRETATION_INSTRUCTIONS: tuple[str, ...] = (
    "You are interpreting user-requested itinerary changes. You do not redesign the "
    "itinerary yourself -- you only translate the user's feedback into a structured "
    "change request for a later step to act on.",
    "Reference an existing itinerary item only by its experience_id, exactly as given in "
    "current_items. Never invent a new experience_id, and never resolve a reference by "
    "position/order.",
    "If the user asks for a place that is not in current_items, return it only as a "
    "new_place_request with the name/description they used as the query -- never invent a "
    "provider id, coordinates, price, rating, opening hours, or availability for it.",
    "Do not invent new provider facts for an existing item either -- you may reference it "
    "by experience_id, but you may never state or imply a price, rating, opening hour, "
    "route duration, or availability for it.",
    "If the user states a reason (e.g. 'it's closed', 'that area is unsafe'), treat that as "
    "their own opinion/context for the action, never as a verified fact -- do not create a "
    "field asserting it as true.",
    "Preserve every explicit 'do not change'/'keep' constraint the user states, using the "
    "preserve field -- never leave it only in a rationale/summary string.",
    "If a reference is ambiguous (e.g. two current items could both be 'the museum'), "
    "return status=needs_clarification with the possible experience_ids -- never guess "
    "which one the user means.",
    "Map a pace/preference request only onto pace (relaxed/balanced/packed), interests, "
    "must_visit, or must_avoid -- never invent a new preference concept.",
    "Explicit scope wins: if the user names a specific day (e.g. 'day 2'), keep the "
    "interpretation scoped to that day -- use scope=single_day (or multiple_days for several "
    "named days) with a regenerate_day action carrying an instruction describing the "
    "day-specific request -- unless the user explicitly says the whole trip/every day/overall "
    "itinerary should change. Never convert a day-specific complaint like 'Day 2 is too "
    "packed, make it more relaxed' into a global change_pace or adjust_interest action.",
    "Return structured interpretation only.",
)


class AIFeedbackInterpretationRequest(BaseModel):
    """Everything a feedback-interpretation call receives as input (Task
    3). Bounded and typed -- never a raw `PlanningState` dump.
    """

    model_config = ConfigDict(protected_namespaces=())

    trip_id: str
    current_version: str | None = None
    destination_name: str
    start_date: str
    end_date: str
    trip_duration_days: int = Field(ge=1)
    traveler_context: TravelerContextSummary
    feedback_text: str

    current_items: list[AIFeedbackItineraryItemContext] = Field(default_factory=list)
    active_locks: list[AIFeedbackLockContext] = Field(default_factory=list)

    validation_status: str | None = None
    warning_count: int = 0
    critical_issue_count: int = 0

    interpretation_instructions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FEEDBACK_INTERPRETATION_INSTRUCTIONS)
    )

    @field_validator("trip_id", "destination_name", "feedback_text")
    @classmethod
    def validate_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_non_blank(value, info.field_name)

    @field_validator("current_items")
    @classmethod
    def validate_unique_experience_ids(
        cls, value: list[AIFeedbackItineraryItemContext]
    ) -> list[AIFeedbackItineraryItemContext]:
        seen: set[str] = set()
        for item in value:
            if item.experience_id in seen:
                raise ValueError(
                    "AIFeedbackInterpretationRequest.current_items has duplicate "
                    f"experience_id: {item.experience_id!r}."
                )
            seen.add(item.experience_id)
        return value

    def allowed_experience_ids(self) -> set[str]:
        return {item.experience_id for item in self.current_items}


class AIFeedbackActionType(str, Enum):
    REMOVE_EXPERIENCE = "remove_experience"
    MOVE_EXPERIENCE = "move_experience"
    CHANGE_PACE = "change_pace"
    ADJUST_INTEREST = "adjust_interest"
    REGENERATE_DAY = "regenerate_day"
    # Task 5: an honest, deferred catch-all for feedback that doesn't map
    # onto any of the typed actions above -- never a silent drop, and
    # never pretended to be actionable by a later step until it defines
    # a real mechanism for it.
    GENERAL_INSTRUCTION = "general_instruction"


class RemoveExperienceAction(BaseModel):
    type: Literal[AIFeedbackActionType.REMOVE_EXPERIENCE] = AIFeedbackActionType.REMOVE_EXPERIENCE
    experience_id: str

    @field_validator("experience_id")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "experience_id")


class MoveExperienceAction(BaseModel):
    type: Literal[AIFeedbackActionType.MOVE_EXPERIENCE] = AIFeedbackActionType.MOVE_EXPERIENCE
    experience_id: str
    target_day_index: int = Field(ge=1)

    @field_validator("experience_id")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "experience_id")


class ChangePaceAction(BaseModel):
    type: Literal[AIFeedbackActionType.CHANGE_PACE] = AIFeedbackActionType.CHANGE_PACE
    pace: str

    @field_validator("pace")
    @classmethod
    def validate_pace_supported(cls, value: str) -> str:
        if value not in _SUPPORTED_PACE_VALUES:
            raise ValueError(
                f"pace must be one of {_SUPPORTED_PACE_VALUES}, got {value!r}."
            )
        return value


class AdjustInterestAction(BaseModel):
    type: Literal[AIFeedbackActionType.ADJUST_INTEREST] = AIFeedbackActionType.ADJUST_INTEREST
    category: str
    direction: Literal["more", "less"]

    @field_validator("category")
    @classmethod
    def validate_not_blank_and_safe(cls, value: str) -> str:
        stripped = _require_non_blank(value, "category")
        return _check_forbidden(stripped, "category")


class RegenerateDayAction(BaseModel):
    """Section 196.1 (Task 3): the typed carrier for a day-specific
    qualitative request (e.g. "Day 2 is too packed, make it more
    relaxed") that must NOT be widened into a global `ChangePaceAction`/
    `AdjustInterestAction`. `instruction` is required and structurally
    scoped to `day_index` -- never left only in free-text rationale, so
    a future Section 197 consumer can act on the target day without
    reparsing natural language (Task 6).
    """

    type: Literal[AIFeedbackActionType.REGENERATE_DAY] = AIFeedbackActionType.REGENERATE_DAY
    day_index: int = Field(ge=1)
    instruction: str

    @field_validator("instruction")
    @classmethod
    def validate_instruction_not_blank_and_safe(cls, value: str) -> str:
        stripped = _require_non_blank(value, "instruction")
        return _check_forbidden(stripped, "instruction")


class GeneralInstructionAction(BaseModel):
    type: Literal[AIFeedbackActionType.GENERAL_INSTRUCTION] = AIFeedbackActionType.GENERAL_INSTRUCTION
    note: str

    @field_validator("note")
    @classmethod
    def validate_not_blank_and_safe(cls, value: str) -> str:
        stripped = _require_non_blank(value, "note")
        return _check_forbidden(stripped, "note")


AIFeedbackAction = Annotated[
    Union[
        RemoveExperienceAction,
        MoveExperienceAction,
        ChangePaceAction,
        AdjustInterestAction,
        RegenerateDayAction,
        GeneralInstructionAction,
    ],
    Field(discriminator="type"),
]


class NewPlaceRequest(BaseModel):
    """Task 6/15: the ONLY representation of "the user wants a place not
    currently in the plan." Structurally cannot carry a provider id,
    coordinate, price, rating, opening hour, or availability claim --
    those fields simply do not exist on this model. `requires_provider_
    lookup` is always `True` -- restated as a real field (not just
    documentation) so a future consumer (Section 197) can never mistake
    this for an already-resolved place.
    """

    query: str
    requires_provider_lookup: Literal[True] = True
    note: str | None = None

    @field_validator("query")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        return _require_non_blank(value, "query")

    @field_validator("note")
    @classmethod
    def validate_note_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _check_forbidden(value, "note")


class AIFeedbackPreserveScope(BaseModel):
    """Task 9: explicit, structural preservation intent -- never left
    only in free-text rationale. Empty by default (nothing the user
    asked to keep unchanged).
    """

    day_indices: list[int] = Field(default_factory=list)
    experience_ids: list[str] = Field(default_factory=list)

    @field_validator("day_indices")
    @classmethod
    def validate_day_indices_unique(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("AIFeedbackPreserveScope.day_indices must not repeat a day_index.")
        return value

    @field_validator("experience_ids")
    @classmethod
    def validate_experience_ids_unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError(
                "AIFeedbackPreserveScope.experience_ids must not repeat an experience_id."
            )
        return value


class AIFeedbackScope(str, Enum):
    SINGLE_EXPERIENCE = "single_experience"
    SINGLE_DAY = "single_day"
    MULTIPLE_DAYS = "multiple_days"
    WHOLE_ITINERARY = "whole_itinerary"
    PREFERENCES_ONLY = "preferences_only"
    NEW_PLACE_REQUEST = "new_place_request"


class AIFeedbackInterpretationStatus(str, Enum):
    NOT_CONNECTED = "not_connected"
    COMPLETED = "completed"
    NEEDS_CLARIFICATION = "needs_clarification"
    REJECTED = "rejected"


class AIFeedbackClarification(BaseModel):
    reason: str
    possible_experience_ids: list[str] = Field(default_factory=list)

    @field_validator("reason")
    @classmethod
    def validate_reason_not_blank_and_safe(cls, value: str) -> str:
        stripped = _require_non_blank(value, "reason")
        return _check_forbidden(stripped, "reason")


class AIFeedbackInterpretationResult(BaseModel):
    """Base result shape every feedback-interpretation provider response
    must validate through before being accepted (Task 7). Enforces the
    same status-consistency contract every earlier AI-facing result model
    in this repo already uses:

    - `status="completed"` requires at least one action, new-place
      request, or non-empty preserve scope -- never a "completed" result
      that did nothing at all.
    - `status="needs_clarification"` requires a `clarification`, and --
      Task 23's conservative reading -- no executable actions/new-place
      requests anywhere in the same result (an unresolved ambiguity is
      never partially acted around).
    - `status="rejected"` requires at least one `blocked_reasons` entry.
    - `status="not_connected"` always carries empty `actions`/
      `new_place_requests` and `confidence=0.0`.
    """

    model_config = ConfigDict(protected_namespaces=())

    status: AIFeedbackInterpretationStatus
    scope: AIFeedbackScope | None = None
    actions: list[AIFeedbackAction] = Field(default_factory=list)
    new_place_requests: list[NewPlaceRequest] = Field(default_factory=list)
    preserve: AIFeedbackPreserveScope = Field(default_factory=AIFeedbackPreserveScope)
    clarification: AIFeedbackClarification | None = None
    summary: str | None = None
    blocked_reasons: list[str] = Field(default_factory=list)
    provider_name: str | None = None
    model_name: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("summary")
    @classmethod
    def validate_summary_safe(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _check_forbidden(value, "summary")

    @field_validator("blocked_reasons")
    @classmethod
    def validate_blocked_reasons_safe(cls, value: list[str]) -> list[str]:
        for item in value:
            _check_forbidden(item, "blocked_reasons")
        return value

    @model_validator(mode="after")
    def validate_status_consistency(self) -> "AIFeedbackInterpretationResult":
        if self.status == AIFeedbackInterpretationStatus.COMPLETED:
            if not self.actions and not self.new_place_requests and not (
                self.preserve.day_indices or self.preserve.experience_ids
            ):
                raise ValueError(
                    "completed AIFeedbackInterpretationResult must include at least one "
                    "action, new_place_request, or preserve entry."
                )
        if self.status == AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION:
            if self.clarification is None:
                raise ValueError(
                    "needs_clarification AIFeedbackInterpretationResult requires a clarification."
                )
            if self.actions or self.new_place_requests:
                raise ValueError(
                    "needs_clarification AIFeedbackInterpretationResult must not include any "
                    "executable actions or new_place_requests."
                )
        if self.status == AIFeedbackInterpretationStatus.REJECTED:
            if not self.blocked_reasons:
                raise ValueError(
                    "rejected AIFeedbackInterpretationResult requires at least one blocked_reason."
                )
        if self.status == AIFeedbackInterpretationStatus.NOT_CONNECTED:
            if self.actions or self.new_place_requests:
                raise ValueError(
                    "not_connected AIFeedbackInterpretationResult must have no actions or "
                    "new_place_requests."
                )
            if self.confidence != 0.0:
                raise ValueError(
                    "not_connected AIFeedbackInterpretationResult must have confidence=0.0."
                )
        return self


# Section 196.1 (Task 7): a lightweight, bounded, deterministic day-scope
# extraction -- NOT a general NLP parser. `_DAY_MENTION_PATTERN` finds each
# "day"/"days" occurrence, and `_DAY_MENTION_WINDOW` characters after it are
# scanned for bare integers, so "Days 2 and 3 are too busy" yields {2, 3}
# without attempting to parse arbitrary sentence structure. This is a
# defense-in-depth check behind the primary fix (Task 8's prompt/contract
# change) -- it exists to catch an otherwise schema-valid provider response
# that silently widens an explicitly narrow day-scoped request.
_DAY_MENTION_PATTERN = re.compile(r"\bdays?\b", re.IGNORECASE)
_DAY_NUMBER_PATTERN = re.compile(r"\d+")
_DAY_MENTION_WINDOW = 20

_WHOLE_ITINERARY_PHRASES: tuple[str, ...] = (
    "whole trip",
    "whole itinerary",
    "entire trip",
    "entire itinerary",
    "every day",
    "all days",
    "overall",
    "throughout the trip",
)


def _extract_mentioned_day_numbers(feedback_text: str) -> set[int]:
    mentioned: set[int] = set()
    for match in _DAY_MENTION_PATTERN.finditer(feedback_text):
        window = feedback_text[match.end() : match.end() + _DAY_MENTION_WINDOW]
        mentioned.update(int(n) for n in _DAY_NUMBER_PATTERN.findall(window))
    return mentioned


def _feedback_states_whole_itinerary(feedback_text: str) -> bool:
    lowered = feedback_text.lower()
    return any(phrase in lowered for phrase in _WHOLE_ITINERARY_PHRASES)


def validate_interpretation_against_request(
    request: AIFeedbackInterpretationRequest, result: AIFeedbackInterpretationResult
) -> list[str]:
    """Task 10/21/22's deterministic safety check. Pure, side-effect-free,
    and independently callable -- returns every violation found, never
    raises itself.

    Checks:
    - every `experience_id` referenced anywhere (actions, preserve,
      clarification) exists in `request.current_items` (Task 10/36 --
      never a fuzzy lookup, never an auto-created id).
    - every `day_index`/`target_day_index` referenced is within
      `1..request.trip_duration_days` (Task 21).
    - no `experience_id` is both removed and moved (Task 22/35).
    - no `day_index`/`experience_id` is both preserved and targeted by a
      `regenerate_day`/`move_experience`/`remove_experience` action
      (Task 9/22).
    - Section 196.1 (Task 7): if `request.feedback_text` explicitly names one
      or more valid day(s) and never explicitly states a whole-trip/every-day
      scope, `result` must not combine `scope=whole_itinerary` with a global
      `change_pace`/`adjust_interest` action -- an explicitly narrow day
      request must never silently widen to a global preference change.
    """
    violations: list[str] = []
    allowed_ids = request.allowed_experience_ids()

    removed_ids: set[str] = set()
    moved_ids: set[str] = set()
    targeted_day_indices: set[int] = set()
    targeted_experience_ids: set[str] = set()

    all_referenced_ids: set[str] = set()
    if result.clarification is not None:
        all_referenced_ids.update(result.clarification.possible_experience_ids)
    all_referenced_ids.update(result.preserve.experience_ids)

    for action in result.actions:
        if isinstance(action, RemoveExperienceAction):
            all_referenced_ids.add(action.experience_id)
            removed_ids.add(action.experience_id)
            targeted_experience_ids.add(action.experience_id)
        elif isinstance(action, MoveExperienceAction):
            all_referenced_ids.add(action.experience_id)
            moved_ids.add(action.experience_id)
            targeted_experience_ids.add(action.experience_id)
            if action.target_day_index > request.trip_duration_days:
                violations.append(
                    f"move_experience target_day_index {action.target_day_index} exceeds "
                    f"trip_duration_days ({request.trip_duration_days})."
                )
            targeted_day_indices.add(action.target_day_index)
        elif isinstance(action, RegenerateDayAction):
            if action.day_index > request.trip_duration_days:
                violations.append(
                    f"regenerate_day day_index {action.day_index} exceeds trip_duration_days "
                    f"({request.trip_duration_days})."
                )
            targeted_day_indices.add(action.day_index)

    for day_index in result.preserve.day_indices:
        if day_index > request.trip_duration_days:
            violations.append(
                f"preserve.day_indices contains {day_index}, which exceeds trip_duration_days "
                f"({request.trip_duration_days})."
            )

    for experience_id in all_referenced_ids:
        if experience_id not in allowed_ids:
            violations.append(
                f"experience_id {experience_id!r} is not in request.current_items."
            )

    for experience_id in removed_ids & moved_ids:
        violations.append(
            f"experience_id {experience_id!r} is both removed and moved -- contradictory."
        )

    for day_index in set(result.preserve.day_indices) & targeted_day_indices:
        violations.append(
            f"day_index {day_index} is both preserved and targeted by another action -- "
            "contradictory."
        )

    for experience_id in set(result.preserve.experience_ids) & targeted_experience_ids:
        violations.append(
            f"experience_id {experience_id!r} is both preserved and targeted by another "
            "action -- contradictory."
        )

    explicit_days = {
        day
        for day in _extract_mentioned_day_numbers(request.feedback_text)
        if 1 <= day <= request.trip_duration_days
    }
    if explicit_days and not _feedback_states_whole_itinerary(request.feedback_text):
        if result.scope == AIFeedbackScope.WHOLE_ITINERARY:
            global_action_types = [
                action.type.value
                for action in result.actions
                if isinstance(action, (ChangePaceAction, AdjustInterestAction))
            ]
            if global_action_types:
                violations.append(
                    f"feedback explicitly named day(s) {sorted(explicit_days)} without stating "
                    "a whole-trip/every-day scope, but the interpretation used "
                    f"scope=whole_itinerary with global action(s) {global_action_types} instead "
                    "of a day-specific regenerate_day action."
                )

    return violations
