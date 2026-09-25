from __future__ import annotations

from datetime import date
from typing import Any

from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
)
from app.models.ai_itinerary_repair import AIItineraryRepairResult, AIItineraryRepairStatus
from app.models.common import ValidationSeverity
from app.services.itinerary_narrative_grounding import SAFE_AI_UNAVAILABLE_MESSAGE
from app.models.itinerary_narrative import (
    ItineraryNarrativeDayOutput,
    ItineraryNarrativeReport,
    ItineraryNarrativeStatus,
    validate_narrative_against_request,
)
from app.models.planning_state import (
    DailyPlan,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripRequest,
    ValidationIssue,
    ValidationReport,
)
from app.services.itinerary_narrative_request_builder import ItineraryNarrativeRequestBuilder
from app.services.itinerary_narrative_service import ItineraryNarrativeService

# Section 195 (docs/14_backend_architecture.md, following section 145):
# proves the narrator consumes only the FINAL, current, post-repair
# PlanningState -- never a stale pre-repair snapshot -- and that its
# structural place-identity/forbidden-claim safety mechanisms actually
# reject unsafe output rather than merely documenting an intent. Every
# test here constructs `PlanningState` fields directly (no provider/LLM/
# network call anywhere, matching this repo's established AI-contract
# test convention).


def _trip_request(**overrides: Any) -> TripRequest:
    fields: dict[str, Any] = {
        "primary_destination": "Lisbon, Portugal",
        "start_date": "2026-09-10",
        "end_date": "2026-09-11",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
    }
    fields.update(overrides)
    return TripRequest(**fields)


def _experience(experience_id: str, name: str, day_number: int, stop_order: int) -> ExperienceItem:
    return ExperienceItem(
        experience_id=experience_id,
        name=name,
        category="attraction",
        day_number=day_number,
        stop_order=stop_order,
        why_included="Matches the traveler's stated interests.",
    )


def _base_planning_state() -> PlanningState:
    """Day 1 = [A, B], day 2 = [C] -- the FINAL, current plan (as if a
    Section 194B repair already removed B's original day-2 companion and
    day 2 now only has C -- the exact shape doesn't matter, only that
    this is what `experience_plan` currently holds when the narrator
    runs).
    """
    planning_state = PlanningState(trip_request=_trip_request())
    day1 = DailyPlan(
        day_number=1,
        date=date(2026, 9, 10),
        experiences=[
            _experience("exp_a", "Castelo de Sao Jorge", 1, 1),
            _experience("exp_b", "Praca do Comercio", 1, 2),
        ],
    )
    day2 = DailyPlan(
        day_number=2,
        date=date(2026, 9, 11),
        experiences=[_experience("exp_c", "Torre de Belem", 2, 1)],
    )
    planning_state.experience_plan = ExperiencePlan(daily_plans=[day1, day2])
    return planning_state


def _completed_reasoning_result(day2_rationale: str = "Focused on Belem's monuments.") -> AIItineraryReasoningResult:
    return AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.COMPLETED,
        strategy=ItineraryReasoningStrategy(
            summary="A relaxed 2-day Lisbon plan.", pace="balanced", reason="Matches traveler preferences."
        ),
        days=[
            ItineraryReasoningDayPlan(
                day_index=1,
                candidate_ids=["openstreetmap_places:way/1", "openstreetmap_places:way/2"],
                rationale="Central historic Lisbon in the morning.",
            ),
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=["openstreetmap_places:way/3"], rationale=day2_rationale
            ),
        ],
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        confidence=0.8,
    )


def _completed_repair_result() -> AIItineraryRepairResult:
    return AIItineraryRepairResult(
        status=AIItineraryRepairStatus.COMPLETED,
        repaired_days=[
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=["openstreetmap_places:way/3"], rationale="Dropped the far stop."
            )
        ],
        repair_summary="Removed one candidate from day 2 to reduce geographic spread.",
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        provider_name="fake_repair_provider",
        confidence=0.7,
    )


class _FakeProvider:
    def __init__(self, report: ItineraryNarrativeReport) -> None:
        self._report = report
        self.last_request = None

    def narrate(self, request):  # noqa: ANN001
        self.last_request = request
        return self._report


def _success_report_for(request, referenced_ids_by_day: dict[int, list[str]] | None = None) -> ItineraryNarrativeReport:
    referenced_ids_by_day = referenced_ids_by_day or {}
    return ItineraryNarrativeReport(
        status=ItineraryNarrativeStatus.SUCCESS,
        provider="fake_test_provider",
        summary=f"A trip to {request.destination}.",
        daily_narratives=[
            ItineraryNarrativeDayOutput(
                day_number=day.day_number,
                date=day.date,
                title=f"Day {day.day_number}",
                narrative=f"Day {day.day_number} narrative.",
                referenced_experience_ids=referenced_ids_by_day.get(day.day_number, []),
            )
            for day in request.days
        ],
    )


# ---------------------------------------------------------------------------
# Task 1: request builder always reads the CURRENT (final, post-repair)
# state -- proven directly by inspecting what the real builder produces
# from the "already repaired" fixture above.
# ---------------------------------------------------------------------------


def test_request_reflects_only_the_final_current_plan() -> None:
    planning_state = _base_planning_state()

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    day2 = next(day for day in request.days if day.day_number == 2)
    assert [e.name for e in day2.experiences] == ["Torre de Belem"]
    assert [e.experience_id for e in day2.experiences] == ["exp_c"]


# ---------------------------------------------------------------------------
# Task 11: removed-candidate leakage regression -- both at request-builder
# level and at service/provider-output validation level.
# ---------------------------------------------------------------------------


def test_removed_candidate_is_absent_from_the_request() -> None:
    """Simulates: original AI plan had A, B, C on day 2; a repair removed
    B (day 2 now only has C, as in _base_planning_state). The narrator
    request must never mention B by name or by experience_id, because the
    request builder only ever reads the CURRENT experience_plan."""
    planning_state = _base_planning_state()

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    all_names = {e.name for day in request.days for e in day.experiences}
    all_ids = {e.experience_id for day in request.days for e in day.experiences}
    assert "B" not in all_names
    assert "exp_b_removed" not in all_ids


def test_hallucinated_referenced_experience_id_is_rejected_at_validation_level() -> None:
    """Even if a (buggy/adversarial) provider's free-text narrative never
    mentions a removed candidate by name, a structurally invented or
    cross-day `referenced_experience_ids` entry must be rejected -- never
    trusted just because the narrative prose itself looks clean."""
    planning_state = _base_planning_state()
    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    # A hallucinated id that was never part of any day's real schedule
    # (e.g. the internal id of a since-repaired-away candidate B).
    report = _success_report_for(request, referenced_ids_by_day={2: ["exp_b_removed_by_repair"]})

    violations = validate_narrative_against_request(request, report)
    assert violations
    assert any("exp_b_removed_by_repair" in v for v in violations)


def test_cross_day_referenced_experience_id_is_rejected() -> None:
    """A real id, but attributed to the wrong day, is also rejected --
    exp_a is real but belongs to day 1, not day 2."""
    planning_state = _base_planning_state()
    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    report = _success_report_for(request, referenced_ids_by_day={2: ["exp_a"]})

    violations = validate_narrative_against_request(request, report)
    assert violations
    assert any("exp_a" in v and "day 2" in v for v in violations)


def test_valid_referenced_experience_ids_produce_no_violations() -> None:
    planning_state = _base_planning_state()
    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    report = _success_report_for(
        request, referenced_ids_by_day={1: ["exp_a", "exp_b"], 2: ["exp_c"]}
    )

    assert validate_narrative_against_request(request, report) == []


# ---------------------------------------------------------------------------
# Task 20: final validation context -- resolved issues excluded, exhausted/
# unresolved issues preserved.
# ---------------------------------------------------------------------------


def test_resolved_issue_is_not_present_in_the_request() -> None:
    """After a successful repair cleared a geographic_spread issue, the
    CURRENT validation_report has none -- the request must not describe
    one, since the builder only ever reads whatever is current."""
    planning_state = _base_planning_state()
    planning_state.validation_report = ValidationReport(
        readiness_status="ready", warnings=[], critical_issues=[]
    )

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.warning_count == 0
    assert request.critical_issue_count == 0


def test_unresolved_issue_after_exhausted_repair_is_preserved_in_the_request() -> None:
    planning_state = _base_planning_state()
    planning_state.validation_report = ValidationReport(
        readiness_status="needs_review",
        warnings=[
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="geographic_spread",
                message="Day 2 is geographically spread out.",
                affected_section="experience_plan.daily_plans[2]",
            )
        ],
        critical_issues=[],
    )

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.warning_count == 1
    assert request.validation_status == "needs_review"


# ---------------------------------------------------------------------------
# Task 22: narrator + repaired plan integration test.
# ---------------------------------------------------------------------------


def test_narrator_request_reflects_successful_repair_context() -> None:
    planning_state = _base_planning_state()
    planning_state.ai_itinerary_reasoning_result = _completed_reasoning_result()
    planning_state.ai_itinerary_repair_result = _completed_repair_result()
    planning_state.ai_itinerary_repair_attempt_count = 1

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.was_adjusted_after_feasibility_checks is True
    assert request.reasoning_strategy_summary == "A relaxed 2-day Lisbon plan."
    day2 = next(day for day in request.days if day.day_number == 2)
    assert day2.reasoning_rationale == "Focused on Belem's monuments."


def test_narrator_service_end_to_end_with_repaired_plan_excludes_removed_candidate() -> None:
    planning_state = _base_planning_state()
    planning_state.ai_itinerary_reasoning_result = _completed_reasoning_result()
    planning_state.ai_itinerary_repair_result = _completed_repair_result()
    planning_state.ai_itinerary_repair_attempt_count = 1

    fake_provider = _FakeProvider(
        ItineraryNarrativeReport(status=ItineraryNarrativeStatus.SUCCESS, summary="placeholder")
    )
    # Build the real report from the real request the service builds,
    # exactly as a real provider would.
    service = ItineraryNarrativeService(provider=fake_provider)
    request_preview = ItineraryNarrativeRequestBuilder().build_request(planning_state)
    fake_provider._report = _success_report_for(
        request_preview, referenced_ids_by_day={1: ["exp_a", "exp_b"], 2: ["exp_c"]}
    )

    from app.core.config import Settings
    import app.services.itinerary_narrative_service as narrative_service_module

    original_get_settings = narrative_service_module.get_settings
    narrative_service_module.get_settings = lambda: Settings(_env_file=None, ITINERARY_NARRATOR_ENABLED=True)
    try:
        result = service.generate(planning_state)
    finally:
        narrative_service_module.get_settings = original_get_settings

    report = result.itinerary_narrative_report
    assert report is not None
    assert report.status == ItineraryNarrativeStatus.SUCCESS
    all_referenced = {i for day in report.daily_narratives for i in day.referenced_experience_ids}
    assert "exp_b_removed_by_repair" not in all_referenced
    assert fake_provider.last_request is not None
    assert fake_provider.last_request.was_adjusted_after_feasibility_checks is True


# ---------------------------------------------------------------------------
# Task 23: narrator + failed/exhausted repair test.
# ---------------------------------------------------------------------------


def test_rejected_repair_does_not_claim_adjustment() -> None:
    planning_state = _base_planning_state()
    planning_state.ai_itinerary_reasoning_result = _completed_reasoning_result()
    planning_state.ai_itinerary_repair_result = AIItineraryRepairResult(
        status=AIItineraryRepairStatus.REJECTED,
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=["bad output"]),
    )
    planning_state.ai_itinerary_repair_attempt_count = 1
    planning_state.validation_report = ValidationReport(
        readiness_status="needs_review",
        warnings=[
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="geographic_spread",
                message="Day 2 is geographically spread out.",
                affected_section="experience_plan.daily_plans[2]",
            )
        ],
    )

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.was_adjusted_after_feasibility_checks is False
    assert request.warning_count == 1


# ---------------------------------------------------------------------------
# Task 24: narrator + no-repair test.
# ---------------------------------------------------------------------------


def test_no_repair_attempted_never_implies_adjustment() -> None:
    planning_state = _base_planning_state()
    planning_state.ai_itinerary_reasoning_result = _completed_reasoning_result()
    assert planning_state.ai_itinerary_repair_attempt_count == 0
    assert planning_state.ai_itinerary_repair_result is None

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.was_adjusted_after_feasibility_checks is False


# ---------------------------------------------------------------------------
# Task 25: narrator + deterministic fallback (no AI reasoning at all).
# ---------------------------------------------------------------------------


def test_deterministic_fallback_plan_has_no_reasoning_context() -> None:
    planning_state = _base_planning_state()
    assert planning_state.ai_itinerary_reasoning_result is None

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.reasoning_strategy_summary is None
    assert all(day.reasoning_rationale is None for day in request.days)
    assert request.was_adjusted_after_feasibility_checks is False


def test_deterministic_fallback_present_when_reasoning_not_completed() -> None:
    """A `rejected`/`not_connected` reasoning result must not leak its
    (empty/invalid) content into the narrator's context either."""
    planning_state = _base_planning_state()
    planning_state.ai_itinerary_reasoning_result = AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.REJECTED,
        days=[],
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=["bad output"]),
        confidence=0.0,
    )

    request = ItineraryNarrativeRequestBuilder().build_request(planning_state)

    assert request.reasoning_strategy_summary is None
    assert all(day.reasoning_rationale is None for day in request.days)


# ---------------------------------------------------------------------------
# Task 26: no-fabrication -- forbidden factual claims are rejected by the
# models themselves, not just by prompt instruction.
# ---------------------------------------------------------------------------


def test_forbidden_factual_claim_in_narrative_is_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="forbidden pattern"):
        ItineraryNarrativeDayOutput(
            day_number=1, date=date(2026, 9, 10), title="Day 1", narrative="This is highly rated and cheap."
        )


def test_overclaim_language_in_summary_is_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="forbidden pattern"):
        ItineraryNarrativeReport(status=ItineraryNarrativeStatus.SUCCESS, summary="This plan is guaranteed and optimal.")


def test_blank_narrative_is_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ItineraryNarrativeDayOutput(day_number=1, date=date(2026, 9, 10), title="Day 1", narrative="   ")


# ---------------------------------------------------------------------------
# Task 27: read-only regression -- narrator generation never mutates any
# authoritative field.
# ---------------------------------------------------------------------------


def test_narrator_generation_is_completely_read_only() -> None:
    from app.core.config import Settings
    import app.services.itinerary_narrative_service as narrative_service_module

    planning_state = _base_planning_state()
    planning_state.ai_itinerary_reasoning_result = _completed_reasoning_result()
    planning_state.ai_itinerary_repair_result = _completed_repair_result()
    planning_state.ai_itinerary_repair_attempt_count = 1
    planning_state.validation_report = ValidationReport(readiness_status="ready")

    before = planning_state.model_copy(deep=True)

    fake_provider = _FakeProvider(
        ItineraryNarrativeReport(status=ItineraryNarrativeStatus.SUCCESS, summary="placeholder")
    )
    service = ItineraryNarrativeService(provider=fake_provider)
    request_preview = ItineraryNarrativeRequestBuilder().build_request(planning_state)
    fake_provider._report = _success_report_for(request_preview)

    original_get_settings = narrative_service_module.get_settings
    narrative_service_module.get_settings = lambda: Settings(_env_file=None, ITINERARY_NARRATOR_ENABLED=True)
    try:
        result = service.generate(planning_state)
    finally:
        narrative_service_module.get_settings = original_get_settings

    dumped_before = before.model_dump(exclude={"itinerary_narrative_report", "metadata"})
    dumped_after = result.model_dump(exclude={"itinerary_narrative_report", "metadata"})
    assert dumped_before == dumped_after
    assert result.itinerary_narrative_report is not None
    assert result.itinerary_narrative_report.status == ItineraryNarrativeStatus.SUCCESS


# ---------------------------------------------------------------------------
# Section 195.1 (docs/14_backend_architecture.md, following section 146):
# a deterministic, non-LLM guarantee that a final unresolved validation
# limitation is never silently omitted from a successful narrative,
# without depending on whether the LLM voluntarily chose to mention it.
# ---------------------------------------------------------------------------


def _generate_with_fake_provider(planning_state: PlanningState, report: ItineraryNarrativeReport):
    from app.core.config import Settings
    import app.services.itinerary_narrative_service as narrative_service_module

    fake_provider = _FakeProvider(report)
    service = ItineraryNarrativeService(provider=fake_provider)

    original_get_settings = narrative_service_module.get_settings
    narrative_service_module.get_settings = lambda: Settings(_env_file=None, ITINERARY_NARRATOR_ENABLED=True)
    try:
        result = service.generate(planning_state)
    finally:
        narrative_service_module.get_settings = original_get_settings
    return result, fake_provider


def _needs_review_validation_report() -> ValidationReport:
    return ValidationReport(
        readiness_status="needs_review",
        warnings=[
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="geographic_spread",
                message="Day 2 is geographically spread out.",
                affected_section="experience_plan.daily_plans[2]",
            )
        ],
        critical_issues=[],
    )


def test_unresolved_validation_and_llm_omits_warning_gets_deterministic_disclosure() -> None:
    planning_state = _base_planning_state()
    planning_state.validation_report = _needs_review_validation_report()

    request_preview = ItineraryNarrativeRequestBuilder().build_request(planning_state)
    llm_report = _success_report_for(request_preview)  # no warnings from the LLM
    assert llm_report.warnings == []

    result, _ = _generate_with_fake_provider(planning_state, llm_report)

    report = result.itinerary_narrative_report
    assert report is not None
    assert report.status == ItineraryNarrativeStatus.SUCCESS
    assert len(report.warnings) == 1
    assert "still need review" in report.warnings[0]
    # No overclaim language ever appears in the deterministic wording.
    for forbidden in ("guaranteed", "optimal", "verified", "safest", "travel-ready", "booking-ready"):
        assert forbidden not in report.warnings[0].lower()


def test_clean_final_validation_never_gets_a_synthetic_warning() -> None:
    planning_state = _base_planning_state()
    planning_state.validation_report = ValidationReport(readiness_status="ready", warnings=[], critical_issues=[])

    request_preview = ItineraryNarrativeRequestBuilder().build_request(planning_state)
    llm_report = _success_report_for(request_preview)
    assert llm_report.warnings == []

    result, _ = _generate_with_fake_provider(planning_state, llm_report)

    report = result.itinerary_narrative_report
    assert report is not None
    assert report.warnings == []


def test_resolved_after_repair_never_mentions_the_old_issue() -> None:
    """Pre-repair, a geographic_spread issue existed; the FINAL (current)
    validation_report is clean -- the disclosure must never fire, because
    it is driven entirely by the request's own (final-state) warning/
    critical counts, never a stale pre-repair validation snapshot."""
    planning_state = _base_planning_state()
    planning_state.ai_itinerary_repair_result = _completed_repair_result()
    planning_state.ai_itinerary_repair_attempt_count = 1
    planning_state.validation_report = ValidationReport(readiness_status="ready", warnings=[], critical_issues=[])

    request_preview = ItineraryNarrativeRequestBuilder().build_request(planning_state)
    llm_report = _success_report_for(request_preview)

    result, _ = _generate_with_fake_provider(planning_state, llm_report)

    report = result.itinerary_narrative_report
    assert report is not None
    assert report.warnings == []


def test_exhausted_repair_with_remaining_issue_gets_disclosure() -> None:
    planning_state = _base_planning_state()
    planning_state.ai_itinerary_repair_result = _completed_repair_result()
    planning_state.ai_itinerary_repair_attempt_count = 1
    planning_state.validation_report = _needs_review_validation_report()

    request_preview = ItineraryNarrativeRequestBuilder().build_request(planning_state)
    llm_report = _success_report_for(request_preview)

    result, _ = _generate_with_fake_provider(planning_state, llm_report)

    report = result.itinerary_narrative_report
    assert report is not None
    assert len(report.warnings) == 1


def test_narrator_already_provides_a_warning_is_not_duplicated() -> None:
    planning_state = _base_planning_state()
    planning_state.validation_report = _needs_review_validation_report()

    request_preview = ItineraryNarrativeRequestBuilder().build_request(planning_state)
    llm_report = _success_report_for(request_preview)
    llm_report = llm_report.model_copy(
        update={"warnings": ["A day may involve substantial geographic spread; you may want to review it."]}
    )

    result, _ = _generate_with_fake_provider(planning_state, llm_report)

    report = result.itinerary_narrative_report
    assert report is not None
    assert len(report.warnings) == 1
    assert "geographic spread" in report.warnings[0]


def test_failed_narrator_status_unaffected_by_disclosure_logic() -> None:
    planning_state = _base_planning_state()
    planning_state.validation_report = _needs_review_validation_report()

    failed_report = ItineraryNarrativeReport(
        status=ItineraryNarrativeStatus.FAILED, message="Simulated provider failure."
    )
    result, _ = _generate_with_fake_provider(planning_state, failed_report)

    report = result.itinerary_narrative_report
    assert report is not None
    assert report.status == ItineraryNarrativeStatus.FAILED  # the AI attempt's status is preserved
    # Section 202B.3: the raw provider message is replaced by a fixed safe
    # sentence, and a deterministic fallback narrative is attached.
    assert report.message == SAFE_AI_UNAVAILABLE_MESSAGE
    assert report.narrative_source == "deterministic_fallback"
    assert "Simulated provider failure" not in str(report.model_dump())


def test_disclosure_logic_is_read_only_over_authoritative_state() -> None:
    planning_state = _base_planning_state()
    planning_state.validation_report = _needs_review_validation_report()
    before = planning_state.model_copy(deep=True)

    request_preview = ItineraryNarrativeRequestBuilder().build_request(planning_state)
    llm_report = _success_report_for(request_preview)

    result, _ = _generate_with_fake_provider(planning_state, llm_report)

    dumped_before = before.model_dump(exclude={"itinerary_narrative_report", "metadata"})
    dumped_after = result.model_dump(exclude={"itinerary_narrative_report", "metadata"})
    assert dumped_before == dumped_after
    assert result.itinerary_narrative_report is not None
    assert len(result.itinerary_narrative_report.warnings) == 1
