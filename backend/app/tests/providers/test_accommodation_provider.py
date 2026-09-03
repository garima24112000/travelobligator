from __future__ import annotations

import ast
import inspect
from datetime import date

import pytest

from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus
from app.providers.accommodation import AccommodationInventoryProvider

# Safety/skeleton tests for Step 167A's accommodation inventory provider
# contract. Never calls a real network service and never requires any
# lodging provider configuration.


def _request() -> AccommodationSearchRequest:
    return AccommodationSearchRequest(
        destination="Lisbon, Portugal",
        check_in_date=date(2026, 10, 10),
        check_out_date=date(2026, 10, 14),
        adults=2,
        rooms=1,
    )


# ---------------------------------------------------------------------------
# 11. Provider interface can be subclassed by a fake test provider.
# ---------------------------------------------------------------------------


def test_accommodation_inventory_provider_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        AccommodationInventoryProvider()  # type: ignore[abstract]


class _FakeAccommodationInventoryProvider(AccommodationInventoryProvider):
    """Deterministic test double proving the interface can be subclassed
    without touching any real lodging provider."""

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
        return AccommodationSearchResult(
            provider=self.provider_name,
            status=AccommodationSearchStatus.NOT_CONNECTED,
            offers=[],
            message="Fake provider for test purposes only.",
        )


def test_fake_provider_can_subclass_interface() -> None:
    provider = _FakeAccommodationInventoryProvider()
    result = provider.search_accommodations(_request())
    assert isinstance(result, AccommodationSearchResult)
    assert result.status == AccommodationSearchStatus.NOT_CONNECTED
    assert provider.calls == [_request()]


def test_fake_provider_can_return_success_with_offers() -> None:
    offer = AccommodationOffer(
        provider="fake_accommodation_inventory_provider",
        provider_property_id="prop_1",
        property_name="Fake Property",
        data_status=DataStatus.LIVE,
    )
    fake_result = AccommodationSearchResult(
        provider="fake_accommodation_inventory_provider",
        status=AccommodationSearchStatus.SUCCESS,
        offers=[offer],
    )
    provider = _FakeAccommodationInventoryProvider(result=fake_result)
    result = provider.search_accommodations(_request())
    assert result.status == AccommodationSearchStatus.SUCCESS
    assert result.offers[0].property_name == "Fake Property"


def test_incomplete_subclass_missing_method_cannot_be_instantiated() -> None:
    class _IncompleteProvider(AccommodationInventoryProvider):
        pass

    with pytest.raises(TypeError):
        _IncompleteProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Module import safety: no LangGraph, Groq, Anthropic, Kiwi/MCP, httpx, or
# other live/network/vendor import in the contract module.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "app.providers.accommodation.base",
        "app.models.accommodation",
    ],
)
def test_accommodation_skeleton_modules_have_no_disallowed_imports(module_name: str) -> None:
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


def test_provider_gateway_wires_accommodation_inventory_contract() -> None:
    """As of Step 167C, `ProviderGateway` exposes this contract through a
    dedicated `accommodation_inventory` slot + `search_accommodations`
    method -- see test_provider_gateway_accommodation.py for behavior
    tests. The pre-existing `accommodation` slot (`app.providers.base.
    AccommodationProvider` stub) is untouched and remains separate."""
    import app.providers.gateway as gateway_module

    source = inspect.getsource(gateway_module)
    assert "AccommodationInventoryProvider" in source
    assert "accommodation_inventory" in source.lower()
    assert "app.providers.accommodation" in source


def test_planning_orchestrator_wires_accommodation_inventory_report() -> None:
    """As of Step 167D, `PlanningOrchestrator` computes and stores
    `accommodation_inventory_report` via `AccommodationInventoryService`
    (which itself calls `ProviderGateway.search_accommodations`, not this
    contract's classes directly) -- see
    test_planning_orchestrator_accommodation_inventory.py for behavior
    tests."""
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "accommodation_inventory_report" in source
    assert "AccommodationInventoryService" in source
