"""M2 Phase 2 (Workstream D, and B's source states).

  * the plan's route context restates the FINAL route report (it read
    `unavailable` next to a fully routed day in the development E2E run);
  * readiness reports the owner's GENERATION_ENABLED switch as a temporary
    pause instead of offering an action the route refuses with 503;
  * accommodation results name WHY they hold the offers they hold.
Fakes and local files only."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings, get_settings
from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchResult, AccommodationSearchStatus
from app.models.common import DataStatus, ProviderStatus
from app.models.planning_state import (
    DailyPlan,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripRequest,
)
from app.models.routing import RouteFeasibilityReport, RouteFeasibilityStatus, RouteLegFeasibility
from app.providers.accommodation import scraped_adapter as accommodation_scraped
from app.providers.accommodation.scraped_adapter import ScrapedAccommodationProvider
from app.services.accommodation_inventory_service import AccommodationInventoryService, ensure_accommodation_reason
from app.services.regeneration_readiness_service import (
    CHANGE_APPLICATION_NOT_SUPPORTED,
    CHANGE_GENERATION_PAUSED,
    change_application_unavailable_reason,
)
from app.services.route_context_finalizer import finalize_route_feasibility_context, route_context_from_report


# -- route context from the final report -----------------------------------------------------


def _experience(experience_id: str) -> ExperienceItem:
    return ExperienceItem(experience_id=experience_id, name=f"Stop {experience_id}", category="sight")


def _leg(from_id: str, to_id: str, status: ProviderStatus, seconds: float | None = 600.0) -> RouteLegFeasibility:
    return RouteLegFeasibility(
        from_experience_id=from_id, from_experience_name=f"Stop {from_id}",
        to_experience_id=to_id, to_experience_name=f"Stop {to_id}",
        provider="geoapify", status=status,
        distance_meters=800.0 if status == ProviderStatus.SUCCESS else None,
        duration_seconds=seconds if status == ProviderStatus.SUCCESS else None,
        feasibility_status=RouteFeasibilityStatus.FEASIBLE if status == ProviderStatus.SUCCESS
        else RouteFeasibilityStatus.NEEDS_REVIEW,
        mode="walk" if status == ProviderStatus.SUCCESS else None,
    )


def _state(legs: list[RouteLegFeasibility] | None, report_status: ProviderStatus = ProviderStatus.SUCCESS) -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville, Testland", start_date="2027-08-10", end_date="2027-08-11",
            travelers_count=2, travel_group_type=TravelGroupType.COUPLE,
        )
    )
    state.experience_plan = ExperiencePlan(
        daily_plans=[
            DailyPlan(day_number=1, date="2027-08-10", experiences=[_experience("a"), _experience("b"), _experience("c")]),
            DailyPlan(day_number=2, date="2027-08-11", experiences=[_experience("d"), _experience("e")]),
        ]
    )
    if legs is not None:
        state.route_feasibility_report = RouteFeasibilityReport(
            status=report_status, legs=legs, provider="geoapify", route_data_source="geoapify"
        )
    return state


def test_a_fully_routed_plan_is_live_with_one_segment_per_transfer() -> None:
    state = _state([
        _leg("a", "b", ProviderStatus.SUCCESS), _leg("b", "c", ProviderStatus.SUCCESS),
        _leg("d", "e", ProviderStatus.SUCCESS, 900.0),
    ])
    context = route_context_from_report(state)
    assert context.data_status == DataStatus.LIVE and context.warnings == [] and context.confidence == 1.0
    assert [day.data_status for day in context.daily_route_feasibility] == [DataStatus.LIVE, DataStatus.LIVE]
    segment = context.daily_route_feasibility[1].segments[0]
    assert (segment.travel_mode, segment.distance_meters, segment.duration_minutes, segment.source) == (
        "walk", 800.0, 15.0, "geoapify"
    )


def test_an_unverified_transfer_is_never_a_segment_and_never_called_verified() -> None:
    state = _state([
        _leg("a", "b", ProviderStatus.SUCCESS), _leg("b", "c", ProviderStatus.FAILED),
        _leg("d", "e", ProviderStatus.UNAVAILABLE),
    ])
    context = route_context_from_report(state)
    assert context.data_status == DataStatus.UNAVAILABLE
    assert context.warnings == ["2 of 3 transfers between stops have no verified route; they are not verified."]
    day_one, day_two = context.daily_route_feasibility
    assert len(day_one.segments) == 1 and day_one.data_status == DataStatus.UNAVAILABLE
    assert day_one.warnings == ["1 of 2 transfers between stops has no verified route; it is not verified."]
    assert day_two.segments == [] and day_two.data_status == DataStatus.UNAVAILABLE
    assert context.confidence == round(1 / 3, 3)


def test_no_report_or_no_routing_keeps_the_draft_context() -> None:
    for state in (_state(None), _state([], report_status=ProviderStatus.NOT_CONNECTED)):
        draft = state.experience_plan.route_feasibility_context
        assert route_context_from_report(state) is None
        assert finalize_route_feasibility_context(state).experience_plan.route_feasibility_context == draft


def test_finalize_replaces_the_draft_context() -> None:
    state = _state([_leg("a", "b", ProviderStatus.SUCCESS)])
    finalize_route_feasibility_context(state)
    assert state.experience_plan.route_feasibility_context.data_status == DataStatus.LIVE


def test_legs_crossing_days_or_unknown_stops_are_ignored() -> None:
    state = _state([_leg("c", "d", ProviderStatus.SUCCESS), _leg("x", "y", ProviderStatus.SUCCESS)])
    context = route_context_from_report(state)
    assert context.daily_route_feasibility == [] and context.data_status == DataStatus.UNAVAILABLE


def test_both_engines_and_both_regeneration_paths_finalize() -> None:
    root = Path(__file__).resolve().parents[2] / "services"
    for name in ("planning_orchestrator.py", "regeneration_mutation_service.py", "targeted_regeneration_application_service.py"):
        assert "finalize_route_feasibility_context(" in (root / name).read_text(encoding="utf-8"), name
    assert (root / "planning_orchestrator.py").read_text(encoding="utf-8").count("finalize_route_feasibility_context(") == 2


# -- the GENERATION_ENABLED switch -----------------------------------------------------------


def test_a_paused_generation_switch_is_reported(monkeypatch: pytest.MonkeyPatch, synthetic_legacy_regeneration_support: None) -> None:
    assert change_application_unavailable_reason([]) is None
    monkeypatch.setattr(get_settings(), "generation_enabled", False, raising=False)
    assert change_application_unavailable_reason([]) == CHANGE_GENERATION_PAUSED


def test_a_missing_capability_is_reported_before_the_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "generation_enabled", False, raising=False)
    assert change_application_unavailable_reason([]) == CHANGE_APPLICATION_NOT_SUPPORTED


def test_paused_readiness_says_try_later(monkeypatch: pytest.MonkeyPatch, synthetic_legacy_regeneration_support: None) -> None:
    from app.models.common import RegenerationStrategy
    from app.models.planning_state import FeedbackEvent, PlanningStage
    from app.services.regeneration_readiness_service import regeneration_readiness_service
    from app.services.versioning_service import VersioningService

    state = _state(None)
    state = VersioningService().create_initial_version(state)
    state.feedback_history.append(
        FeedbackEvent(feedback_text="Make this less packed", feedback_type="pace_change",
                      affected_stages=[PlanningStage.EXPERIENCE_PLAN],
                      regeneration_strategy=RegenerationStrategy.EXPLANATION_ONLY)
    )
    monkeypatch.setattr(get_settings(), "generation_enabled", False, raising=False)
    readiness = regeneration_readiness_service.recompute(state).regeneration_readiness
    assert readiness.can_regenerate is False
    assert readiness.change_requests_unavailable_reason == CHANGE_GENERATION_PAUSED
    assert "paused" in readiness.blocked_by[0] and "later" in readiness.next_step


# -- accommodation source states --------------------------------------------------------------


def _accommodation(monkeypatch: pytest.MonkeyPatch, html_path: str | None) -> AccommodationSearchResult:
    settings = Settings(
        _env_file=None, scraping_enabled=True, scraped_accommodation_provider_enabled=True,
        scraped_accommodation_html_path=html_path, scraped_accommodation_html_path_booking=None,
        scraped_accommodation_html_path_expedia=None, scraped_accommodation_html_path_hotelbeds=None,
        scraped_accommodation_html_path_hostelworld=None, scraped_accommodation_html_path_vrbo=None,
        scraped_accommodation_html_path_airbnb=None, provider_cache_enabled=False,
    )
    monkeypatch.setattr(accommodation_scraped, "get_settings", lambda: settings)
    start = date.today() + timedelta(days=30)
    return ScrapedAccommodationProvider().search_accommodations(
        AccommodationSearchRequest(
            destination="Testville", check_in_date=start, check_out_date=start + timedelta(days=2), adults=2, rooms=1
        )
    )


def test_no_local_file_is_no_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _accommodation(monkeypatch, str(tmp_path / "missing.html"))
    assert result.status == AccommodationSearchStatus.UNAVAILABLE
    assert result.availability_reason == "no_source_configured"


def test_a_local_file_without_offers_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    html = tmp_path / "stays.html"
    html.write_text("<html><body>nothing to book here</body></html>", encoding="utf-8")
    result = _accommodation(monkeypatch, str(html))
    assert result.availability_reason == "local_source_no_matching_offers"
    assert "held no offer" in result.message and "were found" not in result.message


@pytest.mark.parametrize(
    ("status", "offers", "expected"),
    [(AccommodationSearchStatus.NOT_CONNECTED, [], "no_source_configured"),
     (AccommodationSearchStatus.FAILED, [], "provider_failed"),
     (AccommodationSearchStatus.UNAVAILABLE, [], None)],
)
def test_service_fallback(status: AccommodationSearchStatus, offers: list[Any], expected: str | None) -> None:
    result = AccommodationSearchResult(provider="p", status=status, offers=offers)
    assert ensure_accommodation_reason(result).availability_reason == expected


def test_a_stay_without_a_night_is_not_searchable() -> None:
    state = _state(None)
    state.trip_request.end_date = state.trip_request.start_date
    class _Gateway:
        class accommodation_inventory:  # noqa: N801
            provider_name = "fake"

    result = AccommodationInventoryService(gateway=_Gateway()).build_report(state)  # type: ignore[arg-type]
    assert result.availability_reason == "search_not_possible"
