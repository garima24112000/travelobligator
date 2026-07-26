from __future__ import annotations

from app.models.planning_state import (
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripRequest,
    ValidationReport,
)
from app.services.versioning_service import VersioningService


def _planning_state() -> PlanningState:
    return PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville, Testland",
            start_date="2026-08-10",
            end_date="2026-08-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        )
    )


def test_new_planning_state_has_empty_version_history() -> None:
    assert _planning_state().version_history == []


def test_create_initial_version_records_v1_with_expected_fields() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state.validation_report = ValidationReport()

    versioning_service = VersioningService()
    result = versioning_service.create_initial_version(planning_state)

    assert len(result.version_history) == 1
    version_item = result.version_history[0]

    assert version_item.version_label == "v1"
    assert version_item.created_by == "system_generation"
    assert version_item.feedback_event_id is None
    assert version_item.preserved_sections == []
    assert result.metadata.current_version == "v1"

    # traveler_profile/destination_context/trip_strategy/stay_transport are
    # still None on this state, so they must not be claimed as changed.
    assert "traveler_profile" not in version_item.changed_sections
    assert "destination_context" not in version_item.changed_sections
    assert "trip_strategy" not in version_item.changed_sections
    assert "stay_transport" not in version_item.changed_sections
    assert "experience_plan" in version_item.changed_sections
    assert "validation_report" in version_item.changed_sections
    # provider_coverage always exists (default_factory), so it is always recorded.
    assert "provider_coverage" in version_item.changed_sections


def test_create_initial_version_is_idempotent() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()

    versioning_service = VersioningService()
    first_result = versioning_service.create_initial_version(planning_state)
    first_version_id = first_result.version_history[0].version_id

    second_result = versioning_service.create_initial_version(first_result)

    # Calling it again (e.g. POST /generate re-run) must not append a
    # duplicate v1 entry -- the existing item is kept as-is.
    assert len(second_result.version_history) == 1
    assert second_result.version_history[0].version_id == first_version_id


def test_create_initial_version_does_not_mutate_other_sections() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()
    planning_state.validation_report = ValidationReport()

    before_experience_plan = planning_state.experience_plan.model_copy(deep=True)
    before_validation_report = planning_state.validation_report.model_copy(deep=True)
    before_provider_coverage = planning_state.provider_coverage.model_copy(deep=True)
    before_route_feasibility = (
        planning_state.experience_plan.route_feasibility_context.model_copy(deep=True)
    )
    before_feedback_history = list(planning_state.feedback_history)
    before_pending_feedback_summary = planning_state.pending_feedback_summary.model_copy(
        deep=True
    )
    before_user_locks = list(planning_state.user_locks)

    versioning_service = VersioningService()
    result = versioning_service.create_initial_version(planning_state)

    # Recording the initial version is purely additive bookkeeping: none of
    # the actual planning output is touched by it.
    assert result.experience_plan == before_experience_plan
    assert result.validation_report == before_validation_report
    assert result.provider_coverage == before_provider_coverage
    assert result.experience_plan.route_feasibility_context == before_route_feasibility
    assert result.feedback_history == before_feedback_history
    assert result.pending_feedback_summary == before_pending_feedback_summary
    assert result.user_locks == before_user_locks


def test_create_initial_version_does_not_invent_travel_facts() -> None:
    planning_state = _planning_state()
    planning_state.experience_plan = ExperiencePlan()

    versioning_service = VersioningService()
    result = versioning_service.create_initial_version(planning_state)
    version_item = result.version_history[0]

    # Only pipeline section names ever appear here -- never a destination,
    # place, price, or route string.
    known_sections = {
        "traveler_profile",
        "destination_context",
        "trip_strategy",
        "stay_transport",
        "experience_plan",
        "validation_report",
        "provider_coverage",
    }
    assert set(version_item.changed_sections) <= known_sections
    assert version_item.preserved_sections == []
