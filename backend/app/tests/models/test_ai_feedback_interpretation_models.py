from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.ai_feedback_interpretation import (
    AdjustInterestAction,
    AIFeedbackClarification,
    AIFeedbackInterpretationRequest,
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
    AIFeedbackItineraryItemContext,
    AIFeedbackPreserveScope,
    AIFeedbackScope,
    ChangePaceAction,
    GeneralInstructionAction,
    MoveExperienceAction,
    NewPlaceRequest,
    RegenerateDayAction,
    RemoveExperienceAction,
    validate_interpretation_against_request,
)
from app.models.ai_itinerary_reasoning import TravelerContextSummary

# Tests for the Section 196 AI feedback-interpretation contract
# (docs/14_backend_architecture.md, following section 146). No provider/
# LLM call anywhere in this file -- every request/result is constructed
# directly, exactly like every other AI-contract model test suite in
# this repo.


def _item(experience_id: str, name: str, day_index: int, category: str = "attraction") -> AIFeedbackItineraryItemContext:
    return AIFeedbackItineraryItemContext(
        experience_id=experience_id, name=name, category=category, day_index=day_index
    )


_ITEMS = [
    _item("exp_a", "Torre de Belem", 2),
    _item("exp_b", "Castelo de Sao Jorge", 1),
    _item("exp_c", "Museu Nacional", 3),
]


def _request(**overrides: object) -> AIFeedbackInterpretationRequest:
    fields: dict[str, object] = {
        "trip_id": "trip_001",
        "destination_name": "Lisbon, Portugal",
        "start_date": "2026-09-10",
        "end_date": "2026-09-12",
        "trip_duration_days": 3,
        "traveler_context": TravelerContextSummary(
            travelers_count=2, travel_group_type="couple", pace="balanced"
        ),
        "feedback_text": "Remove Belem Tower.",
        "current_items": _ITEMS,
    }
    fields.update(overrides)
    return AIFeedbackInterpretationRequest(**fields)


def _completed(**overrides: object) -> AIFeedbackInterpretationResult:
    fields: dict[str, object] = {
        "status": AIFeedbackInterpretationStatus.COMPLETED,
        "scope": AIFeedbackScope.SINGLE_EXPERIENCE,
        "actions": [RemoveExperienceAction(experience_id="exp_a")],
        "summary": "Removing Torre de Belem.",
        "confidence": 0.8,
    }
    fields.update(overrides)
    return AIFeedbackInterpretationResult(**fields)


# ---------------------------------------------------------------------------
# Request structural safety.
# ---------------------------------------------------------------------------


def test_request_builds_successfully() -> None:
    request = _request()
    assert request.allowed_experience_ids() == {"exp_a", "exp_b", "exp_c"}


def test_request_rejects_duplicate_experience_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        _request(current_items=[_item("exp_a", "A", 1), _item("exp_a", "B", 2)])


def test_request_rejects_blank_feedback_text() -> None:
    with pytest.raises(ValidationError):
        _request(feedback_text="   ")


# ---------------------------------------------------------------------------
# Result status consistency.
# ---------------------------------------------------------------------------


def test_completed_result_requires_some_content() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        AIFeedbackInterpretationResult(status=AIFeedbackInterpretationStatus.COMPLETED, confidence=0.5)


def test_completed_with_only_preserve_is_valid() -> None:
    result = AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.COMPLETED,
        preserve=AIFeedbackPreserveScope(day_indices=[1]),
        summary="Keeping day 1 unchanged.",
        confidence=0.6,
    )
    assert result.preserve.day_indices == [1]


def test_needs_clarification_requires_clarification_object() -> None:
    with pytest.raises(ValidationError, match="requires a clarification"):
        AIFeedbackInterpretationResult(status=AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION, confidence=0.5)


def test_needs_clarification_must_have_no_actions() -> None:
    with pytest.raises(ValidationError, match="must not include any executable"):
        AIFeedbackInterpretationResult(
            status=AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION,
            actions=[RemoveExperienceAction(experience_id="exp_a")],
            clarification=AIFeedbackClarification(reason="Ambiguous.", possible_experience_ids=["exp_a", "exp_c"]),
            confidence=0.5,
        )


def test_rejected_requires_blocked_reason() -> None:
    with pytest.raises(ValidationError, match="at least one blocked_reason"):
        AIFeedbackInterpretationResult(status=AIFeedbackInterpretationStatus.REJECTED, confidence=0.0)


def test_not_connected_must_have_no_actions() -> None:
    with pytest.raises(ValidationError, match="must have no actions"):
        AIFeedbackInterpretationResult(
            status=AIFeedbackInterpretationStatus.NOT_CONNECTED,
            actions=[RemoveExperienceAction(experience_id="exp_a")],
            confidence=0.0,
        )


def test_change_pace_rejects_unsupported_value() -> None:
    with pytest.raises(ValidationError, match="pace must be one of"):
        ChangePaceAction(pace="extremely relaxed")


@pytest.mark.parametrize("field", ["summary"])
def test_forbidden_factual_claim_is_rejected(field: str) -> None:
    with pytest.raises(ValidationError, match="forbidden pattern"):
        _completed(**{field: "This is a cheap and highly rated change."})


# ---------------------------------------------------------------------------
# Task 27: remove existing experience.
# ---------------------------------------------------------------------------


def test_valid_remove_existing_experience_has_no_violations() -> None:
    request = _request()
    result = _completed()
    assert validate_interpretation_against_request(request, result) == []


# ---------------------------------------------------------------------------
# Task 28: move experience, valid day.
# ---------------------------------------------------------------------------


def test_valid_move_experience_has_no_violations() -> None:
    request = _request(feedback_text="Move the castle to day 2.")
    result = _completed(
        scope=AIFeedbackScope.SINGLE_EXPERIENCE,
        actions=[MoveExperienceAction(experience_id="exp_b", target_day_index=2)],
    )
    assert validate_interpretation_against_request(request, result) == []


def test_move_experience_to_out_of_range_day_is_rejected() -> None:
    request = _request(feedback_text="Move the castle to day 9.")
    result = _completed(actions=[MoveExperienceAction(experience_id="exp_b", target_day_index=9)])
    violations = validate_interpretation_against_request(request, result)
    assert any("exceeds trip_duration_days" in v for v in violations)


# ---------------------------------------------------------------------------
# Task 29: preserve day + regenerate a different day -- no conflict.
# ---------------------------------------------------------------------------


def test_preserve_day_and_regenerate_different_day_has_no_conflict() -> None:
    request = _request(feedback_text="Redo day 2 but don't change day 1.")
    result = _completed(
        scope=AIFeedbackScope.SINGLE_DAY,
        actions=[RegenerateDayAction(day_index=2, instruction="Make it less packed.")],
        preserve=AIFeedbackPreserveScope(day_indices=[1]),
    )
    assert validate_interpretation_against_request(request, result) == []


# ---------------------------------------------------------------------------
# Task 30: preference change maps only to supported fields.
# ---------------------------------------------------------------------------


def test_preference_change_only_uses_supported_action_types() -> None:
    request = _request(feedback_text="Make the trip more relaxed and add more food.")
    result = _completed(
        scope=AIFeedbackScope.PREFERENCES_ONLY,
        actions=[
            ChangePaceAction(pace="relaxed"),
            AdjustInterestAction(category="food", direction="more"),
        ],
    )
    assert validate_interpretation_against_request(request, result) == []
    assert {type(a).__name__ for a in result.actions} == {"ChangePaceAction", "AdjustInterestAction"}


# ---------------------------------------------------------------------------
# Task 31: new-place request -- no fabricated identity possible.
# ---------------------------------------------------------------------------


def test_new_place_request_has_no_factual_fields() -> None:
    request = _request(feedback_text="I also want to visit Sintra.")
    result = _completed(
        scope=AIFeedbackScope.NEW_PLACE_REQUEST,
        actions=[],
        new_place_requests=[NewPlaceRequest(query="Sintra")],
    )
    assert validate_interpretation_against_request(request, result) == []
    dumped = result.new_place_requests[0].model_dump()
    forbidden_fields = {"provider_place_id", "provider_source", "coordinates", "price", "rating", "opening_hours"}
    assert forbidden_fields.isdisjoint(dumped.keys())
    assert result.new_place_requests[0].requires_provider_lookup is True


# ---------------------------------------------------------------------------
# Task 32/36: nonexistent/hallucinated reference is rejected.
# ---------------------------------------------------------------------------


def test_removal_of_nonexistent_place_is_rejected() -> None:
    request = _request(feedback_text="Remove Eiffel Tower.")
    result = _completed(actions=[RemoveExperienceAction(experience_id="made-up-eiffel-tower")])
    violations = validate_interpretation_against_request(request, result)
    assert any("made-up-eiffel-tower" in v for v in violations)


# ---------------------------------------------------------------------------
# Task 33: ambiguous reference -> needs_clarification, no random pick.
# ---------------------------------------------------------------------------


def test_ambiguous_reference_uses_needs_clarification() -> None:
    request = _request(
        current_items=[_item("exp_m1", "City Museum", 1), _item("exp_m2", "Art Museum", 2)],
        feedback_text="Remove the museum.",
    )
    result = AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION,
        clarification=AIFeedbackClarification(
            reason="Two museums are currently scheduled.", possible_experience_ids=["exp_m1", "exp_m2"]
        ),
        confidence=0.4,
    )
    assert validate_interpretation_against_request(request, result) == []
    assert result.actions == []


# ---------------------------------------------------------------------------
# Task 34: user-stated factual claim is rationale, never a provider fact.
# ---------------------------------------------------------------------------


def test_user_factual_claim_produces_only_a_remove_action() -> None:
    request = _request(feedback_text="Remove exp_a because it is closed.")
    result = _completed(actions=[RemoveExperienceAction(experience_id="exp_a")])
    assert validate_interpretation_against_request(request, result) == []
    dumped = result.model_dump()
    assert "opening_status" not in dumped
    assert "opening_hours" not in dumped


# ---------------------------------------------------------------------------
# Task 35: conflicting actions are rejected.
# ---------------------------------------------------------------------------


def test_remove_and_move_same_experience_is_rejected() -> None:
    request = _request()
    result = _completed(
        actions=[
            RemoveExperienceAction(experience_id="exp_a"),
            MoveExperienceAction(experience_id="exp_a", target_day_index=1),
        ]
    )
    violations = validate_interpretation_against_request(request, result)
    assert any("both removed and moved" in v for v in violations)


def test_preserve_day_and_regenerate_same_day_is_rejected() -> None:
    request = _request(feedback_text="Redo day 2 but also don't touch day 2.")
    result = _completed(
        actions=[RegenerateDayAction(day_index=2, instruction="Make it less packed.")],
        preserve=AIFeedbackPreserveScope(day_indices=[2]),
    )
    violations = validate_interpretation_against_request(request, result)
    assert any("both preserved and targeted" in v for v in violations)


# ---------------------------------------------------------------------------
# Section 196.1: explicit day scope must not silently widen to
# whole_itinerary/a global pace or interest change.
# ---------------------------------------------------------------------------


def test_regenerate_day_action_requires_non_blank_instruction() -> None:
    with pytest.raises(ValidationError):
        RegenerateDayAction(day_index=2, instruction="   ")


def test_regenerate_day_action_rejects_forbidden_pattern_in_instruction() -> None:
    with pytest.raises(ValidationError, match="forbidden pattern"):
        RegenerateDayAction(day_index=2, instruction="This place is the best, guaranteed.")


def test_day_specific_pace_complaint_scoped_to_day_passes() -> None:
    request = _request(feedback_text="Day 2 is too packed. Make it more relaxed.")
    result = _completed(
        scope=AIFeedbackScope.SINGLE_DAY,
        actions=[RegenerateDayAction(day_index=2, instruction="Make it more relaxed, less packed.")],
    )
    assert validate_interpretation_against_request(request, result) == []


def test_day_specific_pace_complaint_widened_to_whole_itinerary_is_rejected() -> None:
    request = _request(feedback_text="Day 2 is too packed. Make it more relaxed.")
    result = _completed(
        scope=AIFeedbackScope.WHOLE_ITINERARY,
        actions=[ChangePaceAction(pace="relaxed")],
    )
    violations = validate_interpretation_against_request(request, result)
    assert any("explicitly named day(s)" in v for v in violations)


def test_day_specific_interest_complaint_widened_to_global_is_rejected() -> None:
    request = _request(feedback_text="Day 3 needs more food.")
    result = _completed(
        scope=AIFeedbackScope.WHOLE_ITINERARY,
        actions=[AdjustInterestAction(category="food", direction="more")],
    )
    violations = validate_interpretation_against_request(request, result)
    assert any("explicitly named day(s)" in v for v in violations)


def test_genuinely_global_pace_request_stays_global() -> None:
    request = _request(feedback_text="Make the whole trip more relaxed.")
    result = _completed(
        scope=AIFeedbackScope.WHOLE_ITINERARY,
        actions=[ChangePaceAction(pace="relaxed")],
    )
    assert validate_interpretation_against_request(request, result) == []


def test_genuinely_global_interest_request_stays_global() -> None:
    request = _request(feedback_text="I want more food throughout the trip.")
    result = _completed(
        scope=AIFeedbackScope.PREFERENCES_ONLY,
        actions=[AdjustInterestAction(category="food", direction="more")],
    )
    assert validate_interpretation_against_request(request, result) == []


def test_multiple_explicit_days_do_not_widen_to_day_one() -> None:
    request = _request(feedback_text="Days 2 and 3 are too busy.")
    result = _completed(
        scope=AIFeedbackScope.MULTIPLE_DAYS,
        actions=[
            RegenerateDayAction(day_index=2, instruction="Make it less busy."),
            RegenerateDayAction(day_index=3, instruction="Make it less busy."),
        ],
    )
    assert validate_interpretation_against_request(request, result) == []
    targeted_days = {a.day_index for a in result.actions if isinstance(a, RegenerateDayAction)}
    assert targeted_days == {2, 3}
    assert 1 not in targeted_days


def test_invalid_day_reference_is_rejected() -> None:
    request = _request(feedback_text="Make day 7 more relaxed.")
    result = _completed(
        scope=AIFeedbackScope.SINGLE_DAY,
        actions=[RegenerateDayAction(day_index=7, instruction="Make it more relaxed.")],
    )
    violations = validate_interpretation_against_request(request, result)
    assert any("exceeds trip_duration_days" in v for v in violations)


def test_preserve_experience_and_remove_same_experience_is_rejected() -> None:
    request = _request()
    result = _completed(
        actions=[RemoveExperienceAction(experience_id="exp_a")],
        preserve=AIFeedbackPreserveScope(experience_ids=["exp_a"]),
    )
    violations = validate_interpretation_against_request(request, result)
    assert any("both preserved and targeted" in v for v in violations)


# ---------------------------------------------------------------------------
# General instruction (deferred/unsupported catch-all) is honest, not
# pretended-actionable.
# ---------------------------------------------------------------------------


def test_general_instruction_action_carries_a_note_only() -> None:
    action = GeneralInstructionAction(note="User wants a completely different theme for day 3.")
    assert action.type.value == "general_instruction"
    assert set(action.model_dump().keys()) == {"type", "note"}
