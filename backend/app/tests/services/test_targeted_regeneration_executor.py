from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningRequest,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    AIItineraryReasoningGuardrailReport,
    CandidateOrigin,
    ItineraryCandidateReference,
    ItineraryReasoningCategory,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
)
from app.models.ai_itinerary_repair import AIItineraryRepairResult, AIItineraryRepairStatus
from app.models.common import DataStatus, GeoPoint, ProviderStatus, ValidationSeverity
from app.models.planning_state import (
    DailyPlan,
    ExperiencePlan,
    ExperienceItem,
    PlanningState,
    PlanningStage,
    TravelGroupType,
    TravelerProfile,
    TripPace,
    TripRequest,
    UserLock,
    ValidationIssue,
    ValidationReport,
)
from app.models.providers import NormalizedPlace, ProviderResponse, ProviderType
from app.models.targeted_regeneration_execution import TargetedRegenerationExecutionStatus
from app.models.targeted_regeneration_plan import (
    TargetedDayInstruction,
    TargetedExperienceMove,
    TargetedExperienceRemoval,
    TargetedNewPlaceLookup,
    TargetedRegenerationPlan,
    TargetedRegenerationPlanStatus,
    TravelerProfileMutation,
)
from app.services.ai_itinerary_reasoning_request_builder import AIItineraryReasoningRequestBuilder
from app.services.ai_itinerary_repair_request_builder import AIItineraryRepairRequestBuilder
from app.services.targeted_regeneration_executor import TargetedRegenerationExecutor

# Tests for the Section 197B targeted-regeneration EXECUTOR
# (docs/14_backend_architecture.md, following section 148). No real
# provider/LLM call anywhere in this file -- every dependency is either
# the real deterministic service (routing/validation/narrator, which are
# themselves safe to call with ROUTING_PROVIDER=not_connected, the test
# default) or an injected fake.


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _planning_state() -> PlanningState:
    planning_state = PlanningState(trip_request=_trip_request())
    day1 = DailyPlan(
        day_number=1,
        date=date(2026, 9, 10),
        experiences=[
            _experience(
                "exp_A",
                "Museum A",
                1,
                provider_place_id="node/1",
                provider_source="openstreetmap_places",
                coordinates=GeoPoint(lat=38.70, lng=-9.13),
            ),
            _experience("exp_B", "Park B", 1),
        ],
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
    planning_state.traveler_profile = TravelerProfile(
        travel_group_type=TravelGroupType.COUPLE,
        travelers_count=2,
        pace=TripPace.BALANCED,
        interests=["history"],
    )
    return planning_state


def _plan(**overrides: Any) -> TargetedRegenerationPlan:
    fields: dict[str, Any] = {
        "trip_id": "trip_x",
        "source_version": "v1",
        "status": TargetedRegenerationPlanStatus.READY,
        "affected_day_indices": [],
        "preserved_day_indices": [1, 2, 3],
        "required_stages": [],
    }
    fields.update(overrides)
    return TargetedRegenerationPlan(**fields)


def _day(day_number: int, planning_state: PlanningState) -> DailyPlan:
    assert planning_state.experience_plan is not None
    return next(d for d in planning_state.experience_plan.daily_plans if d.day_number == day_number)


class _FakeReasoningProvider:
    provider_name = "fake_reasoning_provider"

    def __init__(self, result: AIItineraryReasoningResult | None = None, exception: Exception | None = None) -> None:
        self._result = result
        self._exception = exception
        self.last_request: AIItineraryReasoningRequest | None = None

    def reason(self, request: AIItineraryReasoningRequest) -> AIItineraryReasoningResult:
        self.last_request = request
        if self._exception is not None:
            raise self._exception
        assert self._result is not None
        return self._result

    def repair(self, request: Any) -> Any:
        raise NotImplementedError


class _FixedCandidatesRequestBuilder:
    """Reuses the real request builder for every field except
    `allowed_candidates`, which is fixed by the test -- avoids needing a
    fully populated `destination_context`/`candidate_quality_report`
    just to exercise scoped-reasoning candidate restriction."""

    def __init__(self, allowed_candidates: list[ItineraryCandidateReference]) -> None:
        self._allowed_candidates = allowed_candidates
        self._real_builder = AIItineraryReasoningRequestBuilder()

    def build_request(self, planning_state: PlanningState) -> AIItineraryReasoningRequest:
        base = self._real_builder.build_request(planning_state)
        return base.model_copy(update={"allowed_candidates": self._allowed_candidates})


def _candidate(
    candidate_id: str, name: str, provider_name: str, provider_place_id: str
) -> ItineraryCandidateReference:
    return ItineraryCandidateReference(
        candidate_id=candidate_id,
        name=name,
        category=ItineraryReasoningCategory.ATTRACTION,
        provider_name=provider_name,
        provider_place_id=provider_place_id,
        coordinates=GeoPoint(lat=38.71, lng=-9.14),
        data_status=DataStatus.LIVE,
        quality_score=0.7,
        quality_tier="good_candidate",
        origin=CandidateOrigin.BROAD_PROVIDER_DISCOVERY,
    )


def _completed_reasoning_result(days: list[ItineraryReasoningDayPlan]) -> AIItineraryReasoningResult:
    return AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.COMPLETED,
        strategy=ItineraryReasoningStrategy(summary="Keep it relaxed.", pace="relaxed", reason="User requested a calmer pace."),
        days=days,
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True, checked_fields=["candidate_ids"]),
        confidence=0.9,
    )


@pytest.fixture(autouse=True)
def _enable_reasoning_and_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ai_itinerary_reasoning_enabled", True, raising=False)
    monkeypatch.setattr(get_settings(), "ai_itinerary_repair_enabled", True, raising=False)
    monkeypatch.setattr(get_settings(), "ai_itinerary_repair_max_attempts", 1, raising=False)


# ---------------------------------------------------------------------------
# Task 36: deterministic removal.
# ---------------------------------------------------------------------------


def test_deterministic_removal_preserves_other_days_exactly() -> None:
    planning_state = _planning_state()
    original_day1 = _day(1, planning_state).model_copy(deep=True)
    original_day3 = _day(3, planning_state).model_copy(deep=True)
    plan = _plan(
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        affected_experience_ids=["exp_C"],
        experience_removals=[
            TargetedExperienceRemoval(
                experience_id="exp_C", day_index=2, candidate_id="openstreetmap_places:way/24341353"
            )
        ],
        required_stages=[PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION],
    )

    result = TargetedRegenerationExecutor().execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    assert _day(1, final).model_dump() == original_day1.model_dump()
    assert _day(3, final).model_dump() == original_day3.model_dump()
    assert {e.experience_id for e in _day(2, final).experiences} == {"exp_D"}
    assert result.reasoning_status == "not_required"
    assert result.provider_lookup_status == "not_required"
    assert result.routing_rerun is True
    assert result.validation_status is not None
    assert result.preservation_audit_passed is True
    # source untouched
    assert {e.experience_id for e in _day(2, planning_state).experiences} == {"exp_C", "exp_D"}


# ---------------------------------------------------------------------------
# Task 37: deterministic move.
# ---------------------------------------------------------------------------


def test_deterministic_move_preserves_identity_and_other_days() -> None:
    planning_state = _planning_state()
    original_day1 = _day(1, planning_state).model_copy(deep=True)
    plan = _plan(
        affected_day_indices=[2, 3],
        preserved_day_indices=[1],
        affected_experience_ids=["exp_C"],
        experience_moves=[
            TargetedExperienceMove(
                experience_id="exp_C",
                source_day_index=2,
                target_day_index=3,
                candidate_id="openstreetmap_places:way/24341353",
                coordinates=GeoPoint(lat=38.6916, lng=-9.2160),
            )
        ],
        required_stages=[PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION],
    )

    result = TargetedRegenerationExecutor().execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    assert _day(1, final).model_dump() == original_day1.model_dump()
    day2_ids = [e.experience_id for e in _day(2, final).experiences]
    day3_ids = [e.experience_id for e in _day(3, final).experiences]
    assert "exp_C" not in day2_ids
    assert day3_ids.count("exp_C") == 1
    moved = next(e for e in _day(3, final).experiences if e.experience_id == "exp_C")
    assert moved.provider_place_id == "way/24341353"
    assert moved.provider_source == "openstreetmap_places"
    assert moved.coordinates is not None and moved.coordinates.lat == 38.6916


# ---------------------------------------------------------------------------
# Task 38: scoped day regeneration success.
# ---------------------------------------------------------------------------


def test_scoped_day_regeneration_changes_only_affected_day() -> None:
    planning_state = _planning_state()
    original_day1 = _day(1, planning_state).model_copy(deep=True)
    original_day3 = _day(3, planning_state).model_copy(deep=True)

    reserved = _candidate("openstreetmap_places:node/1", "Museum A", "openstreetmap_places", "node/1")
    free = _candidate("openstreetmap_places:node/999", "New Spot", "openstreetmap_places", "node/999")

    fake_result = _completed_reasoning_result(
        [
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=["openstreetmap_places:node/999"], rationale="Relaxed day 2."
            )
        ]
    )
    fake_provider = _FakeReasoningProvider(result=fake_result)

    plan = _plan(
        interpretation_scope=None,
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        day_instructions=[TargetedDayInstruction(day_index=2, instruction="Make it more relaxed.")],
        requires_ai_itinerary_reasoning=True,
        required_stages=[PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION],
    )

    executor = TargetedRegenerationExecutor(
        reasoning_request_builder=_FixedCandidatesRequestBuilder([reserved, free]),
        reasoning_provider=fake_provider,
    )
    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    assert _day(1, final).model_dump() == original_day1.model_dump()
    assert _day(3, final).model_dump() == original_day3.model_dump()
    assert result.reasoning_status == "completed"
    # the fake provider never even saw the reserved candidate
    assert fake_provider.last_request is not None
    seen_ids = {c.candidate_id for c in fake_provider.last_request.allowed_candidates}
    assert "openstreetmap_places:node/1" not in seen_ids
    assert "openstreetmap_places:node/999" in seen_ids


# ---------------------------------------------------------------------------
# Task 39: reasoning attempts to modify a preserved day.
# ---------------------------------------------------------------------------


def test_reasoning_touching_preserved_day_is_rejected() -> None:
    planning_state = _planning_state()

    fake_result = _completed_reasoning_result(
        [
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=["openstreetmap_places:node/999"], rationale="Relaxed day 2."
            ),
            ItineraryReasoningDayPlan(
                day_index=1, candidate_ids=["openstreetmap_places:node/1"], rationale="Also changing day 1."
            ),
        ]
    )
    fake_provider = _FakeReasoningProvider(result=fake_result)
    free = _candidate("openstreetmap_places:node/999", "New Spot", "openstreetmap_places", "node/999")
    reserved = _candidate("openstreetmap_places:node/1", "Museum A", "openstreetmap_places", "node/1")

    plan = _plan(
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        day_instructions=[TargetedDayInstruction(day_index=2, instruction="Make it more relaxed.")],
        requires_ai_itinerary_reasoning=True,
    )

    executor = TargetedRegenerationExecutor(
        reasoning_request_builder=_FixedCandidatesRequestBuilder([reserved, free]),
        reasoning_provider=fake_provider,
    )
    before = planning_state.model_copy(deep=True)
    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.REASONING_FAILED
    assert result.resulting_planning_state is None
    assert planning_state.model_dump() == before.model_dump()


# ---------------------------------------------------------------------------
# Task 40: reasoning attempts to steal a candidate reserved by a preserved day.
# ---------------------------------------------------------------------------


def test_reasoning_stealing_reserved_candidate_is_rejected() -> None:
    planning_state = _planning_state()

    # The fake provider "cheats": it returns day 2 using the candidate
    # that belongs to preserved day 1 (exp_A), ignoring the restricted
    # candidate universe it was actually given.
    fake_result = _completed_reasoning_result(
        [
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=["openstreetmap_places:node/1"], rationale="Reusing museum A."
            )
        ]
    )
    fake_provider = _FakeReasoningProvider(result=fake_result)
    reserved = _candidate("openstreetmap_places:node/1", "Museum A", "openstreetmap_places", "node/1")
    free = _candidate("openstreetmap_places:node/999", "New Spot", "openstreetmap_places", "node/999")

    plan = _plan(
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        day_instructions=[TargetedDayInstruction(day_index=2, instruction="Make it more relaxed.")],
        requires_ai_itinerary_reasoning=True,
    )

    executor = TargetedRegenerationExecutor(
        reasoning_request_builder=_FixedCandidatesRequestBuilder([reserved, free]),
        reasoning_provider=fake_provider,
    )
    before = planning_state.model_copy(deep=True)
    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.REASONING_FAILED
    assert result.resulting_planning_state is None
    assert planning_state.model_dump() == before.model_dump()


# ---------------------------------------------------------------------------
# Task 41: route-aware sequencing must not reorder a preserved day.
# ---------------------------------------------------------------------------


def test_route_aware_sequencing_never_reorders_a_preserved_day() -> None:
    planning_state = _planning_state()
    original_day1_order = [e.experience_id for e in _day(1, planning_state).experiences]
    plan = _plan(
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        affected_experience_ids=["exp_C"],
        experience_removals=[
            TargetedExperienceRemoval(experience_id="exp_D", day_index=2, candidate_id=None)
        ],
        required_stages=[PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION],
    )

    result = TargetedRegenerationExecutor().execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    final_day1_order = [e.experience_id for e in _day(1, final).experiences]
    assert final_day1_order == original_day1_order


# ---------------------------------------------------------------------------
# Task 42: global pace mutation.
# ---------------------------------------------------------------------------


def test_global_pace_mutation_applies_to_working_state_only() -> None:
    planning_state = _planning_state()
    before_profile_pace = planning_state.traveler_profile.pace

    fake_result = _completed_reasoning_result(
        [
            ItineraryReasoningDayPlan(day_index=1, candidate_ids=["openstreetmap_places:node/1"], rationale="r1"),
            ItineraryReasoningDayPlan(day_index=2, candidate_ids=["openstreetmap_places:node/999"], rationale="r2"),
            ItineraryReasoningDayPlan(day_index=3, candidate_ids=["openstreetmap_places:node/998"], rationale="r3"),
        ]
    )
    fake_provider = _FakeReasoningProvider(result=fake_result)
    candidates = [
        _candidate("openstreetmap_places:node/1", "Museum A", "openstreetmap_places", "node/1"),
        _candidate("openstreetmap_places:node/999", "New Spot", "openstreetmap_places", "node/999"),
        _candidate("openstreetmap_places:node/998", "Another Spot", "openstreetmap_places", "node/998"),
    ]

    plan = _plan(
        affected_day_indices=[1, 2, 3],
        preserved_day_indices=[],
        traveler_profile_mutation=TravelerProfileMutation(pace="relaxed"),
        requires_ai_itinerary_reasoning=True,
        required_stages=[PlanningStage.TRAVELER_PROFILE, PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION],
    )

    executor = TargetedRegenerationExecutor(
        reasoning_request_builder=_FixedCandidatesRequestBuilder(candidates),
        reasoning_provider=fake_provider,
    )
    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    assert final.traveler_profile.pace.value == "relaxed"
    assert planning_state.traveler_profile.pace == before_profile_pace


# ---------------------------------------------------------------------------
# Task 43/44: new-place grounding success and failure.
# ---------------------------------------------------------------------------


def _place_response(found: bool) -> ProviderResponse[list[NormalizedPlace]]:
    if not found:
        return ProviderResponse(
            provider_name="openstreetmap_places",
            provider_type=ProviderType.PLACES,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=[],
        )
    return ProviderResponse(
        provider_name="openstreetmap_places",
        provider_type=ProviderType.PLACES,
        status=ProviderStatus.SUCCESS,
        data_status=DataStatus.LIVE,
        data=[
            NormalizedPlace(
                place_id="node/5000",
                name="Sintra",
                category="attraction",
                coordinates=GeoPoint(lat=38.7999, lng=-9.3903),
                source="openstreetmap_places",
                data_status=DataStatus.LIVE,
                confidence=0.9,
            )
        ],
    )


class _FakeGatewayPlaces:
    def __init__(self, response: ProviderResponse[Any]) -> None:
        self._response = response

    def search_must_visit_place(self, query: str, destination_name: str, filters: dict | None = None) -> Any:
        return self._response


class _FakeGateway:
    def __init__(self, response: ProviderResponse[Any]) -> None:
        self.places = _FakeGatewayPlaces(response)


def test_new_place_success_grounds_via_real_provider_shape() -> None:
    planning_state = _planning_state()

    fake_result = _completed_reasoning_result(
        [ItineraryReasoningDayPlan(day_index=1, candidate_ids=["openstreetmap_places:node/5000"], rationale="Add Sintra.")]
    )
    fake_provider = _FakeReasoningProvider(result=fake_result)

    # Section 197B.1: a pure additive request's real 197A output is
    # affected_day_indices=[] / preserved_day_indices=[all days] -- the
    # additive execution path derives its own insertion day itself and
    # does not consult these fields.
    plan = _plan(
        affected_day_indices=[],
        preserved_day_indices=[1, 2, 3],
        new_place_lookups=[TargetedNewPlaceLookup(query="Sintra")],
        requires_ai_itinerary_reasoning=True,
    )

    executor = TargetedRegenerationExecutor(
        gateway=_FakeGateway(_place_response(found=True)),
        reasoning_provider=fake_provider,
    )
    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    assert result.provider_lookup_status == "grounded"
    final = result.resulting_planning_state
    assert final is not None
    all_experiences = [e for day in final.experience_plan.daily_plans for e in day.experiences]
    sintra = next((e for e in all_experiences if e.provider_place_id == "node/5000"), None)
    assert sintra is not None, "the grounded new place must actually be scheduled"
    assert sintra.provider_source == "openstreetmap_places"
    assert sintra.coordinates is not None
    assert sintra.coordinates.lat == 38.7999 and sintra.coordinates.lng == -9.3903
    forbidden_keys = {"rating", "opening_hours", "booking_url", "review_count", "price"}
    assert forbidden_keys.isdisjoint(sintra.model_dump().keys())


def test_new_place_provider_unavailable_never_fabricates() -> None:
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)
    plan = _plan(
        affected_day_indices=[],
        preserved_day_indices=[1, 2, 3],
        new_place_lookups=[TargetedNewPlaceLookup(query="Nonexistent Place XYZ")],
        requires_ai_itinerary_reasoning=True,
    )

    executor = TargetedRegenerationExecutor(gateway=_FakeGateway(_place_response(found=False)))
    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.PROVIDER_UNAVAILABLE
    assert result.resulting_planning_state is None
    assert result.provider_lookup_status == "unavailable"
    assert planning_state.model_dump() == before.model_dump()


# ---------------------------------------------------------------------------
# Task 45: stale version.
# ---------------------------------------------------------------------------


def test_stale_source_version_is_blocked_before_any_work() -> None:
    planning_state = _planning_state()
    planning_state.metadata.current_version = "v3"
    plan = _plan(
        source_version="v2",
        affected_day_indices=[2],
        experience_removals=[TargetedExperienceRemoval(experience_id="exp_C", day_index=2)],
    )

    result = TargetedRegenerationExecutor().execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.BLOCKED
    assert result.resulting_planning_state is None
    assert any("stale" in r.lower() or "version" in r.lower() for r in result.block_reasons)


# ---------------------------------------------------------------------------
# Task 46: lock introduced after compile (TOCTOU).
# ---------------------------------------------------------------------------


def test_lock_added_after_compile_blocks_execution() -> None:
    planning_state = _planning_state()
    planning_state.user_locks = [UserLock(locked_item_type="experience", locked_item_id="exp_A", is_active=True)]
    plan = _plan(
        affected_day_indices=[2],
        experience_removals=[
            TargetedExperienceRemoval(experience_id="exp_C", day_index=2, candidate_id="openstreetmap_places:way/24341353")
        ],
    )

    result = TargetedRegenerationExecutor().execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.BLOCKED
    assert result.resulting_planning_state is None
    assert any("lock" in r.lower() for r in result.block_reasons)


# ---------------------------------------------------------------------------
# Task 49: narrator reflects the final regenerated state.
# ---------------------------------------------------------------------------


def test_narrator_runs_on_final_state_after_removal() -> None:
    planning_state = _planning_state()
    plan = _plan(
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        experience_removals=[
            TargetedExperienceRemoval(experience_id="exp_C", day_index=2, candidate_id="openstreetmap_places:way/24341353")
        ],
    )

    result = TargetedRegenerationExecutor().execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    assert result.narrator_status is not None
    final = result.resulting_planning_state
    assert final is not None
    remaining_ids = {e.experience_id for day in final.experience_plan.daily_plans for e in day.experiences}
    assert "exp_C" not in remaining_ids


# ---------------------------------------------------------------------------
# Task 48: repair attempt budget resets per targeted regeneration.
# ---------------------------------------------------------------------------


def test_repair_attempt_budget_resets_for_new_execution() -> None:
    planning_state = _planning_state()
    planning_state.ai_itinerary_repair_attempt_count = 1
    plan = _plan(
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        experience_removals=[
            TargetedExperienceRemoval(experience_id="exp_D", day_index=2, candidate_id=None)
        ],
    )

    result = TargetedRegenerationExecutor().execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    # Working state's repair budget was reset to 0 before any repair
    # consideration -- source's stale count of 1 is left untouched.
    assert planning_state.ai_itinerary_repair_attempt_count == 1


# ---------------------------------------------------------------------------
# Task 50: failure atomicity across a few distinct failure points.
# ---------------------------------------------------------------------------


def test_reasoning_exception_leaves_source_untouched() -> None:
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)
    plan = _plan(
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        day_instructions=[TargetedDayInstruction(day_index=2, instruction="Make it more relaxed.")],
        requires_ai_itinerary_reasoning=True,
    )

    executor = TargetedRegenerationExecutor(
        reasoning_provider=_FakeReasoningProvider(exception=RuntimeError("simulated provider failure"))
    )
    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.REASONING_FAILED
    assert result.resulting_planning_state is None
    assert planning_state.model_dump() == before.model_dump()


def test_unexpected_exception_during_materialization_is_caught() -> None:
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)
    plan = _plan(
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        experience_removals=[
            TargetedExperienceRemoval(experience_id="exp_C", day_index=2, candidate_id="openstreetmap_places:way/24341353")
        ],
    )

    class _BrokenExperiencePlanner:
        def run(self, planning_state: PlanningState) -> PlanningState:
            raise RuntimeError("simulated materialization crash")

    executor = TargetedRegenerationExecutor(experience_planner_service=_BrokenExperiencePlanner())
    # Deterministic-only plans never call the planner, so force the
    # reasoning path to exercise the planner call.
    plan.requires_ai_itinerary_reasoning = True
    fake_result = _completed_reasoning_result(
        [ItineraryReasoningDayPlan(day_index=2, candidate_ids=["openstreetmap_places:node/999"], rationale="r")]
    )
    executor._reasoning_provider_override = _FakeReasoningProvider(result=fake_result)
    executor.reasoning_request_builder = _FixedCandidatesRequestBuilder(
        [_candidate("openstreetmap_places:node/999", "New Spot", "openstreetmap_places", "node/999")]
    )

    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.FAILED
    assert result.resulting_planning_state is None
    assert planning_state.model_dump() == before.model_dump()


# ---------------------------------------------------------------------------
# Blocked / unsupported / needs_clarification plans never execute anything.
# ---------------------------------------------------------------------------


def test_blocked_plan_never_touches_planning_state() -> None:
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)
    plan = _plan(status=TargetedRegenerationPlanStatus.BLOCKED, block_reasons=["some reason"])

    result = TargetedRegenerationExecutor().execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.BLOCKED
    assert planning_state.model_dump() == before.model_dump()


def test_needs_clarification_plan_never_touches_planning_state() -> None:
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)
    plan = _plan(
        status=TargetedRegenerationPlanStatus.NEEDS_CLARIFICATION,
        clarification_reason="Two museums are scheduled.",
    )

    result = TargetedRegenerationExecutor().execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.NEEDS_CLARIFICATION
    assert result.clarification_reason == "Two museums are scheduled."
    assert planning_state.model_dump() == before.model_dump()


# ---------------------------------------------------------------------------
# Task 47: bounded repair respects preserved days.
# ---------------------------------------------------------------------------


class _FixedValidatorService:
    """Always returns the same two-issue report (day 1 and day 2), so the
    test can deterministically exercise day-scoped repair without needing
    real geographic-spread distance data."""

    def __init__(self, report: ValidationReport) -> None:
        self._report = report

    def run(self, working_state: PlanningState) -> PlanningState:
        working_state.validation_report = self._report.model_copy(deep=True)
        return working_state


class _FakeReasoningAndRepairProvider:
    provider_name = "fake_reasoning_and_repair_provider"

    def __init__(self, repair_result: AIItineraryRepairResult) -> None:
        self._repair_result = repair_result
        self.last_repair_request: Any = None

    def reason(self, request: AIItineraryReasoningRequest) -> AIItineraryReasoningResult:
        raise NotImplementedError

    def repair(self, request: Any) -> AIItineraryRepairResult:
        self.last_repair_request = request
        return self._repair_result


def test_bounded_repair_only_touches_affected_day_not_preserved_day() -> None:
    planning_state = _planning_state()
    candidates = [
        _candidate("openstreetmap_places:node/1", "Museum A", "openstreetmap_places", "node/1"),
        _candidate("openstreetmap_places:way/24341353", "Torre de Belem", "openstreetmap_places", "way/24341353"),
        _candidate("openstreetmap_places:node/700", "Alternative Spot", "openstreetmap_places", "node/700"),
    ]
    planning_state.ai_itinerary_reasoning_result = _completed_reasoning_result(
        [
            ItineraryReasoningDayPlan(day_index=1, candidate_ids=["openstreetmap_places:node/1"], rationale="r1"),
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=["openstreetmap_places:way/24341353"], rationale="r2"
            ),
        ]
    )
    planning_state.validation_report = ValidationReport(
        readiness_status="needs_review",
        warnings=[
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="geographic_spread",
                message="Day 1 is spread out.",
                affected_section="experience_plan.daily_plans[1]",
            ),
            ValidationIssue(
                severity=ValidationSeverity.WARNING,
                category="geographic_spread",
                message="Day 2 is spread out.",
                affected_section="experience_plan.daily_plans[2]",
            ),
        ],
    )

    fake_repair_result = AIItineraryRepairResult(
        status=AIItineraryRepairStatus.COMPLETED,
        repaired_days=[
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=["openstreetmap_places:node/700"], rationale="Tighter grouping."
            )
        ],
        repair_summary="Tightened day 2's geographic grouping.",
        guardrail_report=AIItineraryReasoningGuardrailReport(passed=True, checked_fields=["candidate_ids"]),
        confidence=0.85,
        attempt_number=1,
    )
    fake_provider = _FakeReasoningAndRepairProvider(fake_repair_result)

    plan = _plan(
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        experience_removals=[TargetedExperienceRemoval(experience_id="exp_D", day_index=2, candidate_id=None)],
    )

    executor = TargetedRegenerationExecutor(
        repair_request_builder=AIItineraryRepairRequestBuilder(
            reasoning_request_builder=_FixedCandidatesRequestBuilder(candidates)
        ),
        repair_provider=fake_provider,
        plan_validator_service=_FixedValidatorService(planning_state.validation_report),
    )
    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    assert result.repair_attempted is True
    assert result.repair_status == "completed"
    # the repair request builder was scoped to day 2 only
    assert fake_provider.last_repair_request is not None
    assert fake_provider.last_repair_request.affected_days == [2]
    # day 1's issue is never silently dropped -- the (fixed, re-applied)
    # validator still reports it after repair.
    final = result.resulting_planning_state
    assert final is not None
    day1_categories = [issue.category for issue in final.validation_report.warnings]
    assert "geographic_spread" in day1_categories
    day1_messages = [issue.message for issue in final.validation_report.warnings]
    assert "Day 1 is spread out." in day1_messages


# ---------------------------------------------------------------------------
# Section 197B.1: additive new-place preservation.
# ---------------------------------------------------------------------------


def _additive_plan(**overrides: Any) -> TargetedRegenerationPlan:
    fields: dict[str, Any] = {
        "trip_id": "trip_x",
        "source_version": "v1",
        "status": TargetedRegenerationPlanStatus.READY,
        "affected_day_indices": [],
        "preserved_day_indices": [1, 2, 3],
        "new_place_lookups": [TargetedNewPlaceLookup(query="Sintra")],
        "requires_ai_itinerary_reasoning": True,
        "required_stages": [PlanningStage.EXPERIENCE_PLAN, PlanningStage.VALIDATION],
    }
    fields.update(overrides)
    return TargetedRegenerationPlan(**fields)


def _insertion_day_result(day_index: int, candidate_id: str) -> AIItineraryReasoningResult:
    return _completed_reasoning_result(
        [ItineraryReasoningDayPlan(day_index=day_index, candidate_ids=[candidate_id], rationale="Insert here.")]
    )


# Task 12: pure additive insertion.


def test_task12_pure_additive_insertion_preserves_everything_and_adds_one_item() -> None:
    planning_state = _planning_state()
    before_ids_by_day = {
        day.day_number: [e.model_copy(deep=True) for e in day.experiences]
        for day in planning_state.experience_plan.daily_plans
    }

    fake_provider = _FakeReasoningProvider(result=_insertion_day_result(2, "openstreetmap_places:node/5000"))
    executor = TargetedRegenerationExecutor(
        gateway=_FakeGateway(_place_response(found=True)), reasoning_provider=fake_provider
    )
    result = executor.execute(planning_state, _additive_plan())

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None

    for day_number, original_items in before_ids_by_day.items():
        final_day = _day(day_number, final)
        original_subsequence = [e for e in final_day.experiences if e.experience_id in {o.experience_id for o in original_items}]
        assert [e.experience_id for e in original_subsequence] == [o.experience_id for o in original_items]
        for original, current in zip(original_items, original_subsequence):
            assert current.model_dump(exclude={"stop_order"}) == original.model_dump(exclude={"stop_order"})

    all_final_ids = {e.experience_id for day in final.experience_plan.daily_plans for e in day.experiences}
    all_original_ids = {e.experience_id for items in before_ids_by_day.values() for e in items}
    new_ids = all_final_ids - all_original_ids
    assert len(new_ids) == 1
    sintra = next(e for day in final.experience_plan.daily_plans for e in day.experiences if e.experience_id in new_ids)
    assert sintra.provider_place_id == "node/5000"
    day2 = _day(2, final)
    assert day2.experiences[-1].experience_id == sintra.experience_id
    # source untouched
    assert {e.experience_id for e in _day(2, planning_state).experiences} == {"exp_C", "exp_D"}


# Tasks 13/14/15 (combined): existing items are immune by construction --
# even a fake provider "claiming" a day should only contain the new
# candidate can never actually drop/move/reorder anything, since the
# executor never applies day-plan content wholesale for additive
# insertion -- it only ever reads a day_index and appends.


def test_task13_14_15_existing_items_are_immune_to_reasoning_claims() -> None:
    planning_state = _planning_state()
    # The fake "claims" day 2 now contains ONLY the new candidate --
    # if this were trusted as full day content, C and D would vanish.
    fake_provider = _FakeReasoningProvider(result=_insertion_day_result(2, "openstreetmap_places:node/5000"))
    executor = TargetedRegenerationExecutor(
        gateway=_FakeGateway(_place_response(found=True)), reasoning_provider=fake_provider
    )
    result = executor.execute(planning_state, _additive_plan())

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    day2_ids = {e.experience_id for e in _day(2, final).experiences}
    assert {"exp_C", "exp_D"}.issubset(day2_ids)


def test_task15_valid_insertion_position_within_target_day_is_accepted() -> None:
    planning_state = _planning_state()
    fake_provider = _FakeReasoningProvider(result=_insertion_day_result(1, "openstreetmap_places:node/5000"))
    executor = TargetedRegenerationExecutor(
        gateway=_FakeGateway(_place_response(found=True)), reasoning_provider=fake_provider
    )
    result = executor.execute(planning_state, _additive_plan())

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    day1_ids = [e.experience_id for e in _day(1, final).experiences]
    assert day1_ids[:2] == ["exp_A", "exp_B"]  # existing relative order preserved; new item appended after


# Task 16: unrelated candidate introduced.


def test_task16_unrelated_candidate_introduced_is_rejected() -> None:
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)
    fake_result = _completed_reasoning_result(
        [
            ItineraryReasoningDayPlan(
                day_index=1,
                candidate_ids=["openstreetmap_places:node/5000", "openstreetmap_places:node/9999"],
                rationale="Sneaking in an extra.",
            )
        ]
    )
    fake_provider = _FakeReasoningProvider(result=fake_result)
    executor = TargetedRegenerationExecutor(
        gateway=_FakeGateway(_place_response(found=True)), reasoning_provider=fake_provider
    )
    result = executor.execute(planning_state, _additive_plan())

    assert result.status == TargetedRegenerationExecutionStatus.REASONING_FAILED
    assert result.resulting_planning_state is None
    assert planning_state.model_dump() == before.model_dump()


# Task 17: requested candidate appears twice.


def test_task17_duplicate_candidate_reference_is_structurally_impossible() -> None:
    # `ItineraryReasoningDayPlan` itself already forbids a duplicate
    # candidate_id within one day (a pre-existing Section 193A model
    # validator) -- a fake/misbehaving provider cannot even construct a
    # response claiming the requested candidate twice, so there is
    # nothing for the executor's own validation to additionally reject.
    with pytest.raises(ValidationError, match="duplicate"):
        ItineraryReasoningDayPlan(
            day_index=1,
            candidate_ids=["openstreetmap_places:node/5000", "openstreetmap_places:node/5000"],
            rationale="Duplicated.",
        )


# Task 18: explicit regenerate-day + new-place uses normal targeted-day
# semantics for the regenerated day, hard-preserving every other day --
# NOT pure-additive same-day preservation rules.


def test_task18_regenerate_day_plus_new_place_uses_general_pipeline() -> None:
    planning_state = _planning_state()
    original_day1 = _day(1, planning_state).model_copy(deep=True)
    original_day3 = _day(3, planning_state).model_copy(deep=True)

    new_candidate = _candidate("openstreetmap_places:node/5000", "Sintra", "openstreetmap_places", "node/5000")
    fake_result = _completed_reasoning_result(
        [
            ItineraryReasoningDayPlan(
                day_index=2, candidate_ids=["openstreetmap_places:node/5000"], rationale="Regenerated with Sintra."
            )
        ]
    )
    fake_provider = _FakeReasoningProvider(result=fake_result)

    plan = _plan(
        affected_day_indices=[2],
        preserved_day_indices=[1, 3],
        day_instructions=[TargetedDayInstruction(day_index=2, instruction="Make it more relaxed and add Sintra.")],
        new_place_lookups=[TargetedNewPlaceLookup(query="Sintra")],
        requires_ai_itinerary_reasoning=True,
    )

    executor = TargetedRegenerationExecutor(
        gateway=_FakeGateway(_place_response(found=True)),
        reasoning_request_builder=_FixedCandidatesRequestBuilder([new_candidate]),
        reasoning_provider=fake_provider,
    )
    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    assert _day(1, final).model_dump() == original_day1.model_dump()
    assert _day(3, final).model_dump() == original_day3.model_dump()


# Task 19: additive routing/sequencing must never reorder pre-existing
# items, even if route-aware scheduling is enabled.


def test_task19_additive_insertion_never_reorders_existing_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "route_aware_scheduling_enabled", True, raising=False)
    planning_state = _planning_state()
    original_day1_order = [e.experience_id for e in _day(1, planning_state).experiences]

    fake_provider = _FakeReasoningProvider(result=_insertion_day_result(1, "openstreetmap_places:node/5000"))
    executor = TargetedRegenerationExecutor(
        gateway=_FakeGateway(_place_response(found=True)), reasoning_provider=fake_provider
    )
    result = executor.execute(planning_state, _additive_plan())

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    final_day1_original_subsequence = [
        e.experience_id for e in _day(1, final).experiences if e.experience_id in set(original_day1_order)
    ]
    assert final_day1_original_subsequence == original_day1_order


# Task 20: additive validation problem never triggers removal/repair of
# existing content.


def test_task20_additive_validation_issue_never_removes_existing_items() -> None:
    planning_state = _planning_state()
    fake_provider = _FakeReasoningProvider(result=_insertion_day_result(2, "openstreetmap_places:node/5000"))
    executor = TargetedRegenerationExecutor(
        gateway=_FakeGateway(_place_response(found=True)), reasoning_provider=fake_provider
    )
    result = executor.execute(planning_state, _additive_plan())

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    assert result.repair_attempted is False
    assert result.repair_status is None
    final = result.resulting_planning_state
    assert final is not None
    assert {"exp_C", "exp_D"}.issubset({e.experience_id for e in _day(2, final).experiences})
    assert result.narrator_status is not None


# Task 11: multiple additive places, one fails grounding.


def test_task11_one_failed_grounding_among_multiple_fails_whole_request() -> None:
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)
    plan = _additive_plan(
        new_place_lookups=[TargetedNewPlaceLookup(query="Sintra"), TargetedNewPlaceLookup(query="Nonexistent XYZ")]
    )

    class _TwoQueryGatewayPlaces:
        def search_must_visit_place(self, query: str, destination_name: str, filters: dict | None = None) -> Any:
            return _place_response(found=(query == "Sintra"))

    class _TwoQueryGateway:
        places = _TwoQueryGatewayPlaces()

    executor = TargetedRegenerationExecutor(gateway=_TwoQueryGateway())
    result = executor.execute(planning_state, plan)

    assert result.status == TargetedRegenerationExecutionStatus.PROVIDER_UNAVAILABLE
    assert result.resulting_planning_state is None
    assert planning_state.model_dump() == before.model_dump()


# Task 21: additive preservation audit read-only proof.


def test_task21_additive_execution_never_mutates_source() -> None:
    planning_state = _planning_state()
    before = planning_state.model_copy(deep=True)
    fake_provider = _FakeReasoningProvider(result=_insertion_day_result(3, "openstreetmap_places:node/5000"))
    executor = TargetedRegenerationExecutor(
        gateway=_FakeGateway(_place_response(found=True)), reasoning_provider=fake_provider
    )
    executor.execute(planning_state, _additive_plan())

    assert planning_state.model_dump() == before.model_dump()


# ---------------------------------------------------------------------------
# Section 202B.1 (Task 10): the same provider-grounded place is never
# scheduled twice -- an additive request for a place already on the
# itinerary inserts nothing (the commit boundary then reports no effect).
# ---------------------------------------------------------------------------


def test_202b1_additive_request_for_an_already_scheduled_place_inserts_nothing() -> None:
    planning_state = _planning_state()
    existing = _day(1, planning_state).experiences[0]
    existing.provider_source = "openstreetmap_places"
    existing.provider_place_id = "node/5000"  # the identity the requested place will resolve to
    before_dump = planning_state.experience_plan.model_dump()

    fake_provider = _FakeReasoningProvider(result=_insertion_day_result(2, "openstreetmap_places:node/5000"))
    executor = TargetedRegenerationExecutor(
        gateway=_FakeGateway(_place_response(found=True)), reasoning_provider=fake_provider
    )
    result = executor.execute(planning_state, _additive_plan())

    assert result.status == TargetedRegenerationExecutionStatus.COMPLETED
    final = result.resulting_planning_state
    assert final is not None
    assert final.experience_plan.model_dump() == before_dump  # nothing added, nothing changed
    assert any("already scheduled" in edit for edit in result.deterministic_edits_applied)
