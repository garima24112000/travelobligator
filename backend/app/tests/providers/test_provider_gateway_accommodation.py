from __future__ import annotations

from datetime import date

import pytest

from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.accommodation.not_connected_adapter import (
    NotConnectedAccommodationProvider,
)
from app.providers.gateway import ProviderGateway

# Tests for Step 167C: `ProviderGateway.accommodation_inventory` +
# `ProviderGateway.search_accommodations`. Every test here uses either the
# default not_connected accommodation provider or an injected fake -- none
# makes a real network call, mirroring
# backend/app/tests/providers/test_provider_gateway_routing.py.


def _request(**overrides: object) -> AccommodationSearchRequest:
    fields: dict[str, object] = {
        "destination": "Lisbon, Portugal",
        "check_in_date": date(2026, 10, 10),
        "check_out_date": date(2026, 10, 14),
        "adults": 2,
        "rooms": 1,
    }
    fields.update(overrides)
    return AccommodationSearchRequest(**fields)


class _FakeAccommodationInventoryProvider(AccommodationInventoryProvider):
    """Deterministic test double -- never makes a network call, and its
    result is trivially distinguishable from a real not_connected result
    so tests can prove the gateway actually delegates to whatever provider
    was injected."""

    provider_name = "fake_accommodation_inventory_provider"

    def __init__(self, result: AccommodationSearchResult | None = None) -> None:
        self._result = result
        self.calls: list[AccommodationSearchRequest] = []

    def search_accommodations(
        self, request: AccommodationSearchRequest
    ) -> AccommodationSearchResult:
        self.calls.append(request)
        if self._result is not None:
            return self._result
        offer = AccommodationOffer(
            provider=self.provider_name,
            provider_property_id="prop_1",
            property_name="Fake Property",
            data_status=DataStatus.LIVE,
        )
        return AccommodationSearchResult(
            provider=self.provider_name,
            status=AccommodationSearchStatus.SUCCESS,
            offers=[offer],
            message="Fake provider for test purposes only.",
        )


# ---------------------------------------------------------------------------
# 1/2/3. Provider gateway returns not_connected accommodation result by
# default, with empty offers and no fabricated fields.
# ---------------------------------------------------------------------------


def test_gateway_default_search_accommodations_returns_not_connected() -> None:
    gateway = ProviderGateway()

    result = gateway.search_accommodations(_request())

    assert isinstance(result, AccommodationSearchResult)
    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert isinstance(gateway.accommodation_inventory, NotConnectedAccommodationProvider)


def test_gateway_default_search_accommodations_returns_empty_offers() -> None:
    gateway = ProviderGateway()
    result = gateway.search_accommodations(_request())
    assert result.offers == []


def test_gateway_default_search_accommodations_never_fabricates_fields() -> None:
    gateway = ProviderGateway()
    result = gateway.search_accommodations(_request())

    assert result.offers == []
    for forbidden_attr in ("property_name", "nightly_price_amount", "total_price_amount", "rating", "booking_url"):
        # No offer exists to carry a fabricated value in the first place --
        # the empty offers list itself is the guarantee.
        assert not hasattr(result, forbidden_attr)


def test_gateway_default_accommodation_provider_name() -> None:
    gateway = ProviderGateway()
    result = gateway.search_accommodations(_request())
    assert result.provider == "accommodation_inventory_provider"


# ---------------------------------------------------------------------------
# 4. Provider gateway can use an injected fake accommodation provider and
#    returns its provider-backed result.
# ---------------------------------------------------------------------------


def test_gateway_uses_injected_accommodation_provider() -> None:
    fake_provider = _FakeAccommodationInventoryProvider()
    gateway = ProviderGateway(accommodation_inventory=fake_provider)

    request = _request()
    result = gateway.search_accommodations(request)

    assert result.provider == "fake_accommodation_inventory_provider"
    assert result.status == AccommodationSearchStatus.SUCCESS
    assert result.offers[0].property_name == "Fake Property"
    assert fake_provider.calls == [request]


def test_gateway_passes_accommodation_request_through_unchanged() -> None:
    fake_provider = _FakeAccommodationInventoryProvider()
    gateway = ProviderGateway(accommodation_inventory=fake_provider)

    request = _request(children=2, currency="EUR")
    gateway.search_accommodations(request)

    assert fake_provider.calls[0].children == 2
    assert fake_provider.calls[0].currency == "EUR"


def test_gateway_other_provider_slots_are_unaffected_by_injected_accommodation() -> None:
    """Injecting a fake `accommodation_inventory` provider must not disturb
    any other gateway slot's default construction."""
    fake_provider = _FakeAccommodationInventoryProvider()
    gateway = ProviderGateway(accommodation_inventory=fake_provider)

    assert gateway.places is not None
    assert gateway.weather is not None
    assert gateway.holiday is not None
    assert gateway.currency is not None
    assert gateway.routing is not None
    assert gateway.accommodation is not None
    assert gateway.accommodation_inventory is fake_provider


# ---------------------------------------------------------------------------
# 5. Provider gateway accommodation lookup does not call network by
#    default.
# ---------------------------------------------------------------------------


def test_gateway_search_accommodations_makes_no_network_call_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "ProviderGateway.search_accommodations must not open a real httpx.Client here"
        )

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    gateway = ProviderGateway()
    result = gateway.search_accommodations(_request())

    assert result.status == AccommodationSearchStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# 6. Provider gateway construction remains backward compatible.
# ---------------------------------------------------------------------------


def test_gateway_construction_with_no_arguments_still_works() -> None:
    gateway = ProviderGateway()

    assert gateway.places is not None
    assert gateway.routes is not None
    assert gateway.transit is not None
    assert gateway.accommodation is not None
    assert gateway.flight is not None
    assert gateway.weather is not None
    assert gateway.holiday is not None
    assert gateway.currency is not None
    assert gateway.ai_reasoning is not None
    assert gateway.routing is not None
    assert gateway.accommodation_inventory is not None


def test_gateway_does_not_inspect_destination_to_invent_data() -> None:
    gateway = ProviderGateway()

    result_lisbon = gateway.search_accommodations(_request(destination="Lisbon, Portugal"))
    result_tokyo = gateway.search_accommodations(_request(destination="Tokyo, Japan"))

    assert result_lisbon.offers == result_tokyo.offers == []
    assert result_lisbon.status == result_tokyo.status == AccommodationSearchStatus.NOT_CONNECTED


def test_gateway_does_not_transform_not_connected_into_fake_offers() -> None:
    gateway = ProviderGateway()
    request = _request()

    first = gateway.search_accommodations(request)
    second = gateway.search_accommodations(request)

    assert first.model_dump(exclude={"generated_at"}) == second.model_dump(exclude={"generated_at"})
    assert first.offers == [] and second.offers == []


# ---------------------------------------------------------------------------
# 11/12/13. As of Step 167D, `PlanningOrchestrator` and
# `PlanValidatorService` intentionally consume the accommodation inventory
# report (see docs/12_provider_architecture.md section 44,
# docs/13_llm_reasoning_pipeline.md section 67,
# docs/14_backend_architecture.md section 44); this test file itself is
# from Step 167C, one step before that wiring existed. `ProviderCoverageService`
# stays untouched by this step -- `ProviderGateway.default_provider_coverage()`
# and the `hotel_prices` coverage mapping for the accommodation inventory
# report live in `PlanningOrchestrator` directly (mirroring how
# `provider_coverage.routes` is set directly in `PlanningOrchestrator`, not
# `ProviderCoverageService`), never inside `ProviderCoverageService` itself.
# ---------------------------------------------------------------------------


def test_planning_orchestrator_calls_accommodation_inventory_service() -> None:
    """Step 167D: `PlanningOrchestrator` now builds and stores
    `accommodation_inventory_report` via `AccommodationInventoryService` --
    see test_accommodation_inventory_service.py and
    test_planning_orchestrator_accommodation_inventory.py for behavior
    tests."""
    import inspect

    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "AccommodationInventoryService" in source
    assert "accommodation_inventory_report" in source
    assert "provider_coverage.hotel_prices" in source


def test_plan_validator_references_accommodation_inventory_report() -> None:
    """Step 167D: `PlanValidatorService` now surfaces
    `accommodation_inventory_report` as a non-blocking validation warning
    -- see test_plan_validator_service.py's accommodation-inventory tests."""
    import inspect

    import app.services.plan_validator_service as plan_validator_module

    source = inspect.getsource(plan_validator_module)
    assert "accommodation_inventory_report" in source
    assert "_build_accommodation_inventory_warning" in source
    # The provider *class* is still never referenced here -- only the
    # already-computed report/result models are read.
    assert "AccommodationInventoryProvider" not in source


def test_provider_coverage_service_does_not_reference_accommodation_inventory_contract() -> None:
    import inspect

    import app.services.provider_coverage_service as provider_coverage_module

    source = inspect.getsource(provider_coverage_module)
    assert "search_accommodations" not in source
    assert "accommodation_inventory" not in source.lower()
    assert "AccommodationInventoryProvider" not in source


def test_gateway_default_provider_coverage_is_unchanged() -> None:
    """`ProviderGateway.default_provider_coverage()` still reports
    `accommodations: not_connected` exactly as before this step -- adding
    `search_accommodations` did not touch coverage reporting."""
    gateway = ProviderGateway()
    coverage = gateway.default_provider_coverage()
    assert coverage.accommodations == "not_connected"
