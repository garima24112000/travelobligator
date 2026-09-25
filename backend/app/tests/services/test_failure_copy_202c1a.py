from __future__ import annotations

from typing import Any

import pytest

from app.models.common import DataStatus, ProviderCoverage
from app.models.planning_state import DestinationContext, PlanningState, TripRequest
from app.models.targeted_regeneration_execution import (
    TargetedRegenerationExecutionResult,
    TargetedRegenerationExecutionStatus,
)
from app.models.targeted_regeneration_runtime import TargetedRegenerationRuntimeStatus
from app.services.plan_validator_service import PlanValidatorService
from test_itinerary_content_quality import plan as _plan_days, poi as _poi, state as _state  # type: ignore[import-not-found]
from test_targeted_regeneration_application_service import (  # type: ignore[import-not-found]
    _completed_interpretation,
    _FakeExecutor,
    _FakeInterpreter,
    _FakePlanBuilder,
    _FakeRepository,
    _pending_event,
    _planning_state,
    _ready_removal_plan,
    _service,
)

# Section 202C.1A (Task 11): user-facing copy follows the STRUCTURED cause the
# backend already knows -- never message text, never a raw provider error.


# -- AI reasoning rate limit (was generic REGENERATION_NOT_AVAILABLE) ---------------


def _reasoning_failed(kind: str | None):
    def build(planning_state: PlanningState) -> TargetedRegenerationExecutionResult:
        return TargetedRegenerationExecutionResult(
            trip_id="trip_x",
            status=TargetedRegenerationExecutionStatus.REASONING_FAILED,
            source_version=planning_state.metadata.current_version,
            reasoning_status="failed_or_rejected",
            block_reasons=["Scoped AI itinerary reasoning did not produce a usable, preservation-safe result."],
            provider_failure_kind=kind,
        )

    return build


def _run(kind: str | None):
    state = _planning_state()
    state.feedback_history = [_pending_event()]
    repository = _FakeRepository(state)
    service = _service(
        _FakeInterpreter(_completed_interpretation()),
        _FakePlanBuilder(_ready_removal_plan()),
        _FakeExecutor(builder=_reasoning_failed(kind)),
        repository,
    )
    return service.regenerate("trip_x"), repository.get_by_trip_id("trip_x")


def test_reasoning_stage_rate_limit_is_reported_as_a_rate_limit_and_changes_nothing() -> None:
    result, persisted = _run("rate_limited")

    assert result.status == TargetedRegenerationRuntimeStatus.RATE_LIMITED
    assert result.provider_failure_kind == "rate_limited"
    assert "rate-limited" in result.message and "execution did not complete" not in result.message
    assert persisted.regeneration_attempts[-1].reason_code == "REGENERATION_PROVIDER_RATE_LIMITED"
    assert persisted.regeneration_attempts[-1].status == "blocked"
    assert persisted.feedback_history[0].applied_at is None  # still pending
    assert persisted.metadata.current_version == "v1" and len(persisted.version_history) == 1


@pytest.mark.parametrize("kind", ["authentication", "timeout_or_network", "provider_error", "malformed_output"])
def test_reasoning_stage_provider_failures_are_ai_unavailable_not_generic(kind: str) -> None:
    result, persisted = _run(kind)

    assert result.status == TargetedRegenerationRuntimeStatus.PROVIDER_UNAVAILABLE
    assert persisted.regeneration_attempts[-1].reason_code == "REGENERATION_AI_UNAVAILABLE"
    assert persisted.feedback_history[0].applied_at is None


def test_reasoning_failure_without_a_structured_provider_cause_stays_generic() -> None:
    # e.g. the model answered but the result failed guardrails / preservation
    # checks: the backend cannot truthfully call that a provider outage.
    result, persisted = _run(None)

    assert result.status == TargetedRegenerationRuntimeStatus.FAILED
    assert persisted.regeneration_attempts[-1].reason_code == "REGENERATION_NOT_AVAILABLE"
    assert result.provider_failure_kind is None


# -- empty plan: provider outage vs no supply (validator copy) ------------------------------


def _empty_state(places: str | None, candidates: int = 0) -> PlanningState:
    st = PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville", start_date="2026-08-10", end_date="2026-08-11", travelers_count=2,
            travel_group_type="couple",
        )
    )
    st.destination_context = DestinationContext(
        destination_name="Testville",
        candidate_pois=[{"place_id": f"p{i}", "name": f"P{i}", "category": "museum"} for i in range(candidates)],
    )
    st.provider_coverage = ProviderCoverage(places=places)
    return st


def _critical(st: PlanningState):
    PlanValidatorService().run(st)
    return [i for i in st.validation_report.critical_issues if i.category == "provider_coverage"]


def test_places_provider_outage_is_not_described_as_nothing_to_see() -> None:
    (issue,) = _critical(_empty_state(DataStatus.FAILED.value))
    assert "could not be reached" in issue.message and "nothing to see" in issue.message
    assert "try" in (issue.suggested_fix or "").lower()
    (also,) = _critical(_empty_state(DataStatus.UNAVAILABLE.value))
    assert "could not be reached" in also.message


def test_provider_that_answered_with_no_candidates_is_a_supply_limit_not_an_outage() -> None:
    (issue,) = _critical(_empty_state(DataStatus.LIVE.value, candidates=0))
    assert "responded but returned no attraction candidates" in issue.message
    assert "could not be reached" not in issue.message


def test_candidates_found_but_none_schedulable_says_so() -> None:
    # Only isolated single trees (never eligible filler): candidates exist, none can be scheduled.
    trees = [
        _poi(f"t{i}", f"Tree {i}", {"natural": "tree", "tourism": "attraction", "heritage": "2"}, i * 1e-4)
        for i in range(4)
    ]
    st = _state(trees, days=2)
    st.provider_coverage = ProviderCoverage(places=DataStatus.LIVE.value)
    _plan_days(st)
    PlanValidatorService().run(st)
    (issue,) = [i for i in st.validation_report.critical_issues if i.category == "scheduling"]
    assert "4 provider-backed candidate(s) were found" in issue.message
    assert "planner stage" not in issue.message and "planner stage" not in (issue.suggested_fix or "")


def test_no_places_provider_connected_keeps_its_own_copy() -> None:
    (issue,) = _critical(_empty_state("not_connected"))
    assert "no places provider is connected" in issue.message


def test_missing_coverage_information_falls_back_to_the_no_candidates_copy_without_inferring_an_outage() -> None:
    (issue,) = _critical(_empty_state(None))
    assert "could not be reached" not in issue.message
