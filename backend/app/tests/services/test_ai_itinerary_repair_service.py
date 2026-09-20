from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.core.config import Settings
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningRequest,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
)
from app.models.ai_itinerary_repair import AIItineraryRepairRequest, AIItineraryRepairResult, AIItineraryRepairStatus
from app.models.candidate_quality import CandidateQualityReport, CandidateQualityScore, CandidateQualityTier, CandidateUseCase
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest, ValidationIssue, ValidationReport
from app.models.common import ValidationSeverity
from app.providers.ai_itinerary_reasoning.base import AIItineraryReasoningProvider
from app.services import ai_itinerary_repair_service as service_module
from app.services.ai_itinerary_repair_request_builder import AIItineraryRepairRequestBuilder
from app.services.ai_itinerary_repair_service import AIItineraryRepairService, apply_repair_safely

# Tests for the Section 194A repair service layer (docs/14_backend_
# architecture.md, following section 143). No real provider/network call
# anywhere here.


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


def _poi(place_id: str, name: str) -> dict[str, Any]:
    return {
        "place_id": place_id,
        "name": name,
        "category": "attraction",
        "coordinates": {"lat": 38.7, "lng": -9.1},
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": 0.8,
    }


def _planning_state_with_completed_reasoning(*, with_repairable_issue: bool) -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request())
    planning_state.destination_context = DestinationContext(
        destination_name="Lisbon, Portugal",
        candidate_pois=[_poi("way/A", "A"), _poi("way/B", "B")],
    )
    planning_state.candidate_quality_report = CandidateQualityReport(
        destination_name="Lisbon, Portugal",
        generated_at=datetime.now(timezone.utc),
        attraction_scores=[
            CandidateQualityScore(
                candidate_id="way/A",
                candidate_name="A",
                use_case=CandidateUseCase.ATTRACTION,
                quality_tier=CandidateQualityTier.GOOD_CANDIDATE,
                total_score=0.6,
            ),
            CandidateQualityScore(
                candidate_id="way/B",
                candidate_name="B",
                use_case=CandidateUseCase.ATTRACTION,
                quality_tier=CandidateQualityTier.GOOD_CANDIDATE,
                total_score=0.6,
            ),
        ],
        restaurant_scores=[],
        accommodation_poi_scores=[],
    )
    planning_state.ai_itinerary_reasoning_result = AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.COMPLETED,
        strategy=ItineraryReasoningStrategy(summary="s", pace="balanced", reason="r"),
        days=[
            ItineraryReasoningDayPlan(
                day_index=1,
                candidate_ids=["openstreetmap_places:way/A", "openstreetmap_places:way/B"],
                rationale="Day one.",
            )
        ],
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        confidence=0.8,
    )
    if with_repairable_issue:
        planning_state.validation_report = ValidationReport(
            warnings=[
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="geographic_spread",
                    message="Day 1 is geographically spread out.",
                    affected_section="experience_plan.daily_plans[1]",
                )
            ],
        )
    return planning_state


class _FakeProvider(AIItineraryReasoningProvider):
    provider_name = "fake_ai_itinerary_reasoning_provider"

    def __init__(self, result: AIItineraryRepairResult | None = None, *, raises: bool = False) -> None:
        self._result = result
        self._raises = raises
        self.call_count = 0
        self.last_request: AIItineraryRepairRequest | None = None

    def reason(self, request: AIItineraryReasoningRequest) -> AIItineraryReasoningResult:
        raise NotImplementedError("Not exercised in this file.")

    def repair(self, request: AIItineraryRepairRequest) -> AIItineraryRepairResult:
        self.call_count += 1
        self.last_request = request
        if self._raises:
            raise RuntimeError("simulated provider crash")
        return self._result if self._result is not None else _completed_repair_result()


def _completed_repair_result() -> AIItineraryRepairResult:
    return AIItineraryRepairResult(
        status=AIItineraryRepairStatus.COMPLETED,
        repaired_days=[
            ItineraryReasoningDayPlan(
                day_index=1, candidate_ids=["openstreetmap_places:way/A"], rationale="Dropped B."
            )
        ],
        repair_summary="Removed B to reduce spread.",
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
        provider_name="fake_ai_itinerary_reasoning_provider",
        confidence=0.7,
    )


# ---------------------------------------------------------------------------
# Task 18: disabled -> not_connected, request never built, provider never
# resolved/called.
# ---------------------------------------------------------------------------


def test_disabled_by_default_never_builds_request_or_calls_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "get_settings", lambda: Settings(_env_file=None))

    def _fail_build_request(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("build_request must not be called while disabled")

    builder = AIItineraryRepairRequestBuilder()
    monkeypatch.setattr(builder, "build_request", _fail_build_request)
    fake_provider = _FakeProvider()
    service = AIItineraryRepairService(request_builder=builder, provider=fake_provider)
    planning_state = _planning_state_with_completed_reasoning(with_repairable_issue=True)

    result = service.repair(planning_state)

    assert result.status == AIItineraryRepairStatus.NOT_CONNECTED
    assert result.repaired_days == []
    assert fake_provider.call_count == 0


# ---------------------------------------------------------------------------
# Task 26: no repairable issues -> skipped, provider never called.
# ---------------------------------------------------------------------------


def test_no_repairable_issues_never_calls_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    fake_provider = _FakeProvider()
    service = AIItineraryRepairService(provider=fake_provider)
    planning_state = _planning_state_with_completed_reasoning(with_repairable_issue=False)

    result = service.repair(planning_state)

    assert result.status == AIItineraryRepairStatus.SKIPPED
    assert result.repaired_days == []
    assert fake_provider.call_count == 0


# ---------------------------------------------------------------------------
# Enabled + repairable issue -> provider is actually called and its
# result is returned honestly.
# ---------------------------------------------------------------------------


def test_enabled_with_repairable_issue_calls_provider_and_returns_its_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    fake_provider = _FakeProvider()
    service = AIItineraryRepairService(provider=fake_provider)
    planning_state = _planning_state_with_completed_reasoning(with_repairable_issue=True)

    result = service.repair(planning_state)

    assert result.status == AIItineraryRepairStatus.COMPLETED
    assert fake_provider.call_count == 1
    assert fake_provider.last_request is not None
    assert fake_provider.last_request.affected_days == [1]


# ---------------------------------------------------------------------------
# Task 27: provider failure -> safe result, no mutated reasoning/plan.
# ---------------------------------------------------------------------------


def test_provider_raising_is_not_swallowed_by_repair_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    """`repair` itself does not catch provider exceptions (mirrors
    `AIItineraryReasoningService.reason`) -- `apply_repair_safely` is the
    fail-safe boundary, tested separately below."""
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    fake_provider = _FakeProvider(raises=True)
    service = AIItineraryRepairService(provider=fake_provider)
    planning_state = _planning_state_with_completed_reasoning(with_repairable_issue=True)

    with pytest.raises(RuntimeError, match="simulated provider crash"):
        service.repair(planning_state)


def test_apply_safely_leaves_planning_state_unchanged_on_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    fake_provider = _FakeProvider(raises=True)
    service = AIItineraryRepairService(provider=fake_provider)
    planning_state = _planning_state_with_completed_reasoning(with_repairable_issue=True)
    before = planning_state.model_copy(deep=True)

    result = apply_repair_safely(planning_state, service)

    assert result == before
    assert result.ai_itinerary_repair_result is None


def test_rejected_provider_result_is_stored_honestly() -> None:
    rejected = AIItineraryRepairResult(
        status=AIItineraryRepairStatus.REJECTED,
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=False, blocked_reasons=["bad output"]),
    )
    fake_provider = _FakeProvider(result=rejected)
    from unittest.mock import patch

    with patch.object(service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)):
        service = AIItineraryRepairService(provider=fake_provider)
        planning_state = _planning_state_with_completed_reasoning(with_repairable_issue=True)
        result = service.repair(planning_state)

    assert result.status == AIItineraryRepairStatus.REJECTED
    assert result.repaired_days == []


# ---------------------------------------------------------------------------
# apply() touches only ai_itinerary_repair_result.
# ---------------------------------------------------------------------------


def test_apply_populates_only_ai_itinerary_repair_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    fake_provider = _FakeProvider()
    service = AIItineraryRepairService(provider=fake_provider)
    planning_state = _planning_state_with_completed_reasoning(with_repairable_issue=True)
    before = planning_state.model_copy(deep=True)

    result = service.apply(planning_state)

    assert result.ai_itinerary_repair_result is not None
    assert result.ai_itinerary_repair_result.status == AIItineraryRepairStatus.COMPLETED
    dumped_result = result.model_dump(exclude={"ai_itinerary_repair_result", "metadata"})
    dumped_before = before.model_dump(exclude={"ai_itinerary_repair_result", "metadata"})
    assert dumped_result == dumped_before


def test_injected_provider_bypasses_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail_factory(provider_name: str | None = None) -> None:
        raise AssertionError("get_ai_itinerary_reasoning_provider must not be called when injected")

    monkeypatch.setattr(service_module, "get_ai_itinerary_reasoning_provider", _fail_factory)
    monkeypatch.setattr(
        service_module, "get_settings", lambda: Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED=True)
    )
    fake_provider = _FakeProvider()
    service = AIItineraryRepairService(provider=fake_provider)
    planning_state = _planning_state_with_completed_reasoning(with_repairable_issue=True)

    result = service.repair(planning_state)

    assert result.status == AIItineraryRepairStatus.COMPLETED
    assert fake_provider.call_count == 1
