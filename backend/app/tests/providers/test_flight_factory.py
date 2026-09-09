from __future__ import annotations

import ast
import inspect

import pytest

from app.core.config import Settings
from app.providers.flights import (
    KiwiMcpFlightProvider,
    NotConnectedFlightProvider,
    ScrapedLocalFlightProvider,
    get_flight_provider,
)
from app.providers.flights import factory as factory_module

# Safety tests for the Step 169B flight provider factory. Mirrors
# test_accommodation_factory.py. Never calls a real network service.


# ---------------------------------------------------------------------------
# 12. Factory default returns ScrapedLocalFlightProvider.
# ---------------------------------------------------------------------------


def test_factory_returns_scraped_local_provider_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FLIGHT_PROVIDER", raising=False)
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    provider = get_flight_provider()

    assert isinstance(provider, ScrapedLocalFlightProvider)


def test_settings_default_flight_provider_is_scraped_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FLIGHT_PROVIDER", raising=False)
    settings = Settings(_env_file=None)
    assert settings.flight_provider == "scraped_local"


def test_factory_uses_settings_when_no_provider_name_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module, "get_settings", lambda: Settings(flight_provider="not_connected")
    )
    provider = get_flight_provider()
    assert isinstance(provider, NotConnectedFlightProvider)


# ---------------------------------------------------------------------------
# 13. Factory explicit "scraped_local" returns ScrapedLocalFlightProvider.
# ---------------------------------------------------------------------------


def test_factory_returns_scraped_local_provider_for_explicit_name() -> None:
    provider = get_flight_provider("scraped_local")
    assert isinstance(provider, ScrapedLocalFlightProvider)


# ---------------------------------------------------------------------------
# 14. Factory explicit "not_connected" returns NotConnectedFlightProvider.
# ---------------------------------------------------------------------------


def test_factory_returns_not_connected_provider_for_explicit_name() -> None:
    provider = get_flight_provider("not_connected")
    assert isinstance(provider, NotConnectedFlightProvider)


def test_factory_returns_not_connected_when_explicitly_selected_even_if_scraping_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator can still explicitly opt out of the default
    scraped_local provider by setting flight_provider="not_connected",
    even with scraping_enabled/scraped_flight_provider_enabled both
    True."""
    monkeypatch.setattr(
        factory_module,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            scraping_enabled=True,
            scraped_flight_provider_enabled=True,
            flight_provider="not_connected",
        ),
    )

    provider = get_flight_provider()

    assert isinstance(provider, NotConnectedFlightProvider)


# ---------------------------------------------------------------------------
# Step 178B: factory explicit "kiwi_mcp" returns KiwiMcpFlightProvider --
# never selected by default, never causes a network call by itself
# (KiwiMcpFlightProvider only connects when Settings.kiwi_mcp_enabled is
# also explicitly True; construction alone never connects to anything).
# ---------------------------------------------------------------------------


def test_factory_returns_kiwi_mcp_provider_for_explicit_name() -> None:
    provider = get_flight_provider("kiwi_mcp")
    assert isinstance(provider, KiwiMcpFlightProvider)


def test_factory_default_is_not_kiwi_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLIGHT_PROVIDER", raising=False)
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    provider = get_flight_provider()

    assert not isinstance(provider, KiwiMcpFlightProvider)
    assert isinstance(provider, ScrapedLocalFlightProvider)


# ---------------------------------------------------------------------------
# Step 182E: "manual_html" is a non-breaking alias for "scraped_local" --
# same adapter class, same local-file-only/no-live-fetch behavior, and
# completely distinct from "kiwi_mcp".
# ---------------------------------------------------------------------------


def test_factory_returns_scraped_local_provider_for_manual_html_alias() -> None:
    provider = get_flight_provider("manual_html")
    assert isinstance(provider, ScrapedLocalFlightProvider)
    assert not isinstance(provider, KiwiMcpFlightProvider)


def test_manual_html_alias_does_not_remove_scraped_local() -> None:
    assert isinstance(get_flight_provider("scraped_local"), ScrapedLocalFlightProvider)
    assert isinstance(get_flight_provider("manual_html"), ScrapedLocalFlightProvider)
    assert type(get_flight_provider("scraped_local")) is type(get_flight_provider("manual_html"))


# ---------------------------------------------------------------------------
# 15. Factory unknown provider falls back safely to
#     NotConnectedFlightProvider. "kiwi_mcp" itself is now a *supported*
#     name (Step 178B) so it is deliberately not included below. Also
#     includes the partner-only provider names documented in README.md's
#     "Provider activation" section (Step 182E) -- selecting one without a
#     real adapter/credentials must always fall back to not_connected,
#     never fabricate flight inventory.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsupported_name",
    [
        "amadeus",
        "duffel",
        "kiwi",
        "skyscanner",
        "made_up_provider",
        "",
        "NOT_CONNECTED",
        "KIWI_MCP",
    ],
)
def test_factory_falls_back_to_not_connected_for_unsupported_names(
    unsupported_name: str,
) -> None:
    provider = get_flight_provider(unsupported_name)

    assert isinstance(provider, NotConnectedFlightProvider)


def test_factory_falls_back_when_settings_hold_unsupported_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module, "get_settings", lambda: Settings(flight_provider="amadeus")
    )
    provider = get_flight_provider()
    assert isinstance(provider, NotConnectedFlightProvider)


# ---------------------------------------------------------------------------
# 16/17. Factory/provider creation does not call network, and flight
# provider modules do not import requests/httpx/browser automation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "app.providers.flights.factory",
        "app.providers.flights.not_connected_adapter",
        "app.providers.flights.scraped_adapter",
        "app.providers.flights.kiwi_mcp_adapter",
        "app.providers.flights.kiwi_mcp_parser",
    ],
)
def test_flight_provider_modules_have_no_disallowed_imports(module_name: str) -> None:
    """Note: `app.providers.flights.kiwi_mcp_adapter`/`kiwi_mcp_parser`
    are included here deliberately -- both modules import only internal
    `app.*` modules (never `mcp` directly; see
    test_kiwi_mcp_adapter.py::test_only_kiwi_mcp_client_module_imports_the_mcp_sdk),
    so they pass this same strict check every other flight provider
    module does. `app.providers.flights.kiwi_mcp_client` is intentionally
    NOT included in this list -- that module's whole job is to import the
    real `mcp` SDK (lazily), which this check would otherwise flag."""
    import importlib

    module = importlib.import_module(module_name)
    source = inspect.getsource(module)
    tree = ast.parse(source)

    vendor_disallowed_substrings = (
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
        "selenium",
        "playwright",
    )
    internal_disallowed_substrings = (
        "app.providers.places",
        "app.providers.weather",
        "app.providers.holidays",
        "app.providers.currency",
        "app.providers.gateway",
        "app.providers.routing",
        "app.providers.accommodation",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        if not lowered.startswith("app."):
            for disallowed in vendor_disallowed_substrings:
                assert disallowed not in lowered, f"{module_name}: disallowed import found: {name}"
        for disallowed in internal_disallowed_substrings:
            assert disallowed not in lowered, f"{module_name}: disallowed import found: {name}"


# ---------------------------------------------------------------------------
# Not wired into ProviderGateway or PlanningOrchestrator yet.
# ---------------------------------------------------------------------------


def test_provider_gateway_now_wires_flight_factory() -> None:
    """Step 169E wires `ProviderGateway` to the flight factory -- mirroring
    `test_accommodation_factory.py::test_provider_gateway_wires_accommodation_factory`.
    See test_provider_gateway_flights.py for the gateway's behavior
    tests."""
    import app.providers.gateway as gateway_module

    source = inspect.getsource(gateway_module)
    assert "get_flight_provider" in source
    assert "self.flight_inventory" in source
    assert "def search_flights(" in source


def test_planning_orchestrator_does_not_reference_flight_factory() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "get_flight_provider" not in source
    assert "FlightInventoryProvider" not in source
    assert "app.providers.flights" not in source
