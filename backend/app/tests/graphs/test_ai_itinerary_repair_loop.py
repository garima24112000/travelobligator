from __future__ import annotations

from typing import Any

import pytest

from app.core.config import get_settings
from app.graphs import PlanningGraphRunner
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
    build_candidate_id,
)
from app.models.ai_itinerary_repair import AIItineraryRepairResult, AIItineraryRepairStatus
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
from app.providers.ai_itinerary_reasoning.base import AIItineraryReasoningProvider
from app.services.ai_itinerary_repair_service import AIItineraryRepairService

# Full-graph integration tests for Section 194B's bounded automatic AI
# repair loop (docs/14_backend_architecture.md, following section 144).
# Every test here uses only real, unfaked ExperiencePlannerService/
# RouteFeasibilityService/RouteAwareSequencingService/
# TravelTimeBufferService/PlanValidatorService/AIItineraryRepairService
# (Section 194A) -- only the destination-context/reasoning INPUT and the
# repair PROVIDER (the actual "LLM inference" boundary) are faked, so the
# real geographic_spread validator finding, the real 194A classifier, the
# real merge, and the real downstream rerun are all genuinely exercised.

_PROVIDER_NAME = "openstreetmap_places"

# Central-Lisbon cluster (tight, well under PlanValidatorService's real
# 8.0 km geographic_spread threshold) -- day 1, always fine.
_DAY1_POIS: list[tuple[str, str, float, float]] = [
    ("day1/a", "Castelo de Sao Jorge", 38.7139, -9.1335),
    ("day1/b", "Praca do Comercio", 38.7075, -9.1364),
]
# Day 2, spread variant: mixes a central-Lisbon point with two Belem-area
# points -- consecutive straight-line distance in this order comfortably
# exceeds 8.0 km, computed purely from real coordinates (never dependent
# on a routing provider being connected).
_DAY2_SPREAD: list[tuple[str, str, float, float]] = [
    ("day2/a", "Alfama Viewpoint", 38.7122, -9.1291),
    ("day2/b", "Torre de Belem", 38.6916, -9.2160),
    ("day2/c", "Mosteiro dos Jeronimos", 38.6979, -9.2065),
]
# Day 2, tight variant: only the central-Lisbon point -- what a real
# successful repair (dropping the two Belem stops) produces.
_DAY2_TIGHT: list[tuple[str, str, float, float]] = [_DAY2_SPREAD[0]]


def _poi(entry: tuple[str, str, float, float]) -> dict[str, Any]:
    place_id, name, lat, lng = entry
    return {
        "place_id": place_id,
        "name": name,
        "category": "museum",
        "coordinates": {"lat": lat, "lng": lng},
        "source": _PROVIDER_NAME,
        "data_status": "live",
        "confidence": 0.85,
    }


def _cid(entry: tuple[str, str, float, float]) -> str:
    return build_candidate_id(_PROVIDER_NAME, entry[0])


class _FakeDestinationContextService:
    def __init__(self, day2_pois: list[tuple[str, str, float, float]]) -> None:
        self._day2_pois = day2_pois

    def run(self, planning_state: PlanningState) -> PlanningState:
        planning_state.destination_context = DestinationContext(
            destination_name=planning_state.trip_request.primary_destination,
            candidate_pois=[_poi(p) for p in _DAY1_POIS + self._day2_pois],
            candidate_restaurants=[],
            candidate_accommodation_pois=[],
        )
        return planning_state


class _FakeReasoningService:
    """Duck-typed stand-in for `AIItineraryReasoningService` -- always
    reports a completed result scheduling `_DAY1_POIS` on day 1 and
    `day2_pois` on day 2. Never calls a real provider/LLM."""

    def __init__(self, day2_pois: list[tuple[str, str, float, float]]) -> None:
        self._day2_pois = day2_pois

    def apply(self, planning_state: PlanningState) -> PlanningState:
        planning_state.ai_itinerary_reasoning_result = AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.COMPLETED,
            strategy=ItineraryReasoningStrategy(
                summary="A 2-day Lisbon plan.", pace="balanced", reason="Fixture reason."
            ),
            days=[
                ItineraryReasoningDayPlan(
                    day_index=1,
                    candidate_ids=[_cid(p) for p in _DAY1_POIS],
                    rationale="Central Lisbon morning.",
                ),
                ItineraryReasoningDayPlan(
                    day_index=2,
                    candidate_ids=[_cid(p) for p in self._day2_pois],
                    rationale="Exploring further afield.",
                ),
            ],
            guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
            provider_name="fake_reasoning_provider",
            confidence=0.8,
        )
        return planning_state


class _FakeRepairProvider(AIItineraryReasoningProvider):
    """Duck-typed stand-in for a real Groq/Anthropic repair adapter --
    returns a caller-controlled sequence of `AIItineraryRepairResult`s,
    one per call, so a test can script exactly what "the LLM" does on
    each bounded attempt. Never calls a real provider/LLM."""

    provider_name = "fake_repair_provider"

    def __init__(self, results: list[AIItineraryRepairResult]) -> None:
        self._results = results
        self.call_count = 0
        self.requests: list[Any] = []

    def reason(self, request: Any) -> Any:
        raise NotImplementedError("Not exercised in this file.")

    def repair(self, request: Any) -> AIItineraryRepairResult:
        self.requests.append(request)
        result = self._results[min(self.call_count, len(self._results) - 1)]
        self.call_count += 1
        return result


def _completed_repair(candidate_ids: list[str], summary: str = "Repaired day 2.") -> AIItineraryRepairResult:
    return AIItineraryRepairResult(
        status=AIItineraryRepairStatus.COMPLETED,
        repaired_days=[
            ItineraryReasoningDayPlan(day_index=2, candidate_ids=candidate_ids, rationale=summary)
        ],
        repair_summary=summary,
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        provider_name="fake_repair_provider",
        confidence=0.7,
    )


def _rejected_repair(reason: str = "Simulated provider rejection.") -> AIItineraryRepairResult:
    return AIItineraryRepairResult(
        status=AIItineraryRepairStatus.REJECTED,
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=[reason]),
        provider_name="fake_repair_provider",
    )


def _not_connected_repair() -> AIItineraryRepairResult:
    return AIItineraryRepairResult(
        status=AIItineraryRepairStatus.NOT_CONNECTED,
        guardrail_report=AIItineraryReasoningGuardrailReport(
            passed=False, blocked_reasons=["Simulated provider not connected."]
        ),
        provider_name="fake_repair_provider",
    )


def _semantic_invalid_repair(kind: str) -> AIItineraryRepairResult:
    """A `completed`-status result that is internally Pydantic-valid but
    violates 194A's candidate-safety contract -- simulates an adversarial
    or buggy provider bypassing its own `validate_repair_result_against_request`
    call (only possible here because this fake provider skips that check
    entirely, unlike the real Groq/Anthropic adapters)."""
    if kind == "unknown_candidate":
        candidate_ids = ["made-up-provider:999"]
    elif kind == "unaffected_day_candidate":
        candidate_ids = [_cid(_DAY1_POIS[0])]  # day 1's own candidate, reused on day 2
    else:
        raise ValueError(kind)
    return AIItineraryRepairResult(
        status=AIItineraryRepairStatus.COMPLETED,
        repaired_days=[
            ItineraryReasoningDayPlan(day_index=2, candidate_ids=candidate_ids, rationale="Invalid repair.")
        ],
        repair_summary="Invalid repair.",
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        provider_name="fake_repair_provider",
        confidence=0.7,
    )


def _unaffected_day_mutation_repair() -> AIItineraryRepairResult:
    """A `completed`-status result that (invalidly) also restates day 1
    -- Task 23/22: an unaffected day must never be accepted as
    "repaired"."""
    return AIItineraryRepairResult(
        status=AIItineraryRepairStatus.COMPLETED,
        repaired_days=[
            ItineraryReasoningDayPlan(
                day_index=1, candidate_ids=[_cid(_DAY1_POIS[1])], rationale="Invalid: touched day 1."
            ),
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=[_cid(_DAY2_SPREAD[0])], rationale="Also repaired day 2."
            ),
        ],
        repair_summary="Invalid repair touching an unaffected day.",
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        provider_name="fake_repair_provider",
        confidence=0.7,
    )


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


@pytest.fixture()
def repair_enabled_env(monkeypatch: pytest.MonkeyPatch):
    """Enables both AI_ITINERARY_REASONING_ENABLED and
    AI_ITINERARY_REPAIR_ENABLED for the duration of one test, via
    process-level env vars (never touching the real `.env`), restoring
    both afterward."""

    def _apply(max_attempts: int = 1) -> None:
        monkeypatch.setenv("AI_ITINERARY_REASONING_ENABLED", "true")
        monkeypatch.setenv("AI_ITINERARY_REPAIR_ENABLED", "true")
        monkeypatch.setenv("AI_ITINERARY_REPAIR_MAX_ATTEMPTS", str(max_attempts))
        get_settings.cache_clear()

    yield _apply
    get_settings.cache_clear()


def _run_graph(
    day2_pois_initial: list[tuple[str, str, float, float]],
    repair_provider: _FakeRepairProvider | None,
    *,
    trip_request_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runner = PlanningGraphRunner(
        destination_context_service=_FakeDestinationContextService(day2_pois_initial),
        ai_itinerary_reasoning_service=_FakeReasoningService(day2_pois_initial),
        ai_itinerary_repair_service=(
            AIItineraryRepairService(provider=repair_provider) if repair_provider is not None else None
        ),
    )
    trip_request = _trip_request(**(trip_request_overrides or {}))
    planning_state = PlanningState(trip_request=trip_request)
    return runner.run(planning_state.trip_id, trip_request, planning_state)


def _has_geographic_spread_issue(planning_state: PlanningState) -> bool:
    if planning_state.validation_report is None:
        return False
    all_issues = (
        list(planning_state.validation_report.critical_issues)
        + list(planning_state.validation_report.warnings)
        + list(planning_state.validation_report.suggestions)
    )
    return any(issue.category == "geographic_spread" for issue in all_issues)


# ---------------------------------------------------------------------------
# Task 16: valid plan requires no repair.
# ---------------------------------------------------------------------------


def test_valid_plan_never_triggers_repair(repair_enabled_env: Any) -> None:
    repair_enabled_env(max_attempts=1)
    fake_provider = _FakeRepairProvider([_completed_repair([_cid(_DAY2_TIGHT[0])])])

    result = _run_graph(_DAY2_TIGHT, fake_provider)

    assert fake_provider.call_count == 0
    planning_state = result["planning_state"]
    assert planning_state.ai_itinerary_repair_attempt_count == 0
    assert planning_state.ai_itinerary_repair_result is None
    assert result["completed_nodes"].count("experience_planning") == 1
    assert result["completed_nodes"].count("route_feasibility") == 1
    assert result["completed_nodes"].count("route_aware_sequencing") == 1
    assert result["completed_nodes"].count("travel_time_buffer") == 1
    assert result["completed_nodes"].count("validation") == 1
    assert "final_state" in result["completed_nodes"]


# ---------------------------------------------------------------------------
# Task 17: only non-repairable findings -> no repair, issues preserved.
# ---------------------------------------------------------------------------


def test_only_non_repairable_findings_never_triggers_repair(repair_enabled_env: Any) -> None:
    repair_enabled_env(max_attempts=1)
    fake_provider = _FakeRepairProvider([_completed_repair([_cid(_DAY2_TIGHT[0])])])

    result = _run_graph(
        _DAY2_TIGHT,
        fake_provider,
        trip_request_overrides={"budget_min": 500.0, "budget_max": 1500.0},
    )

    assert fake_provider.call_count == 0
    planning_state = result["planning_state"]
    assert planning_state.ai_itinerary_repair_attempt_count == 0
    validation_report = planning_state.validation_report
    assert validation_report is not None
    assert any(issue.category == "budget" for issue in validation_report.warnings)
    assert result["completed_nodes"].count("validation") == 1


# ---------------------------------------------------------------------------
# Task 18: successful one-attempt repair -- the critical test.
# ---------------------------------------------------------------------------


def test_successful_one_attempt_repair_reruns_full_downstream_chain(repair_enabled_env: Any) -> None:
    repair_enabled_env(max_attempts=1)
    fake_provider = _FakeRepairProvider([_completed_repair([_cid(_DAY2_TIGHT[0])])])

    result = _run_graph(_DAY2_SPREAD, fake_provider)

    assert fake_provider.call_count == 1
    planning_state = result["planning_state"]
    assert planning_state.ai_itinerary_repair_attempt_count == 1
    assert planning_state.ai_itinerary_repair_result is not None
    assert planning_state.ai_itinerary_repair_result.status == AIItineraryRepairStatus.COMPLETED

    # Reasoning result updated: day 2 now only has the tight candidate.
    day2 = next(day for day in planning_state.ai_itinerary_reasoning_result.days if day.day_index == 2)
    assert day2.candidate_ids == [_cid(_DAY2_TIGHT[0])]
    # Day 1 preserved byte-for-byte.
    day1 = next(day for day in planning_state.ai_itinerary_reasoning_result.days if day.day_index == 1)
    assert day1.candidate_ids == [_cid(p) for p in _DAY1_POIS]

    # Full downstream chain reran exactly twice.
    for node_name in (
        "experience_planning",
        "route_feasibility",
        "route_aware_sequencing",
        "travel_time_buffer",
        "validation",
    ):
        assert result["completed_nodes"].count(node_name) == 2, node_name
    assert result["completed_nodes"].count("ai_itinerary_repair") == 1

    # Second validation is clean of the repairable issue.
    assert not _has_geographic_spread_issue(planning_state)

    assert "provider_coverage" in result["completed_nodes"]
    assert "final_state" in result["completed_nodes"]
    assert result["failed_nodes"] == []


# ---------------------------------------------------------------------------
# Task 19: still invalid with max attempts = 1 -> no second repair call.
# ---------------------------------------------------------------------------


def test_still_invalid_after_one_attempt_stops_at_max_attempts_one(repair_enabled_env: Any) -> None:
    repair_enabled_env(max_attempts=1)
    # The "repair" just restates the exact same spread candidates in the
    # same order -- a valid, safety-passing repair that does not actually
    # reduce the day's spread.
    no_op_repair = _completed_repair([_cid(p) for p in _DAY2_SPREAD])
    fake_provider = _FakeRepairProvider([no_op_repair])

    result = _run_graph(_DAY2_SPREAD, fake_provider)

    assert fake_provider.call_count == 1
    planning_state = result["planning_state"]
    assert planning_state.ai_itinerary_repair_attempt_count == 1
    assert result["completed_nodes"].count("ai_itinerary_repair") == 1
    assert result["completed_nodes"].count("experience_planning") == 2
    assert result["completed_nodes"].count("validation") == 2
    assert _has_geographic_spread_issue(planning_state)
    assert "provider_coverage" in result["completed_nodes"]
    assert "final_state" in result["completed_nodes"]


# ---------------------------------------------------------------------------
# Task 20: max attempts = 2 -- both a valid-after-second-repair and a
# still-invalid-after-second-repair variant, never a third call.
# ---------------------------------------------------------------------------


def test_max_attempts_two_valid_after_second_repair(repair_enabled_env: Any) -> None:
    repair_enabled_env(max_attempts=2)
    no_op_repair = _completed_repair([_cid(p) for p in _DAY2_SPREAD], summary="First attempt, no real change.")
    fixing_repair = _completed_repair([_cid(_DAY2_TIGHT[0])], summary="Second attempt, dropped far stops.")
    fake_provider = _FakeRepairProvider([no_op_repair, fixing_repair])

    result = _run_graph(_DAY2_SPREAD, fake_provider)

    assert fake_provider.call_count == 2
    planning_state = result["planning_state"]
    assert planning_state.ai_itinerary_repair_attempt_count == 2
    assert result["completed_nodes"].count("ai_itinerary_repair") == 2
    assert result["completed_nodes"].count("experience_planning") == 3
    assert result["completed_nodes"].count("validation") == 3
    assert not _has_geographic_spread_issue(planning_state)


def test_max_attempts_two_still_invalid_after_second_repair(repair_enabled_env: Any) -> None:
    repair_enabled_env(max_attempts=2)
    # Two distinct objects (never the same instance reused) -- a real
    # provider always constructs a fresh result per call, and the node's
    # own "was an invocation actually attempted" check relies on that.
    fake_provider = _FakeRepairProvider(
        [
            _completed_repair([_cid(p) for p in _DAY2_SPREAD]),
            _completed_repair([_cid(p) for p in _DAY2_SPREAD]),
        ]
    )

    result = _run_graph(_DAY2_SPREAD, fake_provider)

    assert fake_provider.call_count == 2
    planning_state = result["planning_state"]
    assert planning_state.ai_itinerary_repair_attempt_count == 2
    assert result["completed_nodes"].count("ai_itinerary_repair") == 2
    assert result["completed_nodes"].count("experience_planning") == 3
    assert result["completed_nodes"].count("validation") == 3
    assert _has_geographic_spread_issue(planning_state)
    assert "provider_coverage" in result["completed_nodes"]
    assert "final_state" in result["completed_nodes"]


def test_never_a_third_repair_call_regardless_of_outcome(repair_enabled_env: Any) -> None:
    """Loop-bound test: even if the fake provider WOULD happily return a
    third result, the graph topology itself makes a third call
    impossible once attempt_count reaches max_attempts."""
    repair_enabled_env(max_attempts=2)
    # A third entry that would only ever be reached by a bug -- proves
    # (via call_count) that it never is. Three distinct objects, never a
    # reused instance (see the identity-detection note above).
    fake_provider = _FakeRepairProvider(
        [
            _completed_repair([_cid(p) for p in _DAY2_SPREAD]),
            _completed_repair([_cid(p) for p in _DAY2_SPREAD]),
            _completed_repair([_cid(_DAY2_TIGHT[0])]),
        ]
    )

    result = _run_graph(_DAY2_SPREAD, fake_provider)

    assert fake_provider.call_count == 2
    assert result["planning_state"].ai_itinerary_repair_attempt_count == 2


# ---------------------------------------------------------------------------
# Task 21: repair provider failure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure_factory",
    [_rejected_repair, _not_connected_repair],
    ids=["rejected", "not_connected"],
)
def test_repair_provider_failure_is_recorded_honestly_and_generation_proceeds(
    repair_enabled_env: Any, failure_factory: Any
) -> None:
    repair_enabled_env(max_attempts=2)
    fake_provider = _FakeRepairProvider([failure_factory()])

    result = _run_graph(_DAY2_SPREAD, fake_provider)

    assert fake_provider.call_count == 1
    planning_state = result["planning_state"]
    assert planning_state.ai_itinerary_repair_attempt_count == 1
    assert planning_state.ai_itinerary_repair_result is not None
    assert planning_state.ai_itinerary_repair_result.status != AIItineraryRepairStatus.COMPLETED

    # No second downstream pass -- the original plan/validation remain.
    assert result["completed_nodes"].count("experience_planning") == 1
    assert result["completed_nodes"].count("route_feasibility") == 1
    assert result["completed_nodes"].count("validation") == 1
    day2 = next(day for day in planning_state.ai_itinerary_reasoning_result.days if day.day_index == 2)
    assert day2.candidate_ids == [_cid(p) for p in _DAY2_SPREAD]
    assert _has_geographic_spread_issue(planning_state)

    assert "provider_coverage" in result["completed_nodes"]
    assert "final_state" in result["completed_nodes"]
    assert result["failed_nodes"] == []


# ---------------------------------------------------------------------------
# Task 22: semantic-invalid repair -- rejected, no loop back, no
# fabricated candidate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["unknown_candidate", "unaffected_day_candidate"])
def test_semantic_invalid_repair_is_rejected_and_never_loops_back(
    repair_enabled_env: Any, kind: str
) -> None:
    repair_enabled_env(max_attempts=1)
    fake_provider = _FakeRepairProvider([_semantic_invalid_repair(kind)])

    result = _run_graph(_DAY2_SPREAD, fake_provider)

    assert fake_provider.call_count == 1
    planning_state = result["planning_state"]
    assert planning_state.ai_itinerary_repair_attempt_count == 1
    # The stored repair result is honestly downgraded to rejected --
    # never a stale "completed" label next to an unmodified plan.
    assert planning_state.ai_itinerary_repair_result.status == AIItineraryRepairStatus.REJECTED

    # No loop back: only one experience_planning/validation pass.
    assert result["completed_nodes"].count("experience_planning") == 1
    assert result["completed_nodes"].count("validation") == 1

    # Original reasoning result and plan are untouched -- no fabricated
    # candidate anywhere.
    day2 = next(day for day in planning_state.ai_itinerary_reasoning_result.days if day.day_index == 2)
    assert day2.candidate_ids == [_cid(p) for p in _DAY2_SPREAD]
    scheduled_ids = {
        f"{experience.provider_source}:{experience.provider_place_id}"
        for day in planning_state.experience_plan.daily_plans
        for experience in day.experiences
        if experience.provider_place_id is not None
    }
    assert "made-up-provider:999" not in scheduled_ids


def test_unaffected_day_mutation_repair_is_rejected() -> None:
    """A repair that also restates day 1 (not in affected_days) must
    never be accepted, even though day 2's own portion would otherwise
    be valid (Task 23)."""
    from app.models.ai_itinerary_repair import merge_repair_into_reasoning_result
    from app.services.ai_itinerary_repair_request_builder import AIItineraryRepairRequestBuilder

    day2 = _unaffected_day_mutation_repair()
    # Build a real request the same way the node would, then assert the
    # merge itself refuses this repair.
    planning_state = PlanningState(trip_request=_trip_request())
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_pois=[_poi(p) for p in _DAY1_POIS + _DAY2_SPREAD],
    )
    from datetime import datetime, timezone

    from app.models.candidate_quality import CandidateQualityReport, CandidateQualityScore, CandidateQualityTier, CandidateUseCase

    planning_state.candidate_quality_report = CandidateQualityReport(
        destination_name="Lisbon, Portugal",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[
            CandidateQualityScore(
                candidate_id=p[0],
                candidate_name=p[1],
                use_case=CandidateUseCase.ATTRACTION,
                quality_tier=CandidateQualityTier.GOOD_CANDIDATE,
                total_score=0.6,
            )
            for p in _DAY1_POIS + _DAY2_SPREAD
        ],
        restaurant_scores=[],
        accommodation_poi_scores=[],
    )
    original_result = AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.COMPLETED,
        strategy=ItineraryReasoningStrategy(summary="s", pace="balanced", reason="r"),
        days=[
            ItineraryReasoningDayPlan(
                day_index=1, candidate_ids=[_cid(p) for p in _DAY1_POIS], rationale="Day one."
            ),
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=[_cid(p) for p in _DAY2_SPREAD], rationale="Day two."
            ),
        ],
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        confidence=0.8,
    )
    planning_state.ai_itinerary_reasoning_result = original_result

    # Build a request the same way the real request builder would for
    # day 2 only.
    from app.models.ai_itinerary_repair import AIItineraryRepairIssue, AIItineraryRepairRequest, RepairableIssueType
    from app.models.ai_itinerary_reasoning import TravelerContextSummary
    from app.models.common import ValidationSeverity

    repair_request = AIItineraryRepairRequest(
        trip_id=planning_state.trip_id,
        destination_name="Lisbon, Portugal",
        start_date="2026-09-10",
        end_date="2026-09-11",
        trip_duration_days=2,
        traveler_context=TravelerContextSummary(travelers_count=2, travel_group_type="couple", pace="balanced"),
        allowed_candidates=AIItineraryRepairRequestBuilder()._reasoning_request_builder._allowed_candidates(
            planning_state
        ),
        original_days=original_result.days,
        affected_days=[2],
        issues=[
            AIItineraryRepairIssue(
                issue_type=RepairableIssueType.GEOGRAPHIC_SPREAD,
                day_index=2,
                source_category="geographic_spread",
                message="Day 2 is spread out.",
                severity=ValidationSeverity.WARNING,
            )
        ],
    )

    with pytest.raises(ValueError, match="failed repair-safety validation"):
        merge_repair_into_reasoning_result(original_result, repair_request, day2)


# ---------------------------------------------------------------------------
# Task 23: stale state cannot influence the second pass.
# ---------------------------------------------------------------------------


def test_second_pass_consumes_the_second_experience_plan_not_the_first(repair_enabled_env: Any) -> None:
    repair_enabled_env(max_attempts=1)
    fake_provider = _FakeRepairProvider([_completed_repair([_cid(_DAY2_TIGHT[0])])])

    result = _run_graph(_DAY2_SPREAD, fake_provider)

    planning_state = result["planning_state"]
    day2_names = {
        experience.name
        for day in planning_state.experience_plan.daily_plans
        if day.day_number == 2
        for experience in day.experiences
    }
    # Only the tight (post-repair) candidate remains scheduled on day 2 --
    # the pre-repair Belem candidates are gone from the FINAL plan,
    # proving route_feasibility/sequencing/buffer/validation all consumed
    # the second (post-repair) ExperiencePlan, not a stale first one.
    assert day2_names == {_DAY2_TIGHT[0][1]}
    for leg in planning_state.route_feasibility_report.legs:
        assert leg.from_experience_name in day2_names or leg.from_experience_name in {
            p[1] for p in _DAY1_POIS
        }


# ---------------------------------------------------------------------------
# Task 24: affected/unaffected day preservation after a real repair.
# ---------------------------------------------------------------------------


def test_unaffected_day_scheduling_is_unchanged_by_repair(repair_enabled_env: Any) -> None:
    repair_enabled_env(max_attempts=1)
    fake_provider = _FakeRepairProvider([_completed_repair([_cid(_DAY2_TIGHT[0])])])

    result = _run_graph(_DAY2_SPREAD, fake_provider)

    planning_state = result["planning_state"]
    day1 = next(day for day in planning_state.experience_plan.daily_plans if day.day_number == 1)
    day1_names = [experience.name for experience in day1.experiences]
    assert set(day1_names) == {p[1] for p in _DAY1_POIS}


# ---------------------------------------------------------------------------
# Task 25: factual provenance survives the repair loop for an
# AI-directed promoted candidate.
# ---------------------------------------------------------------------------


def test_promoted_candidate_provenance_survives_repair(repair_enabled_env: Any) -> None:
    from app.models.ai_candidate_promotion import AICandidatePromotionReport, PromotedAICandidate
    from app.models.common import GeoPoint
    from datetime import datetime, timezone

    repair_enabled_env(max_attempts=1)

    class _FakeDestinationContextServiceWithPromoted(_FakeDestinationContextService):
        pass

    # A promoted candidate lives on day 2 alongside the spread-triggering
    # candidates -- the repair keeps it (it is not the far outlier).
    promoted_cid = build_candidate_id(_PROVIDER_NAME, "way/promoted-1")

    class _FakeReasoningServiceWithPromoted(_FakeReasoningService):
        def apply(self, planning_state: PlanningState) -> PlanningState:
            planning_state.ai_itinerary_reasoning_result = AIItineraryReasoningResult(
                status=AIItineraryReasoningStatus.COMPLETED,
                strategy=ItineraryReasoningStrategy(summary="s", pace="balanced", reason="r"),
                days=[
                    ItineraryReasoningDayPlan(
                        day_index=1, candidate_ids=[_cid(p) for p in _DAY1_POIS], rationale="Day one."
                    ),
                    ItineraryReasoningDayPlan(
                        # Section 194B test note: `max_per_day` for a
                        # balanced-pace trip is 3, and
                        # `_resolve_ai_guided_day_groups` truncates a
                        # day's attractions to that cap in candidate_ids
                        # order -- so this day intentionally has exactly
                        # 3 candidate_ids (never 4), or the promoted one
                        # would be silently truncated before the repair
                        # even runs. `_DAY2_SPREAD[0]`/`[1]` (Alfama/
                        # Belem, ~7.9 km apart) plus the promoted
                        # candidate (~0.3 km from Alfama) together sum to
                        # well over the real 8.0 km geographic_spread
                        # threshold in this order.
                        day_index=2,
                        candidate_ids=[_cid(_DAY2_SPREAD[0]), _cid(_DAY2_SPREAD[1]), promoted_cid],
                        rationale="Day two with a promoted candidate.",
                    ),
                ],
                guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
                provider_name="fake_reasoning_provider",
                confidence=0.8,
            )
            return planning_state

    fake_provider = _FakeRepairProvider(
        [
            _completed_repair(
                [_cid(_DAY2_SPREAD[0]), promoted_cid], summary="Dropped Belem, kept the promoted candidate."
            )
        ]
    )

    runner = PlanningGraphRunner(
        destination_context_service=_FakeDestinationContextServiceWithPromoted(_DAY2_SPREAD),
        ai_itinerary_reasoning_service=_FakeReasoningServiceWithPromoted(_DAY2_SPREAD),
        ai_itinerary_repair_service=AIItineraryRepairService(provider=fake_provider),
    )
    trip_request = _trip_request()
    planning_state = PlanningState(trip_request=trip_request)
    planning_state.ai_candidate_promotion_report = AICandidatePromotionReport(
        trip_id=planning_state.trip_id,
        status="promoted",
        total_reviewed_candidates=1,
        promoted_count=1,
        skipped_count=0,
        promoted_candidates=[
            PromotedAICandidate(
                candidate_id="promoted_proposal_001",
                name="Miradouro Secreto",
                category="attraction",
                provider_place_id="way/promoted-1",
                provider_source=_PROVIDER_NAME,
                original_ai_candidate_id="proposal_001",
                quality_bucket="good_candidate",
                grounding_status="targeted_lookup",
                coordinates=GeoPoint(lat=38.7100, lng=-9.1300),
                confidence=0.7,
                data_status="live",
                promoted=True,
            )
        ],
        generated_at=datetime.now(timezone.utc),
    )

    result = runner.run(planning_state.trip_id, trip_request, planning_state)

    final_state = result["planning_state"]
    day2 = next(day for day in final_state.experience_plan.daily_plans if day.day_number == 2)
    promoted_item = next(item for item in day2.experiences if item.name == "Miradouro Secreto")
    assert promoted_item.promoted_from_ai is True
    assert promoted_item.provider_place_id == "way/promoted-1"
    assert promoted_item.provider_source == _PROVIDER_NAME
    assert promoted_item.coordinates.lat == 38.7100
    assert promoted_item.coordinates.lng == -9.1300


# ---------------------------------------------------------------------------
# Task 12/13: disabled/no-AI-reasoning regression -- behavior identical
# to Section 193C.
# ---------------------------------------------------------------------------


def test_repair_disabled_by_default_matches_193c_behavior() -> None:
    fake_provider = _FakeRepairProvider([_completed_repair([_cid(_DAY2_TIGHT[0])])])

    result = _run_graph(_DAY2_SPREAD, fake_provider)  # no repair_enabled_env fixture used

    assert fake_provider.call_count == 0
    planning_state = result["planning_state"]
    assert planning_state.ai_itinerary_repair_attempt_count == 0
    assert planning_state.ai_itinerary_repair_result is None
    assert result["completed_nodes"].count("experience_planning") == 1
    assert result["completed_nodes"].count("validation") == 1
    assert "provider_coverage" in result["completed_nodes"]


def test_repair_enabled_but_reasoning_disabled_never_repairs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ITINERARY_REPAIR_ENABLED", "true")
    get_settings.cache_clear()
    try:
        fake_provider = _FakeRepairProvider([_completed_repair([_cid(_DAY2_TIGHT[0])])])
        result = _run_graph(_DAY2_SPREAD, fake_provider)
    finally:
        monkeypatch.delenv("AI_ITINERARY_REPAIR_ENABLED", raising=False)
        get_settings.cache_clear()

    assert fake_provider.call_count == 0
    assert result["planning_state"].ai_itinerary_repair_attempt_count == 0
