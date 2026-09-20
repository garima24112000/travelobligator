from __future__ import annotations

from datetime import date
from typing import Any

from app.models.ai_feedback_interpretation import (
    AdjustInterestAction,
    AIFeedbackClarification,
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
    AIFeedbackPreserveScope,
    AIFeedbackScope,
    ChangePaceAction,
    GeneralInstructionAction,
    MoveExperienceAction,
    NewPlaceRequest,
    RegenerateDayAction,
    RemoveExperienceAction,
)
from app.models.common import GeoPoint
from app.models.planning_state import (
    DailyPlan,
    ExperiencePlan,
    ExperienceItem,
    PlanningStage,
    PlanningState,
    TravelGroupType,
    TripRequest,
    UserLock,
)
from app.models.targeted_regeneration_plan import (
    TargetedDayInstruction,
    TargetedNewPlaceLookup,
    TargetedRegenerationPlanStatus,
)
from app.services.targeted_regeneration_plan_builder import TargetedRegenerationPlanBuilder

# Tests for the Section 197A targeted-regeneration-plan compiler
# (docs/14_backend_architecture.md, following section 147). No provider/
# LLM call anywhere in this file -- `TargetedRegenerationPlanBuilder` is
# purely deterministic, and every `PlanningState`/interpretation fixture
# is constructed directly.


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Lisbon, Portugal",
        "start_date": "2026-09-10",
        "end_date": "2026-09-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
        "interests": ["history"],
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _experience(
    experience_id: str,
    name: str,
    day_number: int,
    *,
    provider_place_id: str | None = None,
    provider_source: str | None = None,
    coordinates: GeoPoint | None = None,
) -> ExperienceItem:
    return ExperienceItem(
        experience_id=experience_id,
        name=name,
        category="attraction",
        day_number=day_number,
        stop_order=1,
        provider_place_id=provider_place_id,
        provider_source=provider_source,
        coordinates=coordinates,
    )


def _planning_state(*, with_experience_plan: bool = True) -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request())
    if not with_experience_plan:
        return planning_state

    day1 = DailyPlan(
        day_number=1,
        date=date(2026, 9, 10),
        experiences=[_experience("exp_A", "Museum A", 1), _experience("exp_B", "Park B", 1)],
    )
    day2 = DailyPlan(
        day_number=2,
        date=date(2026, 9, 11),
        experiences=[
            _experience(
                "exp_C",
                "Torre de Belem",
                2,
                provider_place_id="way/24341353",
                provider_source="openstreetmap_places",
                coordinates=GeoPoint(lat=38.6916, lng=-9.2160),
            ),
            _experience("exp_D", "Cafe D", 2),
        ],
    )
    day3 = DailyPlan(
        day_number=3,
        date=date(2026, 9, 12),
        experiences=[_experience("exp_E", "Viewpoint E", 3)],
    )
    planning_state.experience_plan = ExperiencePlan(daily_plans=[day1, day2, day3])
    return planning_state


def _completed(**overrides: Any) -> AIFeedbackInterpretationResult:
    fields: dict[str, Any] = {
        "status": AIFeedbackInterpretationStatus.COMPLETED,
        "scope": AIFeedbackScope.SINGLE_EXPERIENCE,
        "actions": [RemoveExperienceAction(experience_id="exp_C")],
        "confidence": 0.9,
    }
    fields.update(overrides)
    return AIFeedbackInterpretationResult(**fields)


def _builder() -> TargetedRegenerationPlanBuilder:
    return TargetedRegenerationPlanBuilder()


# ---------------------------------------------------------------------------
# Task 26: remove.
# ---------------------------------------------------------------------------


def test_remove_experience_compiles_deterministic_plan() -> None:
    planning_state = _planning_state()
    interpretation = _completed(actions=[RemoveExperienceAction(experience_id="exp_C")])

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.READY
    assert plan.affected_day_indices == [2]
    assert plan.preserved_day_indices == [1, 3]
    assert [r.experience_id for r in plan.experience_removals] == ["exp_C"]
    assert plan.experience_removals[0].day_index == 2
    assert plan.experience_removals[0].candidate_id == "openstreetmap_places:way/24341353"
    assert plan.requires_provider_discovery is False
    assert plan.requires_ai_itinerary_reasoning is False
    assert plan.deterministic_edit_possible is True
    assert set(plan.required_stages) == {PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION}


# ---------------------------------------------------------------------------
# Task 27: move.
# ---------------------------------------------------------------------------


def test_move_experience_preserves_identity_and_affects_both_days() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        scope=AIFeedbackScope.SINGLE_EXPERIENCE,
        actions=[MoveExperienceAction(experience_id="exp_C", target_day_index=1)],
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.READY
    assert plan.affected_day_indices == [1, 2]
    assert plan.preserved_day_indices == [3]
    move = plan.experience_moves[0]
    assert move.experience_id == "exp_C"
    assert move.source_day_index == 2
    assert move.target_day_index == 1
    assert move.candidate_id == "openstreetmap_places:way/24341353"
    assert move.coordinates is not None
    assert move.coordinates.lat == 38.6916
    assert plan.requires_provider_discovery is False
    assert plan.requires_ai_itinerary_reasoning is False
    assert plan.deterministic_edit_possible is True


# ---------------------------------------------------------------------------
# Task 28: day-specific regeneration.
# ---------------------------------------------------------------------------


def test_regenerate_day_carries_instruction_without_global_pace_mutation() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        scope=AIFeedbackScope.SINGLE_DAY,
        actions=[RegenerateDayAction(day_index=2, instruction="Make it more relaxed.")],
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.READY
    assert plan.affected_day_indices == [2]
    assert plan.preserved_day_indices == [1, 3]
    assert plan.day_instructions == [TargetedDayInstruction(day_index=2, instruction="Make it more relaxed.")]
    assert plan.traveler_profile_mutation is None
    assert plan.requires_ai_itinerary_reasoning is True
    assert plan.deterministic_edit_possible is False


# ---------------------------------------------------------------------------
# Task 29: global pace.
# ---------------------------------------------------------------------------


def test_global_pace_change_affects_all_days_and_mutates_profile() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        scope=AIFeedbackScope.WHOLE_ITINERARY,
        actions=[ChangePaceAction(pace="relaxed")],
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.READY
    assert plan.affected_day_indices == [1, 2, 3]
    assert plan.preserved_day_indices == []
    assert plan.traveler_profile_mutation is not None
    assert plan.traveler_profile_mutation.pace == "relaxed"
    assert plan.requires_ai_itinerary_reasoning is True
    assert PlanningStage.TRAVELER_PROFILE in plan.required_stages


# ---------------------------------------------------------------------------
# Global interest (Task 9's counterpart to Task 29).
# ---------------------------------------------------------------------------


def test_global_interest_change_mutates_profile_interests() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        scope=AIFeedbackScope.PREFERENCES_ONLY,
        actions=[AdjustInterestAction(category="food", direction="more")],
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.READY
    assert plan.traveler_profile_mutation.interests_to_add == ["food"]
    assert plan.traveler_profile_mutation.interests_to_remove == []
    assert PlanningStage.DESTINATION_CONTEXT in plan.required_stages


# ---------------------------------------------------------------------------
# Task 30: new place.
# ---------------------------------------------------------------------------


def test_new_place_request_requires_lookup_without_fabricated_identity() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        scope=AIFeedbackScope.NEW_PLACE_REQUEST,
        actions=[],
        new_place_requests=[NewPlaceRequest(query="Sintra")],
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.READY
    assert plan.requires_provider_discovery is True
    assert plan.new_place_lookups == [TargetedNewPlaceLookup(query="Sintra", note=None)]
    dumped = plan.model_dump()
    for forbidden in ("provider_place_id", "candidate_id", "coordinates", "price", "rating"):
        assert forbidden not in dumped["new_place_lookups"][0]


# ---------------------------------------------------------------------------
# Task 31: explicit preservation.
# ---------------------------------------------------------------------------


def test_explicit_preserve_day_is_carried_into_plan() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        scope=AIFeedbackScope.SINGLE_DAY,
        actions=[RegenerateDayAction(day_index=2, instruction="Make it less packed.")],
        preserve=AIFeedbackPreserveScope(day_indices=[1]),
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.READY
    assert plan.affected_day_indices == [2]
    assert 1 in plan.preserved_day_indices


# ---------------------------------------------------------------------------
# Task 32: implicit preservation.
# ---------------------------------------------------------------------------


def test_implicit_preservation_covers_unaffected_days() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        scope=AIFeedbackScope.SINGLE_DAY,
        actions=[RegenerateDayAction(day_index=2, instruction="Make it less packed.")],
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.preserved_day_indices == [1, 3]


# ---------------------------------------------------------------------------
# Task 33: conflict defense.
# ---------------------------------------------------------------------------


def test_remove_and_preserve_same_experience_is_blocked() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        actions=[RemoveExperienceAction(experience_id="exp_C")],
        preserve=AIFeedbackPreserveScope(experience_ids=["exp_C"]),
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.BLOCKED
    assert any("both preserved and targeted" in reason for reason in plan.block_reasons)


def test_regenerate_and_preserve_same_day_is_blocked() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        actions=[RegenerateDayAction(day_index=2, instruction="Make it more relaxed.")],
        preserve=AIFeedbackPreserveScope(day_indices=[2]),
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.BLOCKED


def test_move_target_day_preserved_is_blocked() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        actions=[MoveExperienceAction(experience_id="exp_C", target_day_index=1)],
        preserve=AIFeedbackPreserveScope(day_indices=[1]),
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.BLOCKED


# ---------------------------------------------------------------------------
# Task 34: unknown/stale experience reference.
# ---------------------------------------------------------------------------


def test_unknown_experience_id_is_blocked_not_name_matched() -> None:
    planning_state = _planning_state()
    interpretation = _completed(actions=[RemoveExperienceAction(experience_id="exp_ZZZ_stale")])

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.BLOCKED
    assert any("exp_ZZZ_stale" in reason for reason in plan.block_reasons)
    assert plan.experience_removals == []


def test_invalid_day_index_beyond_current_trip_length_is_blocked() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        actions=[RegenerateDayAction(day_index=99, instruction="Make it more relaxed.")]
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.BLOCKED


# ---------------------------------------------------------------------------
# Task 35: source-version metadata.
# ---------------------------------------------------------------------------


def test_plan_carries_current_source_version() -> None:
    planning_state = _planning_state()
    planning_state.metadata.current_version = "v3"
    interpretation = _completed(actions=[RemoveExperienceAction(experience_id="exp_C")])

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.source_version == "v3"


# ---------------------------------------------------------------------------
# Task 36: clarification propagation.
# ---------------------------------------------------------------------------


def test_needs_clarification_produces_non_executable_plan() -> None:
    planning_state = _planning_state()
    interpretation = AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION,
        clarification=AIFeedbackClarification(
            reason="Two museums are scheduled.", possible_experience_ids=["exp_A", "exp_B"]
        ),
        confidence=0.4,
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.NEEDS_CLARIFICATION
    assert plan.is_executable() is False
    assert plan.clarification_reason == "Two museums are scheduled."
    assert plan.clarification_possible_experience_ids == ["exp_A", "exp_B"]
    assert plan.required_stages == []
    assert plan.experience_removals == []


# ---------------------------------------------------------------------------
# Task 37: rejected / not_connected interpretation.
# ---------------------------------------------------------------------------


def test_rejected_interpretation_produces_blocked_plan() -> None:
    planning_state = _planning_state()
    interpretation = AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.REJECTED,
        blocked_reasons=["The model reported it could not interpret this feedback."],
        confidence=0.0,
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.BLOCKED
    assert plan.is_executable() is False


def test_not_connected_interpretation_produces_blocked_plan() -> None:
    planning_state = _planning_state()
    interpretation = AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.NOT_CONNECTED,
        blocked_reasons=["AI feedback interpreter is disabled."],
        confidence=0.0,
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.BLOCKED
    assert plan.is_executable() is False


# ---------------------------------------------------------------------------
# Task 12: existing trip locks still block regeneration globally.
# ---------------------------------------------------------------------------


def test_active_lock_blocks_targeted_plan_even_with_valid_intent() -> None:
    planning_state = _planning_state()
    planning_state.user_locks = [
        UserLock(locked_item_type="experience", locked_item_id="exp_A", is_active=True)
    ]
    interpretation = _completed(actions=[RemoveExperienceAction(experience_id="exp_C")])

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.BLOCKED
    assert any("active lock" in reason.lower() for reason in plan.block_reasons)


def test_inactive_lock_does_not_block() -> None:
    planning_state = _planning_state()
    planning_state.user_locks = [
        UserLock(locked_item_type="experience", locked_item_id="exp_A", is_active=False)
    ]
    interpretation = _completed(actions=[RemoveExperienceAction(experience_id="exp_C")])

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.READY


# ---------------------------------------------------------------------------
# Task 23: GeneralInstructionAction cannot be compiled deterministically.
# ---------------------------------------------------------------------------


def test_general_instruction_action_is_unsupported() -> None:
    planning_state = _planning_state()
    interpretation = _completed(actions=[GeneralInstructionAction(note="Please make it nicer.")])

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.UNSUPPORTED
    assert plan.unsupported_actions


def test_preserve_only_interpretation_is_unsupported() -> None:
    planning_state = _planning_state()
    interpretation = AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.COMPLETED,
        scope=AIFeedbackScope.SINGLE_DAY,
        actions=[],
        preserve=AIFeedbackPreserveScope(day_indices=[1]),
        confidence=0.5,
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.UNSUPPORTED


def test_no_experience_plan_is_blocked() -> None:
    planning_state = _planning_state(with_experience_plan=False)
    interpretation = _completed(actions=[RemoveExperienceAction(experience_id="exp_C")])

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.BLOCKED


# ---------------------------------------------------------------------------
# Task 38: read-only proof.
# ---------------------------------------------------------------------------


def test_builder_never_mutates_planning_state_or_interpretation() -> None:
    planning_state = _planning_state()
    planning_state.user_locks = [
        UserLock(locked_item_type="experience", locked_item_id="exp_A", is_active=False)
    ]
    before_state = planning_state.model_copy(deep=True)
    interpretation = _completed(
        actions=[MoveExperienceAction(experience_id="exp_C", target_day_index=1)],
        preserve=AIFeedbackPreserveScope(day_indices=[3]),
    )
    before_interpretation = interpretation.model_copy(deep=True)

    _builder().build_plan(planning_state, interpretation)

    assert planning_state.model_dump() == before_state.model_dump()
    assert interpretation.model_dump() == before_interpretation.model_dump()


# ---------------------------------------------------------------------------
# Multiple explicit days (mirrors Section 196.1's own multi-day case).
# ---------------------------------------------------------------------------


def test_multiple_regenerate_day_actions_affect_only_named_days() -> None:
    planning_state = _planning_state()
    interpretation = _completed(
        scope=AIFeedbackScope.MULTIPLE_DAYS,
        actions=[
            RegenerateDayAction(day_index=2, instruction="Less busy."),
            RegenerateDayAction(day_index=3, instruction="Less busy."),
        ],
    )

    plan = _builder().build_plan(planning_state, interpretation)

    assert plan.status == TargetedRegenerationPlanStatus.READY
    assert plan.affected_day_indices == [2, 3]
    assert plan.preserved_day_indices == [1]
