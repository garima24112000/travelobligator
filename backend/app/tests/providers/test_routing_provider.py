from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.models.common import ProviderStatus
from app.models.routing import RouteRequest, RouteResult, RoutingProfile
from app.providers.routing import NotConnectedRoutingProvider, RoutingProvider

# Safety/skeleton tests for Step 165A's routing provider contract
# (`RouteRequest`/`RouteResult`) and its default `NotConnectedRoutingProvider`
# adapter. This suite never calls a real network service and never requires
# any OSRM configuration -- see test_osrm_adapter.py for OSRM-specific
# behavior against a fake HTTP client.


def _request(**overrides: object) -> RouteRequest:
    fields: dict[str, object] = {
        "origin_lat": 38.7223,
        "origin_lon": -9.1393,
        "destination_lat": 38.7169,
        "destination_lon": -9.1399,
    }
    fields.update(overrides)
    return RouteRequest(**fields)


# ---------------------------------------------------------------------------
# 1. RoutingProvider cannot be instantiated directly.
# ---------------------------------------------------------------------------


def test_routing_provider_interface_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        RoutingProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# RouteRequest validation.
# ---------------------------------------------------------------------------


def test_route_request_accepts_valid_coordinates() -> None:
    request = _request()
    assert request.origin_lat == pytest.approx(38.7223)
    assert request.profile == RoutingProfile.DRIVING


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [
        ("origin_lat", 91.0),
        ("origin_lat", -91.0),
        ("origin_lon", 181.0),
        ("origin_lon", -181.0),
        ("destination_lat", 90.5),
        ("destination_lon", -200.0),
    ],
)
def test_route_request_rejects_invalid_lat_lon(field_name: str, invalid_value: float) -> None:
    with pytest.raises(ValidationError):
        _request(**{field_name: invalid_value})


def test_route_request_defaults_to_driving_profile() -> None:
    request = _request()
    assert request.profile == RoutingProfile.DRIVING


def test_route_request_accepts_explicit_profile() -> None:
    request = _request(profile=RoutingProfile.WALKING)
    assert request.profile == RoutingProfile.WALKING


def test_route_request_rejects_unsupported_profile_string() -> None:
    with pytest.raises(ValidationError):
        _request(profile="teleport")


# ---------------------------------------------------------------------------
# 2. NotConnectedRoutingProvider returns not_connected with no
#    distance/duration (Required test 1, 9).
# ---------------------------------------------------------------------------


def test_not_connected_provider_returns_not_connected_status() -> None:
    provider = NotConnectedRoutingProvider()
    result = provider.get_route(_request())
    assert result.status == ProviderStatus.NOT_CONNECTED


def test_not_connected_provider_returns_no_distance_or_duration() -> None:
    provider = NotConnectedRoutingProvider()
    result = provider.get_route(_request())
    assert result.distance_meters is None
    assert result.duration_seconds is None
    assert result.geometry is None


def test_not_connected_provider_returns_zero_confidence() -> None:
    provider = NotConnectedRoutingProvider()
    result = provider.get_route(_request())
    assert result.confidence == 0.0


def test_not_connected_provider_returns_expected_provider_name() -> None:
    provider = NotConnectedRoutingProvider()
    result = provider.get_route(_request())
    assert result.provider == "routing_provider"
    assert result.source == "routing_provider"
    assert provider.provider_name == "routing_provider"


def test_not_connected_provider_repeated_calls_are_deterministic() -> None:
    provider = NotConnectedRoutingProvider()
    request = _request()
    first = provider.get_route(request)
    second = provider.get_route(request)
    assert first.model_dump() == second.model_dump()


def test_not_connected_provider_does_not_mutate_request() -> None:
    provider = NotConnectedRoutingProvider()
    request = _request()
    before = copy.deepcopy(request.model_dump())
    provider.get_route(request)
    after = request.model_dump()
    assert before == after


def test_not_connected_provider_output_is_a_valid_result_instance() -> None:
    provider = NotConnectedRoutingProvider()
    result = provider.get_route(_request())
    assert isinstance(result, RouteResult)
    RouteResult.model_validate(result.model_dump())


# ---------------------------------------------------------------------------
# Module import safety: no LangGraph, Groq, Anthropic, Kiwi/MCP, httpx, or
# other provider-adapter import in the base/not_connected modules.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "app.providers.routing.base",
        "app.providers.routing.not_connected_adapter",
    ],
)
def test_routing_skeleton_modules_have_no_disallowed_imports(module_name: str) -> None:
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
# Straight-line (haversine) distance must never be presented as a route.
# ---------------------------------------------------------------------------


def test_route_result_has_no_haversine_or_straight_line_field() -> None:
    """`RouteResult` must never carry a field that could be mistaken for a
    real route value but is actually a straight-line estimate -- those stay
    entirely in `app.utils.geo.haversine_distance_km`, called separately
    and never substituted into a `RouteResult`."""
    field_names = set(RouteResult.model_fields.keys())
    for forbidden in ("haversine_distance_km", "straight_line_km", "straight_line_distance"):
        assert forbidden not in field_names
