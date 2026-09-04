from __future__ import annotations

import pytest

from app.providers.flights import FlightInventoryProvider

# Safety/skeleton tests for Step 169A's flight inventory provider contract.
# This suite never calls a real network service and never requires any
# flight provider configuration -- there is no concrete adapter to test
# yet, only the interface itself.


# ---------------------------------------------------------------------------
# 15. FlightInventoryProvider interface is importable.
# ---------------------------------------------------------------------------


def test_flight_inventory_provider_is_importable() -> None:
    assert FlightInventoryProvider is not None


def test_flight_inventory_provider_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        FlightInventoryProvider()  # type: ignore[abstract]


def test_flight_inventory_provider_requires_search_flights_implementation() -> None:
    class _IncompleteProvider(FlightInventoryProvider):
        pass

    with pytest.raises(TypeError):
        _IncompleteProvider()  # type: ignore[abstract]


def test_flight_inventory_provider_default_provider_name() -> None:
    assert FlightInventoryProvider.provider_name == "flight_inventory_provider"


# ---------------------------------------------------------------------------
# Module import safety: no LangGraph, Groq, Anthropic, Kiwi/MCP, httpx,
# requests, or browser-automation import in the flight provider skeleton.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "app.providers.flights.base",
        "app.models.flight",
    ],
)
def test_flight_skeleton_modules_have_no_disallowed_imports(module_name: str) -> None:
    import ast
    import importlib
    import inspect

    module = importlib.import_module(module_name)
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
        "selenium",
        "playwright",
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
