from __future__ import annotations

import ast
import inspect

import pytest

from app.core.config import Settings
from app.providers.accommodation import (
    NotConnectedAccommodationProvider,
    ScrapedAccommodationProvider,
    get_accommodation_provider,
)
from app.providers.accommodation import factory as factory_module

# Safety tests for the Step 167B accommodation provider factory
# (default flipped to "scraped_local" in Step 168F). Mirrors
# test_routing_factory.py. Never calls a real network service.


# ---------------------------------------------------------------------------
# Factory returns ScrapedAccommodationProvider by default (Step 168F).
# ---------------------------------------------------------------------------


def test_factory_returns_scraped_local_provider_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACCOMMODATION_PROVIDER", raising=False)
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    provider = get_accommodation_provider()

    assert isinstance(provider, ScrapedAccommodationProvider)


def test_factory_returns_not_connected_provider_for_explicit_name() -> None:
    provider = get_accommodation_provider("not_connected")
    assert isinstance(provider, NotConnectedAccommodationProvider)


# ---------------------------------------------------------------------------
# Step 182E: "manual_html" is a non-breaking alias for "scraped_local" --
# same adapter class, same local-file-only/no-live-fetch behavior.
# ---------------------------------------------------------------------------


def test_factory_returns_scraped_local_provider_for_manual_html_alias() -> None:
    provider = get_accommodation_provider("manual_html")
    assert isinstance(provider, ScrapedAccommodationProvider)


def test_manual_html_alias_does_not_remove_scraped_local() -> None:
    assert isinstance(get_accommodation_provider("scraped_local"), ScrapedAccommodationProvider)
    assert isinstance(get_accommodation_provider("manual_html"), ScrapedAccommodationProvider)
    assert type(get_accommodation_provider("scraped_local")) is type(
        get_accommodation_provider("manual_html")
    )


# ---------------------------------------------------------------------------
# Factory behavior for unsupported provider names is safe. Includes every
# partner-only provider name documented in README.md's "Provider
# activation" section (Step 182E) -- selecting a partner-only provider
# without a real adapter/credentials must always fall back to
# not_connected, never fabricate lodging inventory.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsupported_name",
    [
        "booking",
        "expedia",
        "hotelbeds",
        "hostelworld",
        "vrbo",
        "airbnb",
        "made_up_provider",
        "",
        "NOT_CONNECTED",
    ],
)
def test_factory_falls_back_to_not_connected_for_unsupported_names(
    unsupported_name: str,
) -> None:
    provider = get_accommodation_provider(unsupported_name)

    assert isinstance(provider, NotConnectedAccommodationProvider)


# ---------------------------------------------------------------------------
# Config default is "scraped_local" (Step 168F).
# ---------------------------------------------------------------------------


def test_settings_default_accommodation_provider_is_scraped_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACCOMMODATION_PROVIDER", raising=False)
    settings = Settings(_env_file=None)
    assert settings.accommodation_provider == "scraped_local"


def test_factory_uses_settings_when_no_provider_name_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module,
        "get_settings",
        lambda: Settings(_env_file=None, accommodation_provider="not_connected"),
    )
    provider = get_accommodation_provider()
    assert isinstance(provider, NotConnectedAccommodationProvider)


def test_factory_falls_back_when_settings_hold_unsupported_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module,
        "get_settings",
        lambda: Settings(_env_file=None, accommodation_provider="booking"),
    )
    provider = get_accommodation_provider()
    assert isinstance(provider, NotConnectedAccommodationProvider)


# ---------------------------------------------------------------------------
# Factory/provider creation never calls the network or fabricates data.
# ---------------------------------------------------------------------------


def test_factory_module_has_no_disallowed_imports() -> None:
    source = inspect.getsource(factory_module)
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
    )
    internal_disallowed_substrings = (
        "app.providers.places",
        "app.providers.weather",
        "app.providers.holidays",
        "app.providers.currency",
        "app.providers.gateway",
        "app.providers.routing",
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
                assert disallowed not in lowered, f"Disallowed import found: {name}"
        for disallowed in internal_disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


def test_not_connected_adapter_module_has_no_disallowed_imports() -> None:
    import app.providers.accommodation.not_connected_adapter as not_connected_module

    source = inspect.getsource(not_connected_module)
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
            assert disallowed not in lowered, f"Disallowed import found: {name}"


# ---------------------------------------------------------------------------
# Not wired into ProviderGateway or PlanningOrchestrator yet.
# ---------------------------------------------------------------------------


def test_provider_gateway_wires_accommodation_factory() -> None:
    """Step 167C: `ProviderGateway` now imports and uses the accommodation
    factory -- this is the intentional wiring point for this step, unlike
    Step 167B where the gateway didn't reference accommodation inventory
    at all. See test_provider_gateway_accommodation.py for behavior
    tests."""
    import app.providers.gateway as gateway_module

    source = inspect.getsource(gateway_module)
    assert "get_accommodation_provider" in source
    assert "self.accommodation_inventory" in source
    assert "def search_accommodations(" in source


def test_provider_gateway_default_accommodation_provider_is_scraped_local() -> None:
    """Constructing `ProviderGateway()` with no explicit
    `accommodation_inventory=` must resolve the same default the factory
    itself uses (Step 168F: `scraped_local`) -- confirming the gateway
    wiring didn't change the underlying default. This still never
    fabricates data: with no local HTML file present, the provider
    reports `unavailable`, never a fake offer."""
    from app.providers.gateway import ProviderGateway

    gateway = ProviderGateway()

    assert isinstance(gateway.accommodation_inventory, ScrapedAccommodationProvider)


def test_planning_orchestrator_does_not_reference_accommodation_factory() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "get_accommodation_provider" not in source
    assert "AccommodationInventoryProvider" not in source
    assert "app.providers.accommodation" not in source
