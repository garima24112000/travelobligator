from __future__ import annotations

import ast
import inspect

import pytest

from app.core.config import Settings
from app.providers.hotel_ratings import NotConnectedHotelRatingsProvider, get_hotel_ratings_provider
from app.providers.hotel_ratings import factory as factory_module

# Safety tests for the Step 177B hotel ratings provider factory. Mirrors
# test_accommodation_factory.py. Never calls a real network service.


# ---------------------------------------------------------------------------
# 10. Factory returns NotConnectedHotelRatingsProvider by default.
# ---------------------------------------------------------------------------


def test_factory_returns_not_connected_provider_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOTEL_RATINGS_PROVIDER", raising=False)
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    provider = get_hotel_ratings_provider()

    assert isinstance(provider, NotConnectedHotelRatingsProvider)


# ---------------------------------------------------------------------------
# 11. Factory explicit not_connected returns NotConnectedHotelRatingsProvider.
# ---------------------------------------------------------------------------


def test_factory_returns_not_connected_provider_for_explicit_name() -> None:
    provider = get_hotel_ratings_provider("not_connected")
    assert isinstance(provider, NotConnectedHotelRatingsProvider)


# ---------------------------------------------------------------------------
# 12. Factory falls back to not_connected for an unknown/unsupported name,
#     never raises, never fabricates a real provider.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsupported_name",
    ["google_places", "tripadvisor", "amadeus", "yelp", "made_up_provider", "", "NOT_CONNECTED"],
)
def test_factory_falls_back_to_not_connected_for_unsupported_names(
    unsupported_name: str,
) -> None:
    provider = get_hotel_ratings_provider(unsupported_name)

    assert isinstance(provider, NotConnectedHotelRatingsProvider)


def test_factory_uses_settings_when_no_provider_name_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module, "get_settings", lambda: Settings(hotel_ratings_provider="not_connected")
    )
    provider = get_hotel_ratings_provider()
    assert isinstance(provider, NotConnectedHotelRatingsProvider)


def test_factory_falls_back_when_settings_hold_unsupported_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module, "get_settings", lambda: Settings(hotel_ratings_provider="google_places")
    )
    provider = get_hotel_ratings_provider()
    assert isinstance(provider, NotConnectedHotelRatingsProvider)


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
                assert disallowed not in lowered, f"Disallowed import found: {name}"
        for disallowed in internal_disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


def test_not_connected_module_has_no_disallowed_imports() -> None:
    import app.providers.hotel_ratings.not_connected as not_connected_module

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
# Only "not_connected" is a supported provider name as of Step 177B -- no
# real Google Places/Tripadvisor/Amadeus/Yelp adapter exists to select.
# ---------------------------------------------------------------------------


def test_only_not_connected_is_currently_supported() -> None:
    assert list(factory_module._SUPPORTED_PROVIDERS.keys()) == ["not_connected"]
