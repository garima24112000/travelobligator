"""M2 Phase 1 (D4, D6).

D4: an insufficient-inventory finding caused by a place search that did not
complete never claims the destination has little to see. Only the wording
changes -- the code, readiness and schedule do not.

D6: what the planner writes before routing runs never says "no route
provider is connected" when one is configured; Developer view summarises
the real route report instead (frontend tests cover that half)."""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import get_settings
from app.models.common import ChecklistItemStatus, DataStatus, ProviderStatus, ProviderStatusEntry
from app.models.inventory_sufficiency import INSUFFICIENT_VERIFIED_INVENTORY, InventorySufficiencyStatus
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.missing_data_reasons import PLACES_PARTIAL_REQUEST_FAILURES
from app.providers.routing.factory import routing_provider_is_configured
from app.services import experience_planner_service as planner
from app.services.inventory_sufficiency_service import InventorySufficiencyService
from app.services.usefulness_contract import evaluate_usefulness, place_lookup_incomplete, usefulness_findings


def _state(places_entry: ProviderStatusEntry | None = None) -> PlanningState:
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville, Testland",
            start_date="2026-08-10",
            end_date="2026-08-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        )
    )
    if places_entry is not None:
        state.provider_status["geoapify_places:places"] = places_entry
    return state


def _entry(status: ProviderStatus, failure_reason: str | None = None) -> ProviderStatusEntry:
    return ProviderStatusEntry(
        provider_name="geoapify_places",
        provider_type="places",
        status=status,
        data_status=DataStatus.LIVE,
        failure_reason=failure_reason,
    )


# -- D4 ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "incomplete"),
    [
        (None, False),
        (_entry(ProviderStatus.SUCCESS), False),
        (_entry(ProviderStatus.PARTIAL), False),  # small, nothing failed
        (_entry(ProviderStatus.PARTIAL, PLACES_PARTIAL_REQUEST_FAILURES), True),
        (_entry(ProviderStatus.FAILED, "places_rate_limited"), True),
    ],
    ids=["no-entry", "success", "small-partial", "partial-with-failures", "failed"],
)
def test_place_lookup_incomplete(entry: ProviderStatusEntry | None, incomplete: bool) -> None:
    assert place_lookup_incomplete(_state(entry)) is incomplete


def test_other_fields_do_not_count_as_an_incomplete_place_search() -> None:
    state = _state()
    state.provider_status["geoapify_places:restaurants"] = _entry(ProviderStatus.FAILED)
    assert place_lookup_incomplete(state) is False


def _insufficient_finding(state: PlanningState) -> tuple[Any, list[str]]:
    critical, _warnings, blocking, _review = usefulness_findings(evaluate_usefulness(state))
    return critical[0], blocking


def test_a_genuinely_thin_destination_keeps_the_original_wording() -> None:
    issue, blocking = _insufficient_finding(_state(_entry(ProviderStatus.PARTIAL)))
    assert blocking == [INSUFFICIENT_VERIFIED_INVENTORY]
    assert "were found for this destination" in issue.message
    assert "nearby larger destination" in issue.suggested_fix


def test_an_incomplete_search_never_blames_the_destination() -> None:
    issue, blocking = _insufficient_finding(
        _state(_entry(ProviderStatus.PARTIAL, PLACES_PARTIAL_REQUEST_FAILURES))
    )
    assert blocking == [INSUFFICIENT_VERIFIED_INVENTORY]  # same code, same readiness
    assert issue.category == "insufficient_verified_inventory"
    assert "part of the place search did not complete" in issue.message
    assert "does not mean the destination has little to see" in issue.message
    assert "were found for this destination" not in issue.message
    assert issue.suggested_fix == "Try generating the plan again later."
    for word in ("provider", "Geoapify", "credit", "configured"):
        assert word not in issue.message


def test_inventory_report_message_follows_the_same_rule() -> None:
    service = InventorySufficiencyService(gateway=object())  # type: ignore[arg-type]

    thin = service.evaluate(_state(_entry(ProviderStatus.PARTIAL)))
    assert thin.status == InventorySufficiencyStatus.INSUFFICIENT
    assert thin.message.startswith("Not enough verified places were found")

    incomplete = service.evaluate(_state(_entry(ProviderStatus.FAILED, "places_timeout")))
    assert incomplete.status == InventorySufficiencyStatus.INSUFFICIENT
    assert "part of the place search did not complete" in incomplete.message


class _ExpansionPlaces:
    provider_name = "geoapify_places"
    supports_inventory_sizing = True

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome

    def search_attractions(self, destination: str, filters: Any = None) -> Any:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _ExpansionGateway:
    def __init__(self, places: _ExpansionPlaces) -> None:
        self.places = places


def _expansion_state() -> PlanningState:
    from app.models.planning_state import DestinationContext

    state = _state(_entry(ProviderStatus.SUCCESS))  # the broad search itself completed
    state.destination_context = DestinationContext(destination_name="Testville, Testland")
    return state


def _places_response(status: ProviderStatus, failure_reason: str | None = None) -> Any:
    from app.models.providers import ProviderResponse, ProviderType

    return ProviderResponse[Any](
        provider_name="geoapify_places",
        provider_type=ProviderType.PLACES,
        status=status,
        data_status=DataStatus.LIVE if status != ProviderStatus.FAILED else DataStatus.FAILED,
        data=[],
        failure_reason=failure_reason,
    )


@pytest.mark.parametrize(
    ("outcome", "incomplete"),
    [
        (RuntimeError("boom"), True),
        (_places_response(ProviderStatus.FAILED, "places_rate_limited"), True),
        (_places_response(ProviderStatus.PARTIAL, PLACES_PARTIAL_REQUEST_FAILURES), True),
        (_places_response(ProviderStatus.PARTIAL), False),  # small but complete
        (_places_response(ProviderStatus.SUCCESS), False),
    ],
    ids=["raised", "failed", "partial-with-failures", "small-partial", "success"],
)
def test_a_failed_expansion_page_counts_as_an_incomplete_search(outcome: Any, incomplete: bool) -> None:
    state = _expansion_state()
    service = InventorySufficiencyService(gateway=_ExpansionGateway(_ExpansionPlaces(outcome)))  # type: ignore[arg-type]

    state = service.run(state)

    assert place_lookup_incomplete(state) is incomplete
    assert ("geoapify_places:places_expansion" in state.provider_status) is incomplete
    report = state.inventory_sufficiency_report
    assert report.expansion_attempted is True
    assert report.status == InventorySufficiencyStatus.INSUFFICIENT
    assert ("part of the place search did not complete" in report.message) is incomplete


def test_status_entries_without_the_new_field_still_load() -> None:
    legacy = _entry(ProviderStatus.SUCCESS).model_dump(mode="json")
    legacy.pop("failure_reason")
    assert ProviderStatusEntry.model_validate(legacy).failure_reason is None


# -- D6 ---------------------------------------------------------------------------------------


def _routing(monkeypatch: pytest.MonkeyPatch, provider: str, **settings: Any) -> None:
    current = get_settings()
    monkeypatch.setattr(current, "routing_provider", provider, raising=False)
    for name, value in settings.items():
        monkeypatch.setattr(current, name, value, raising=False)


@pytest.mark.parametrize(
    ("provider", "settings", "configured"),
    [
        ("not_connected", {}, False),
        ("geoapify", {"geoapify_api_key": None}, False),
        ("geoapify", {"geoapify_api_key": "k"}, True),
        ("osrm", {"osrm_base_url": None}, False),
        ("osrm", {"osrm_base_url": "http://localhost:5000"}, True),
        ("something_else", {}, False),
    ],
)
def test_routing_provider_is_configured(
    monkeypatch: pytest.MonkeyPatch, provider: str, settings: dict[str, Any], configured: bool
) -> None:
    _routing(monkeypatch, provider, **settings)
    assert routing_provider_is_configured() is configured


def test_without_routing_the_honest_not_connected_context_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _routing(monkeypatch, "not_connected")
    context = planner._build_route_feasibility_context(routing_provider_is_configured())
    assert context.data_status == DataStatus.NOT_CONNECTED
    assert context.warnings == ["Route feasibility is not available because no route provider is connected."]


def test_with_routing_configured_the_draft_context_never_says_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    _routing(monkeypatch, "geoapify", geoapify_api_key="k")
    context = planner._build_route_feasibility_context(routing_provider_is_configured())
    text = " ".join([*context.assumptions, *context.warnings]).lower()
    assert "not connected" not in text and "no route provider" not in text
    assert context.data_status == DataStatus.UNAVAILABLE
    assert context.daily_route_feasibility == []
    # it claims nothing was verified
    assert "not verified" in text and "holds no route data" in text


def test_draft_gaps_and_checklist_follow_the_configured_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state()
    state.provider_coverage.routes = "not_connected"  # what the draft sees before routing runs

    _routing(monkeypatch, "not_connected")
    gaps = planner._build_implementation_gaps(state)
    assert any("routes/transit provider" in item for item in gaps.missing_data)
    checklist = {item.label: item for item in planner._build_readiness_checklist(state, [], [], []).items}
    assert checklist["Route times checked"].status == ChecklistItemStatus.MISSING_DATA

    _routing(monkeypatch, "geoapify", geoapify_api_key="k")
    gaps = planner._build_implementation_gaps(state)
    assert not any("routes/transit provider" in item for item in gaps.missing_data)
    checklist = {item.label: item for item in planner._build_readiness_checklist(state, [], [], []).items}
    for label in ("Route times checked", "Walking feasibility checked"):
        item = checklist[label]
        assert item.status != ChecklistItemStatus.CHECKED  # never claimed as checked here
        assert "not connected" not in item.explanation
        assert "after the day order is final" in item.explanation
