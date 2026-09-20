from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    CandidateOrigin,
    ItineraryCandidateReference,
    ItineraryReasoningCategory,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
    TravelerContextSummary,
)
from app.models.ai_itinerary_repair import (
    AIItineraryRepairIssue,
    AIItineraryRepairRequest,
    AIItineraryRepairResult,
    AIItineraryRepairStatus,
    RepairableIssueType,
    merge_repair_into_reasoning_result,
    validate_repair_result_against_request,
)
from app.models.common import DataStatus, GeoPoint, ValidationSeverity

# Tests for the Section 194A repair contract models (docs/14_backend_
# architecture.md, following section 143). No provider/network/LLM call
# anywhere in this file -- every request/result is constructed directly,
# exactly like Section 193A's own test_ai_itinerary_reasoning_models.py.


def _candidate(candidate_id: str, name: str) -> ItineraryCandidateReference:
    return ItineraryCandidateReference(
        candidate_id=candidate_id,
        name=name,
        category=ItineraryReasoningCategory.ATTRACTION,
        provider_name="openstreetmap_places",
        provider_place_id=candidate_id.split(":")[-1],
        coordinates=GeoPoint(lat=38.7, lng=-9.1),
        data_status=DataStatus.LIVE,
        quality_score=0.7,
        quality_tier="good_candidate",
        origin=CandidateOrigin.BROAD_PROVIDER_DISCOVERY,
    )


_CANDIDATES = [_candidate(f"osm:{letter}", letter) for letter in "ABCDE"]
_ORIGINAL_DAYS = [
    ItineraryReasoningDayPlan(day_index=1, candidate_ids=["osm:A", "osm:B"], rationale="Day one."),
    ItineraryReasoningDayPlan(
        day_index=2, candidate_ids=["osm:C", "osm:D", "osm:E"], rationale="Day two, overloaded."
    ),
]


def _issue(day_index: int = 2, **overrides: object) -> AIItineraryRepairIssue:
    fields: dict[str, object] = {
        "issue_type": RepairableIssueType.GEOGRAPHIC_SPREAD,
        "day_index": day_index,
        "source_category": "geographic_spread",
        "message": "Day 2 is geographically spread out.",
        "severity": ValidationSeverity.WARNING,
    }
    fields.update(overrides)
    return AIItineraryRepairIssue(**fields)


def _request(**overrides: object) -> AIItineraryRepairRequest:
    fields: dict[str, object] = {
        "trip_id": "trip_001",
        "destination_name": "Lisbon, Portugal",
        "start_date": "2026-09-10",
        "end_date": "2026-09-11",
        "trip_duration_days": 2,
        "traveler_context": TravelerContextSummary(
            travelers_count=2, travel_group_type="couple", pace="balanced"
        ),
        "allowed_candidates": _CANDIDATES,
        "original_days": _ORIGINAL_DAYS,
        "affected_days": [2],
        "issues": [_issue()],
    }
    fields.update(overrides)
    return AIItineraryRepairRequest(**fields)


def _completed_result(**overrides: object) -> AIItineraryRepairResult:
    fields: dict[str, object] = {
        "status": AIItineraryRepairStatus.COMPLETED,
        "repaired_days": [
            ItineraryReasoningDayPlan(day_index=2, candidate_ids=["osm:C", "osm:E"], rationale="Dropped D.")
        ],
        "repair_summary": "Removed the third stop from day 2 to reduce geographic spread.",
        "addressed_issue_types": [RepairableIssueType.GEOGRAPHIC_SPREAD],
        "guardrail_report": AIItineraryReasoningGuardrailReport(passed=True),
        "provider_name": "fake_provider",
        "confidence": 0.7,
    }
    fields.update(overrides)
    return AIItineraryRepairResult(**fields)


def _original_reasoning_result() -> AIItineraryReasoningResult:
    return AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.COMPLETED,
        strategy=ItineraryReasoningStrategy(summary="s", pace="balanced", reason="r"),
        days=_ORIGINAL_DAYS,
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        confidence=0.8,
    )


# ---------------------------------------------------------------------------
# Request structural safety.
# ---------------------------------------------------------------------------


def test_request_builds_successfully_with_consistent_fields() -> None:
    request = _request()
    assert request.allowed_candidate_ids() == {"osm:A", "osm:B", "osm:C", "osm:D", "osm:E"}


def test_issue_day_index_must_be_in_affected_days() -> None:
    with pytest.raises(ValidationError, match="not in affected_days"):
        _request(affected_days=[1], issues=[_issue(day_index=2)])


def test_affected_day_index_cannot_exceed_trip_duration_days() -> None:
    with pytest.raises(ValidationError, match="exceeds trip_duration_days"):
        _request(affected_days=[5], issues=[_issue(day_index=5)])


def test_original_days_candidate_must_be_in_allowed_candidates() -> None:
    bad_days = [
        ItineraryReasoningDayPlan(day_index=1, candidate_ids=["osm:not-real"], rationale="bad"),
    ]
    with pytest.raises(ValidationError, match="not in allowed_candidates"):
        _request(original_days=bad_days, affected_days=[1], issues=[_issue(day_index=1)])


def test_affected_days_must_not_repeat() -> None:
    with pytest.raises(ValidationError, match="must not repeat"):
        _request(affected_days=[2, 2])


def test_to_reasoning_request_carries_same_candidate_universe() -> None:
    request = _request()
    reasoning_request = request.to_reasoning_request()
    assert reasoning_request.allowed_candidate_ids() == request.allowed_candidate_ids()
    assert reasoning_request.trip_duration_days == request.trip_duration_days


# ---------------------------------------------------------------------------
# Result status consistency.
# ---------------------------------------------------------------------------


def test_completed_result_requires_repaired_days() -> None:
    with pytest.raises(ValidationError, match="at least one repaired day"):
        _completed_result(repaired_days=[])


def test_completed_result_requires_repair_summary() -> None:
    with pytest.raises(ValidationError, match="repair_summary"):
        _completed_result(repair_summary=None)


def test_completed_result_requires_passed_guardrail() -> None:
    with pytest.raises(ValidationError, match="passed=True"):
        _completed_result(guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=["x"]))


def test_rejected_result_requires_failed_guardrail_with_reason() -> None:
    with pytest.raises(ValidationError, match="passed=False"):
        AIItineraryRepairResult(
            status=AIItineraryRepairStatus.REJECTED,
            guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        )


def test_not_connected_result_must_have_no_repaired_days() -> None:
    with pytest.raises(ValidationError, match="must have no repaired_days"):
        AIItineraryRepairResult(
            status=AIItineraryRepairStatus.NOT_CONNECTED,
            repaired_days=[ItineraryReasoningDayPlan(day_index=1, candidate_ids=["osm:A"], rationale="x")],
            guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=["disabled"]),
        )


def test_skipped_result_must_have_no_repaired_days() -> None:
    with pytest.raises(ValidationError, match="must have no repaired_days"):
        AIItineraryRepairResult(
            status=AIItineraryRepairStatus.SKIPPED,
            repaired_days=[ItineraryReasoningDayPlan(day_index=1, candidate_ids=["osm:A"], rationale="x")],
            guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        )


@pytest.mark.parametrize("field", ["repair_summary"])
def test_forbidden_factual_claim_is_rejected(field: str) -> None:
    with pytest.raises(ValidationError, match="forbidden pattern"):
        _completed_result(**{field: "This costs a cheap price and is highly rated."})


# ---------------------------------------------------------------------------
# Task 21: valid repair -- day 1 unchanged, day 2 changed, D omitted.
# ---------------------------------------------------------------------------


def test_valid_repair_has_no_violations_and_merges_cleanly() -> None:
    request = _request()
    result = _completed_result()

    violations = validate_repair_result_against_request(request, result)
    assert violations == []

    merged = merge_repair_into_reasoning_result(_original_reasoning_result(), request, result)
    merged_by_day = {day.day_index: day.candidate_ids for day in merged.days}
    assert merged_by_day[1] == ["osm:A", "osm:B"]
    assert merged_by_day[2] == ["osm:C", "osm:E"]
    assert "osm:D" not in merged_by_day[2]


# ---------------------------------------------------------------------------
# Task 22: candidate moved between two affected days.
# ---------------------------------------------------------------------------


def test_candidate_can_move_between_two_affected_days() -> None:
    request = _request(
        affected_days=[1, 2],
        issues=[_issue(day_index=1), _issue(day_index=2)],
    )
    result = _completed_result(
        repaired_days=[
            ItineraryReasoningDayPlan(day_index=1, candidate_ids=["osm:A"], rationale="Moved B out."),
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=["osm:B", "osm:C", "osm:D", "osm:E"], rationale="Moved B in."
            ),
        ]
    )

    violations = validate_repair_result_against_request(request, result)
    assert violations == []

    merged = merge_repair_into_reasoning_result(_original_reasoning_result(), request, result)
    merged_by_day = {day.day_index: day.candidate_ids for day in merged.days}
    assert merged_by_day[1] == ["osm:A"]
    assert "osm:B" in merged_by_day[2]


# ---------------------------------------------------------------------------
# Task 23: cannot modify unaffected day.
# ---------------------------------------------------------------------------


def test_unaffected_day_mutation_is_rejected() -> None:
    request = _request()  # affected_days=[2] only
    result = _completed_result(
        repaired_days=[
            ItineraryReasoningDayPlan(day_index=1, candidate_ids=["osm:B"], rationale="Changed day 1.")
        ]
    )

    violations = validate_repair_result_against_request(request, result)
    assert any("not in request.affected_days" in v for v in violations)

    with pytest.raises(ValueError, match="failed repair-safety validation"):
        merge_repair_into_reasoning_result(_original_reasoning_result(), request, result)


def test_reusing_an_unaffected_days_candidate_is_rejected() -> None:
    request = _request()  # affected_days=[2] only; day 1 has A, B
    result = _completed_result(
        repaired_days=[
            ItineraryReasoningDayPlan(day_index=2, candidate_ids=["osm:A"], rationale="Reused A from day 1.")
        ]
    )

    violations = validate_repair_result_against_request(request, result)
    assert any("already used by an unaffected original day" in v for v in violations)


# ---------------------------------------------------------------------------
# Task 24: hallucinated candidate.
# ---------------------------------------------------------------------------


def test_hallucinated_candidate_is_rejected() -> None:
    request = _request()
    result = _completed_result(
        repaired_days=[
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=["made-up-provider:999"], rationale="Invented a place."
            )
        ]
    )

    violations = validate_repair_result_against_request(request, result)
    assert any("not in request.allowed_candidates" in v for v in violations)


# ---------------------------------------------------------------------------
# Task 25: forbidden factual claims in rationale are rejected at the
# ItineraryReasoningDayPlan level (reused from Section 193A) before a
# repaired day can even be constructed.
# ---------------------------------------------------------------------------


def test_forbidden_factual_claim_in_repaired_day_rationale_is_rejected() -> None:
    with pytest.raises(ValidationError, match="forbidden pattern"):
        ItineraryReasoningDayPlan(
            day_index=2, candidate_ids=["osm:C"], rationale="This place is highly rated."
        )


# ---------------------------------------------------------------------------
# Merge safety: rejected/not_connected repair results can never be merged.
# ---------------------------------------------------------------------------


def test_merge_refuses_a_non_completed_repair_result() -> None:
    request = _request()
    rejected = AIItineraryRepairResult(
        status=AIItineraryRepairStatus.REJECTED,
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=["bad"]),
    )
    with pytest.raises(ValueError, match="requires a completed"):
        merge_repair_into_reasoning_result(_original_reasoning_result(), request, rejected)


def test_merge_does_not_mutate_the_original_result() -> None:
    original = _original_reasoning_result()
    original_days_snapshot = [day.model_copy() for day in original.days]
    request = _request()
    result = _completed_result()

    merge_repair_into_reasoning_result(original, request, result)

    assert original.days == original_days_snapshot
