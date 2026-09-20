from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.models.ai_feedback_interpretation import AIFeedbackScope
from app.models.common import GeoPoint
from app.models.planning_state import PlanningStage, TripPace

# Section 197A (docs/14_backend_architecture.md, following section 147):
# the deterministic compiler boundary between the Section 196 AI feedback
# interpreter and any future execution step (Section 197B). This module
# defines ONLY the plan contract -- what would need to change, what must
# stay untouched, and whether new provider lookup is required -- never
# the execution itself. Unlike the leaf AI-contract modules
# (`ai_feedback_interpretation.py`, `ai_itinerary_reasoning.py`, ...),
# this module DOES import `app.models.planning_state` -- it is a
# service-layer compiler that reasons about the CURRENT plan's real
# structure (day numbers, experience ids, provider identity), not a
# leaf contract another module imports from.
#
# Core invariant (Task 25): this compiler is deterministic. It never
# calls an LLM, never calls a provider, never mutates `PlanningState`.
# All the "intelligence" already happened in Section 196 -- this module
# is policy/compilation only.


class TargetedRegenerationPlanStatus(str, Enum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


class TargetedExperienceRemoval(BaseModel):
    """A single existing item to remove, identified only by its stable
    `experience_id` -- never re-resolved by name (Task 16/17)."""

    experience_id: str
    day_index: int
    candidate_id: str | None = None


class TargetedExperienceMove(BaseModel):
    """A single existing item to relocate. `candidate_id`/`coordinates`
    are copied verbatim from the item's current, already-provider-backed
    state -- never reconstructed by name (Task 6/17). `coordinates` is
    structural identity (where the real place already is), not a
    provider fact like price/rating that could go stale.
    """

    experience_id: str
    source_day_index: int
    target_day_index: int
    candidate_id: str | None = None
    coordinates: GeoPoint | None = None


class TargetedDayInstruction(BaseModel):
    """A day-scoped qualitative instruction, carried forward verbatim
    from a `RegenerateDayAction` -- never reinterpreted globally (Task
    7)."""

    day_index: int
    instruction: str


class TravelerProfileMutation(BaseModel):
    """Only fields that actually exist on `TravelerProfile`/`TripRequest`
    (Task 9's audit) -- `pace`, `interests`. No invented field."""

    pace: TripPace | None = None
    interests_to_add: list[str] = Field(default_factory=list)
    interests_to_remove: list[str] = Field(default_factory=list)


class TargetedNewPlaceLookup(BaseModel):
    """Task 10: structurally cannot carry a candidate id, provider place
    id, coordinate, quality score, or category assumption -- those
    fields simply do not exist here. Section 197B is the only step that
    may ground this into a real place."""

    query: str
    note: str | None = None


class TargetedRegenerationPlan(BaseModel):
    """The Section 197A compiler's sole output (Task 3). Captures what
    would need to change and what must stay untouched -- never executes
    anything itself, and never carries an authoritative provider fact
    (price/rating/availability/etc.) of its own; identity references
    (experience_id/candidate_id/coordinates) are copied from the
    current, already-provider-backed state, never invented here.
    """

    model_config = ConfigDict(protected_namespaces=())

    trip_id: str
    source_version: str | None = None
    interpretation_scope: AIFeedbackScope | None = None

    status: TargetedRegenerationPlanStatus

    affected_day_indices: list[int] = Field(default_factory=list)
    preserved_day_indices: list[int] = Field(default_factory=list)
    affected_experience_ids: list[str] = Field(default_factory=list)
    preserved_experience_ids: list[str] = Field(default_factory=list)

    experience_removals: list[TargetedExperienceRemoval] = Field(default_factory=list)
    experience_moves: list[TargetedExperienceMove] = Field(default_factory=list)
    day_instructions: list[TargetedDayInstruction] = Field(default_factory=list)
    traveler_profile_mutation: TravelerProfileMutation | None = None
    new_place_lookups: list[TargetedNewPlaceLookup] = Field(default_factory=list)

    # Task 18/19: the coarse-but-real `PlanningStage` vocabulary that
    # `PlanningOrchestrator.rerun_affected_stages` already consumes --
    # deliberately never TRIP_STRATEGY/STAY_TRANSPORT for any feedback
    # action type this compiler currently supports (Task 19: no
    # accommodation/flight rerun for an item-level or day-level change).
    required_stages: list[PlanningStage] = Field(default_factory=list)

    # Task 20: whether LLM #2 itinerary reasoning must actually rerun,
    # vs. whether the edit is simple enough to apply deterministically
    # (a plain remove/move never needs a fresh reasoning pass; a
    # qualitative day regeneration, a profile mutation, or a new-place
    # insertion does).
    requires_ai_itinerary_reasoning: bool = False
    deterministic_edit_possible: bool = False
    requires_provider_discovery: bool = False
    requires_routing_rerun: bool = False
    requires_validation_rerun: bool = False
    requires_narrator_rerun: bool = False
    would_create_version: bool = False

    unsupported_actions: list[str] = Field(default_factory=list)
    clarification_reason: str | None = None
    clarification_possible_experience_ids: list[str] = Field(default_factory=list)
    block_reasons: list[str] = Field(default_factory=list)

    def is_executable(self) -> bool:
        return self.status == TargetedRegenerationPlanStatus.READY
