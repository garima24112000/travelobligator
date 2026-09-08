from __future__ import annotations

import ast
import inspect

import pytest

from app.models.hotel_ratings import HotelRatingsRequest, HotelRatingsResult, HotelRatingsStatus
from app.providers.hotel_ratings import HotelRatingsProvider, NotConnectedHotelRatingsProvider

# Safety/skeleton tests for Step 177B's hotel ratings provider contract.
# Never calls a real network service and never requires any rating
# provider configuration.


def _request() -> HotelRatingsRequest:
    return HotelRatingsRequest(offer_id="offer_1", provider_property_id="prop_123")


# ---------------------------------------------------------------------------
# 9. Provider interface cannot be instantiated directly, but can be
#    subclassed by a fake test provider.
# ---------------------------------------------------------------------------


def test_hotel_ratings_provider_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        HotelRatingsProvider()  # type: ignore[abstract]


class _FakeHotelRatingsProvider(HotelRatingsProvider):
    """Deterministic test double proving the interface can be subclassed
    without touching any real rating provider."""

    provider_name = "fake_hotel_ratings_provider"

    def __init__(self, result: HotelRatingsResult | None = None) -> None:
        self._result = result
        self.calls: list[list[HotelRatingsRequest]] = []

    def get_ratings(self, requests: list[HotelRatingsRequest]) -> HotelRatingsResult:
        self.calls.append(requests)
        if self._result is not None:
            return self._result
        return HotelRatingsResult(
            provider=self.provider_name,
            status=HotelRatingsStatus.NOT_CONNECTED,
            items=[],
            message="Fake provider for test purposes only.",
        )


def test_fake_provider_can_subclass_interface() -> None:
    provider = _FakeHotelRatingsProvider()
    result = provider.get_ratings([_request()])
    assert isinstance(result, HotelRatingsResult)
    assert result.status == HotelRatingsStatus.NOT_CONNECTED
    assert provider.calls == [[_request()]]


def test_incomplete_subclass_missing_method_cannot_be_instantiated() -> None:
    class _IncompleteProvider(HotelRatingsProvider):
        pass

    with pytest.raises(TypeError):
        _IncompleteProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# 7/8. NotConnectedHotelRatingsProvider returns not_connected with empty
#      results and never populates a rating value.
# ---------------------------------------------------------------------------


def test_not_connected_provider_returns_not_connected_with_empty_items() -> None:
    provider = NotConnectedHotelRatingsProvider()
    result = provider.get_ratings([_request()])

    assert result.status == HotelRatingsStatus.NOT_CONNECTED
    assert result.items == []
    assert result.provider == "hotel_ratings_provider"
    assert "no hotel ratings provider is configured" in (result.message or "").lower()



def test_not_connected_provider_never_populates_rating_values() -> None:
    provider = NotConnectedHotelRatingsProvider()
    result = provider.get_ratings([_request(), _request()])

    assert result.items == []
    # Defensive: even given multiple requests, no item -- and therefore no
    # rating value/review_count -- is ever produced.
    assert all(item.rating is None for item in result.items)


def test_not_connected_provider_ignores_request_contents() -> None:
    """The not_connected provider never inspects request identity fields
    beyond accepting them -- it returns the exact same result regardless
    of what is asked for."""
    provider = NotConnectedHotelRatingsProvider()
    result_a = provider.get_ratings([_request()])
    result_b = provider.get_ratings(
        [HotelRatingsRequest(property_name="Some Other Hotel", address="123 Fake St")]
    )
    assert result_a.status == result_b.status == HotelRatingsStatus.NOT_CONNECTED
    assert result_a.items == result_b.items == []


# ---------------------------------------------------------------------------
# Module import safety: no LangGraph, Groq, Anthropic, Kiwi/MCP, httpx, or
# other live/network/vendor import in the contract module.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "app.providers.hotel_ratings.base",
        "app.providers.hotel_ratings.not_connected",
        "app.models.hotel_ratings",
    ],
)
def test_hotel_ratings_skeleton_modules_have_no_disallowed_imports(module_name: str) -> None:
    module = __import__(module_name, fromlist=["_"])
    source = inspect.getsource(module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "httpx",
        "requests",
        "groq",
        "anthropic",
        "openai",
        "gemini",
        "google.generativeai",
        "kiwi",
        "mcp",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"{module_name}: disallowed import found: {name}"


# ---------------------------------------------------------------------------
# Not wired into ProviderGateway. As of Step 177C, AccommodationInventoryService
# *does* wire in hotel-rating enrichment -- see
# test_accommodation_inventory_service_wires_hotel_rating_enrichment below.
# As of Step 177D, PlanningOrchestrator (and the LangGraph accommodation
# node) *does* wire in the derived ProviderCoverage.hotel_ratings value --
# see test_planning_orchestrator_wires_hotel_ratings_coverage below, which
# replaces the old Step 177B "does not reference" test for that module.
# ---------------------------------------------------------------------------


def test_provider_gateway_does_not_reference_hotel_ratings() -> None:
    import app.providers.gateway as gateway_module

    source = inspect.getsource(gateway_module)
    assert "hotel_ratings" not in source.lower()


def test_planning_orchestrator_wires_hotel_ratings_coverage() -> None:
    """As of Step 177D, `PlanningOrchestrator._build_accommodation_inventory_report_safe`
    also derives and stores `provider_coverage.hotel_ratings` from
    `accommodation_inventory_report.hotel_ratings_status` -- see
    test_planning_orchestrator_accommodation_inventory.py for behavior
    tests. This is the intentional wiring point for this step, unlike
    Step 177B/177C where this module didn't reference hotel ratings at
    all."""
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "hotel_ratings" in source.lower()
    assert "provider_coverage.hotel_ratings" in source


def test_accommodation_inventory_service_wires_hotel_rating_enrichment() -> None:
    """As of Step 177C, `AccommodationInventoryService.build_report` runs
    its base result through `HotelRatingEnrichmentService.enrich` -- see
    test_accommodation_inventory_service.py and
    test_hotel_rating_enrichment_service.py for behavior tests. This is
    the intentional wiring point for this step, unlike Step 177B where
    this module didn't reference hotel ratings at all."""
    import app.services.accommodation_inventory_service as service_module

    source = inspect.getsource(service_module)
    assert "hotel_rating_enrichment_service" in source.lower()
    assert ".enrich(" in source
