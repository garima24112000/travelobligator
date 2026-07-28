from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.ai_reasoning import (
    AIReasoningGuardrailReport,
    AIReasoningRequest,
    AIReasoningResult,
    AIReasoningStatus,
    AIReasoningTask,
    EvidenceRef,
    ExperiencePlanReasoningResult,
    FeedbackInterpretationResult,
    StayAreaReasoningResult,
    TravelerPreferenceInterpretationResult,
    TripStrategyReasoningResult,
    UnavailableConstraint,
    ValidationReasoningResult,
)

_FORBIDDEN_MODEL_FIELD_NAMES = {
    "price",
    "rating",
    "opening_hours",
    "booking_url",
    "route_time",
    "safety_score",
    "restaurant_name",
    "hotel_name",
    "attraction_name",
}

_TASK_SPECIFIC_RESULT_MODELS = (
    TravelerPreferenceInterpretationResult,
    TripStrategyReasoningResult,
    StayAreaReasoningResult,
    ExperiencePlanReasoningResult,
    ValidationReasoningResult,
    FeedbackInterpretationResult,
)


def _not_connected_base(task: AIReasoningTask = AIReasoningTask.FEEDBACK_INTERPRETATION) -> AIReasoningResult:
    return AIReasoningResult(
        task=task,
        status=AIReasoningStatus.NOT_CONNECTED,
        summary="No AI reasoning provider is connected yet.",
        guardrail_report=AIReasoningGuardrailReport(
            passed=False, blocked_reasons=["No AI reasoning provider is connected yet."]
        ),
        confidence=0.0,
    )


def _evidence_ref() -> EvidenceRef:
    return EvidenceRef(
        source_section="feedback_history",
        source_field="feedback_text",
        source_id="feedback_001",
        claim="The traveler asked for a lighter schedule.",
    )


# ---------------------------------------------------------------------------
# 1. not_connected AIReasoningResult is valid with confidence 0.0 and no
#    evidence refs.
# ---------------------------------------------------------------------------


def test_not_connected_result_is_valid_with_zero_confidence_and_no_evidence() -> None:
    result = _not_connected_base()

    assert result.status == AIReasoningStatus.NOT_CONNECTED
    assert result.confidence == 0.0
    assert result.evidence_refs == []


# ---------------------------------------------------------------------------
# 2. completed AIReasoningResult requires evidence refs.
# ---------------------------------------------------------------------------


def test_completed_result_requires_evidence_refs() -> None:
    with pytest.raises(ValidationError):
        AIReasoningResult(
            task=AIReasoningTask.FEEDBACK_INTERPRETATION,
            status=AIReasoningStatus.COMPLETED,
            summary="Explains the captured feedback.",
            evidence_refs=[],
            guardrail_report=AIReasoningGuardrailReport(passed=True),
            confidence=0.6,
        )


def test_completed_result_with_evidence_refs_is_valid() -> None:
    result = AIReasoningResult(
        task=AIReasoningTask.FEEDBACK_INTERPRETATION,
        status=AIReasoningStatus.COMPLETED,
        summary="Explains the captured feedback.",
        evidence_refs=[_evidence_ref()],
        guardrail_report=AIReasoningGuardrailReport(passed=True),
        confidence=0.6,
    )
    assert result.status == AIReasoningStatus.COMPLETED


# ---------------------------------------------------------------------------
# 3. completed AIReasoningResult requires guardrail_report.passed true.
# ---------------------------------------------------------------------------


def test_completed_result_requires_guardrail_passed_true() -> None:
    with pytest.raises(ValidationError):
        AIReasoningResult(
            task=AIReasoningTask.FEEDBACK_INTERPRETATION,
            status=AIReasoningStatus.COMPLETED,
            summary="Explains the captured feedback.",
            evidence_refs=[_evidence_ref()],
            guardrail_report=AIReasoningGuardrailReport(passed=False, blocked_reasons=["x"]),
            confidence=0.6,
        )


# ---------------------------------------------------------------------------
# 4. rejected AIReasoningResult requires blocked reasons.
# ---------------------------------------------------------------------------


def test_rejected_result_requires_blocked_reasons() -> None:
    with pytest.raises(ValidationError):
        AIReasoningResult(
            task=AIReasoningTask.FEEDBACK_INTERPRETATION,
            status=AIReasoningStatus.REJECTED,
            summary="Rejected candidate output.",
            guardrail_report=AIReasoningGuardrailReport(passed=False, blocked_reasons=[]),
            confidence=0.0,
        )


def test_rejected_result_requires_guardrail_passed_false() -> None:
    with pytest.raises(ValidationError):
        AIReasoningResult(
            task=AIReasoningTask.FEEDBACK_INTERPRETATION,
            status=AIReasoningStatus.REJECTED,
            summary="Rejected candidate output.",
            guardrail_report=AIReasoningGuardrailReport(passed=True, blocked_reasons=["x"]),
            confidence=0.0,
        )


def test_rejected_result_with_blocked_reasons_is_valid() -> None:
    result = AIReasoningResult(
        task=AIReasoningTask.FEEDBACK_INTERPRETATION,
        status=AIReasoningStatus.REJECTED,
        summary="Rejected candidate output.",
        guardrail_report=AIReasoningGuardrailReport(
            passed=False, blocked_reasons=["Output contained an invented price."]
        ),
        confidence=0.0,
    )
    assert result.status == AIReasoningStatus.REJECTED


def test_not_connected_result_requires_zero_confidence() -> None:
    with pytest.raises(ValidationError):
        AIReasoningResult(
            task=AIReasoningTask.FEEDBACK_INTERPRETATION,
            status=AIReasoningStatus.NOT_CONNECTED,
            summary="No AI reasoning provider is connected yet.",
            guardrail_report=AIReasoningGuardrailReport(passed=False, blocked_reasons=["x"]),
            confidence=0.4,
        )


# ---------------------------------------------------------------------------
# 5. forbidden strings in summary are rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_summary",
    [
        "This place has a rating: 4.8",
        "The price: $40 per person",
        "opening_hours: 9am-5pm",
        "booking_url: https://example.com/book",
        "reservation_url: https://example.com/reserve",
        "route_time: 12 minutes",
        "safety_score: 9/10",
        "Costs about $40 total",
        "This hotel has 5 stars",
        "This is the " + "top" + " rated" + " place in town",
        "Widely considered the best hotel nearby",
        "You should book now before it sells out",
    ],
)
def test_forbidden_strings_in_summary_are_rejected(forbidden_summary: str) -> None:
    with pytest.raises(ValidationError):
        AIReasoningResult(
            task=AIReasoningTask.FEEDBACK_INTERPRETATION,
            status=AIReasoningStatus.SKIPPED,
            summary=forbidden_summary,
            guardrail_report=AIReasoningGuardrailReport(passed=False, blocked_reasons=["x"]),
            confidence=0.0,
        )


def test_safe_summary_is_accepted() -> None:
    result = AIReasoningResult(
        task=AIReasoningTask.FEEDBACK_INTERPRETATION,
        status=AIReasoningStatus.SKIPPED,
        summary="Feedback interpretation was skipped because no provider is connected.",
        guardrail_report=AIReasoningGuardrailReport(passed=False, blocked_reasons=["x"]),
        confidence=0.0,
    )
    assert result.status == AIReasoningStatus.SKIPPED


# ---------------------------------------------------------------------------
# 6. forbidden strings in reasoning list are rejected.
# ---------------------------------------------------------------------------


def test_forbidden_strings_in_reasoning_list_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AIReasoningResult(
            task=AIReasoningTask.FEEDBACK_INTERPRETATION,
            status=AIReasoningStatus.SKIPPED,
            summary="Feedback interpretation was skipped.",
            reasoning=["The traveler asked for less walking.", "rating: 4.5 stars"],
            guardrail_report=AIReasoningGuardrailReport(passed=False, blocked_reasons=["x"]),
            confidence=0.0,
        )


def test_safe_reasoning_list_is_accepted() -> None:
    result = AIReasoningResult(
        task=AIReasoningTask.FEEDBACK_INTERPRETATION,
        status=AIReasoningStatus.SKIPPED,
        summary="Feedback interpretation was skipped.",
        reasoning=["The traveler asked for a lighter schedule."],
        guardrail_report=AIReasoningGuardrailReport(passed=False, blocked_reasons=["x"]),
        confidence=0.0,
    )
    assert result.reasoning == ["The traveler asked for a lighter schedule."]


# ---------------------------------------------------------------------------
# 7. AIReasoningRequest rejects blank trip_id.
# ---------------------------------------------------------------------------


def test_request_rejects_blank_trip_id() -> None:
    with pytest.raises(ValidationError):
        AIReasoningRequest(
            task=AIReasoningTask.FEEDBACK_INTERPRETATION,
            trip_id="   ",
            input_sections=["feedback_history"],
        )


# ---------------------------------------------------------------------------
# 8. AIReasoningRequest rejects empty input_sections.
# ---------------------------------------------------------------------------


def test_request_rejects_empty_input_sections() -> None:
    with pytest.raises(ValidationError):
        AIReasoningRequest(
            task=AIReasoningTask.FEEDBACK_INTERPRETATION,
            trip_id="trip_001",
            input_sections=[],
        )


def test_request_with_valid_fields_is_accepted() -> None:
    request = AIReasoningRequest(
        task=AIReasoningTask.FEEDBACK_INTERPRETATION,
        trip_id="trip_001",
        input_sections=["feedback_history"],
        user_prompt="Make this less packed",
    )
    assert request.trip_id == "trip_001"
    assert request.evidence_refs == []


def test_request_rejects_overlong_user_prompt() -> None:
    with pytest.raises(ValidationError):
        AIReasoningRequest(
            task=AIReasoningTask.FEEDBACK_INTERPRETATION,
            trip_id="trip_001",
            input_sections=["feedback_history"],
            user_prompt="x" * 3001,
        )


# ---------------------------------------------------------------------------
# 9. EvidenceRef rejects blank source fields.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank_field", ["source_section", "source_field", "claim"])
def test_evidence_ref_rejects_blank_fields(blank_field: str) -> None:
    fields = {
        "source_section": "feedback_history",
        "source_field": "feedback_text",
        "claim": "The traveler asked for a lighter schedule.",
    }
    fields[blank_field] = "   "
    with pytest.raises(ValidationError):
        EvidenceRef(**fields)


def test_unavailable_constraint_rejects_blank_fields() -> None:
    with pytest.raises(ValidationError):
        UnavailableConstraint(field="", reason="No provider connected.", data_status="not_connected")


def test_unavailable_constraint_with_valid_fields_is_accepted() -> None:
    constraint = UnavailableConstraint(
        field="hotel_prices", reason="No accommodation provider is connected.", data_status="not_connected"
    )
    assert constraint.field == "hotel_prices"


# ---------------------------------------------------------------------------
# 10. FeedbackInterpretationResult rejects blank feedback_type.
# ---------------------------------------------------------------------------


def test_feedback_interpretation_result_rejects_blank_feedback_type() -> None:
    with pytest.raises(ValidationError):
        FeedbackInterpretationResult(
            base=_not_connected_base(),
            feedback_type="  ",
            affected_sections=["experience_plan"],
        )


# ---------------------------------------------------------------------------
# 11. FeedbackInterpretationResult rejects empty affected_sections.
# ---------------------------------------------------------------------------


def test_feedback_interpretation_result_rejects_empty_affected_sections() -> None:
    with pytest.raises(ValidationError):
        FeedbackInterpretationResult(
            base=_not_connected_base(),
            feedback_type="pace_change",
            affected_sections=[],
        )


def test_feedback_interpretation_result_rejects_overlong_clarification_question() -> None:
    with pytest.raises(ValidationError):
        FeedbackInterpretationResult(
            base=_not_connected_base(),
            feedback_type="pace_change",
            affected_sections=["experience_plan"],
            clarification_question="x" * 501,
        )


# ---------------------------------------------------------------------------
# 12. Task-specific result models can be instantiated with safe
#     explanation-only text.
# ---------------------------------------------------------------------------


def test_traveler_preference_interpretation_result_accepts_safe_text() -> None:
    result = TravelerPreferenceInterpretationResult(
        base=_not_connected_base(AIReasoningTask.TRAVELER_PREFERENCE_INTERPRETATION),
        interpreted_interests=["museums", "walking tours"],
        interpreted_constraints=["avoid early mornings"],
        ambiguity_notes=["'nearby' was not clarified relative to a specific area."],
    )
    assert result.interpreted_interests == ["museums", "walking tours"]


def test_trip_strategy_reasoning_result_accepts_safe_text() -> None:
    result = TripStrategyReasoningResult(
        base=_not_connected_base(AIReasoningTask.TRIP_STRATEGY_EXPLANATION),
        strategy_themes=["relaxed pacing"],
        tradeoff_notes=["Fewer attractions per day in exchange for less walking."],
    )
    assert result.strategy_themes == ["relaxed pacing"]


def test_stay_area_reasoning_result_accepts_safe_text() -> None:
    result = StayAreaReasoningResult(
        base=_not_connected_base(AIReasoningTask.STAY_AREA_EXPLANATION),
        stay_area_factors=["central to scheduled attractions"],
        missing_accommodation_data=["price", "availability"],
    )
    assert result.stay_area_factors == ["central to scheduled attractions"]


def test_experience_plan_reasoning_result_accepts_safe_text() -> None:
    result = ExperiencePlanReasoningResult(
        base=_not_connected_base(AIReasoningTask.EXPERIENCE_PLAN_EXPLANATION),
        sequencing_rationale=["Grouped by straight-line proximity."],
        unvalidated_experience_facts=["opening hours", "walking time"],
    )
    assert result.sequencing_rationale == ["Grouped by straight-line proximity."]


def test_validation_reasoning_result_accepts_safe_text() -> None:
    result = ValidationReasoningResult(
        base=_not_connected_base(AIReasoningTask.VALIDATION_EXPLANATION),
        user_review_reasons=["Route ordering has not been validated."],
        blocked_reasons=[],
    )
    assert result.user_review_reasons == ["Route ordering has not been validated."]


def test_feedback_interpretation_result_accepts_safe_text() -> None:
    result = FeedbackInterpretationResult(
        base=_not_connected_base(AIReasoningTask.FEEDBACK_INTERPRETATION),
        feedback_type="pace_change",
        affected_sections=["experience_plan"],
        requires_regeneration=True,
        clarification_question="Which day should feel lighter?",
    )
    assert result.feedback_type == "pace_change"
    assert result.requires_regeneration is True


# ---------------------------------------------------------------------------
# 13. No task-specific model contains a forbidden travel-fact field name.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_cls", _TASK_SPECIFIC_RESULT_MODELS)
def test_task_specific_models_never_contain_forbidden_field_names(model_cls) -> None:
    field_names = set(model_cls.model_fields.keys())
    overlap = field_names & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"{model_cls.__name__} has forbidden field(s): {overlap}"


def test_base_ai_reasoning_result_never_contains_forbidden_field_names() -> None:
    field_names = set(AIReasoningResult.model_fields.keys())
    overlap = field_names & _FORBIDDEN_MODEL_FIELD_NAMES
    assert overlap == set(), f"AIReasoningResult has forbidden field(s): {overlap}"
