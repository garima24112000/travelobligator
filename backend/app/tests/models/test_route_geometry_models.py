from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.common import ProviderStatus
from app.models.routing import (
    BufferSufficiencyStatus,
    RouteFeasibilityStatus,
    RouteLegFeasibility,
    RoutePathPoint,
    RouteResult,
    TravelTimeBuffer,
    TravelTimeBufferStatus,
)

# Model/contract tests for the Step 173A route geometry contract
# (docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
# Never calls a real routing provider or network service -- these only
# exercise pydantic validation on the models themselves. `RoutePathPoint`/
# `route_geometry` are pure data contracts: nothing here asserts anything
# about *where* a real point actually came from (that's the provider/
# service tests' job) -- only that the shape accepts real data and stays
# nullable for backward compatibility.


def _points() -> list[RoutePathPoint]:
    return [
        RoutePathPoint(lat=38.7223, lon=-9.1393),
        RoutePathPoint(lat=38.72, lon=-9.1396),
        RoutePathPoint(lat=38.7169, lon=-9.1399),
    ]


# ---------------------------------------------------------------------------
# 1. RoutePathPoint bounds mirror GeoPoint exactly -- an out-of-range
#    coordinate is rejected, never silently clamped.
# ---------------------------------------------------------------------------


def test_route_path_point_accepts_valid_coordinates() -> None:
    point = RoutePathPoint(lat=38.7223, lon=-9.1393)

    assert point.lat == pytest.approx(38.7223)
    assert point.lon == pytest.approx(-9.1393)


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [("lat", 90.1), ("lat", -90.1), ("lon", 180.1), ("lon", -180.1)],
)
def test_route_path_point_rejects_out_of_range_coordinates(
    field_name: str, invalid_value: float
) -> None:
    fields: dict[str, float] = {"lat": 38.7223, "lon": -9.1393}
    fields[field_name] = invalid_value

    with pytest.raises(ValidationError):
        RoutePathPoint(**fields)


# ---------------------------------------------------------------------------
# 2-3. RouteResult accepts real, provider-backed geometry when supplied,
#      and defaults to None (backward compatible, no straight-line
#      fallback ever constructed here).
# ---------------------------------------------------------------------------


def test_route_result_accepts_provider_backed_geometry() -> None:
    result = RouteResult(
        provider="osrm",
        status=ProviderStatus.SUCCESS,
        distance_meters=1500.0,
        duration_seconds=900.0,
        geometry=_points(),
        source="osrm",
        confidence=0.6,
    )

    assert result.geometry == _points()
    assert len(result.geometry) == 3


def test_route_result_geometry_defaults_to_none() -> None:
    result = RouteResult(
        provider="routing_provider",
        status=ProviderStatus.NOT_CONNECTED,
        source="routing_provider",
    )

    assert result.geometry is None


def test_route_result_never_auto_populates_geometry_from_status_alone() -> None:
    """Constructing a `success` RouteResult with no `geometry` argument
    must never implicitly synthesize one -- geometry is only ever what a
    caller (a real provider adapter) explicitly supplies."""
    result = RouteResult(
        provider="osrm",
        status=ProviderStatus.SUCCESS,
        distance_meters=1500.0,
        duration_seconds=900.0,
        source="osrm",
    )

    assert result.geometry is None


# ---------------------------------------------------------------------------
# 4-5. RouteLegFeasibility/TravelTimeBuffer accept route_geometry when
#      supplied, and stay backward-compatible (default None) otherwise.
# ---------------------------------------------------------------------------


def test_route_leg_feasibility_accepts_route_geometry() -> None:
    leg = RouteLegFeasibility(
        from_experience_id="experience_a",
        from_experience_name="A",
        to_experience_id="experience_b",
        to_experience_name="B",
        provider="osrm",
        status=ProviderStatus.SUCCESS,
        distance_meters=1500.0,
        duration_seconds=900.0,
        feasibility_status=RouteFeasibilityStatus.FEASIBLE,
        route_geometry=_points(),
    )

    assert leg.route_geometry == _points()


def test_route_leg_feasibility_route_geometry_defaults_to_none() -> None:
    leg = RouteLegFeasibility(
        from_experience_id="experience_a",
        from_experience_name="A",
        to_experience_id="experience_b",
        to_experience_name="B",
        provider="routing_provider",
        status=ProviderStatus.NOT_CONNECTED,
        feasibility_status=RouteFeasibilityStatus.NEEDS_REVIEW,
    )

    assert leg.route_geometry is None


def test_travel_time_buffer_accepts_route_geometry() -> None:
    buffer = TravelTimeBuffer(
        from_experience_id="experience_a",
        from_experience_name="A",
        to_experience_id="experience_b",
        to_experience_name="B",
        provider="osrm",
        status=TravelTimeBufferStatus.SUCCESS,
        route_duration_seconds=900.0,
        route_distance_meters=1500.0,
        recommended_buffer_seconds=900.0,
        buffer_status=BufferSufficiencyStatus.NOT_COMPUTABLE,
        route_geometry=_points(),
    )

    assert buffer.route_geometry == _points()


def test_travel_time_buffer_route_geometry_defaults_to_none() -> None:
    buffer = TravelTimeBuffer(
        from_experience_id="experience_a",
        from_experience_name="A",
        to_experience_id="experience_b",
        to_experience_name="B",
        provider="routing_provider",
        status=TravelTimeBufferStatus.NOT_CONNECTED,
        buffer_status=BufferSufficiencyStatus.UNAVAILABLE,
    )

    assert buffer.route_geometry is None


# ---------------------------------------------------------------------------
# 6. Round-trip serialization preserves geometry exactly (JSON mode, the
#    same mode PlanningStateRepository.save uses).
# ---------------------------------------------------------------------------


def test_route_result_geometry_round_trips_through_json_dump() -> None:
    result = RouteResult(
        provider="osrm",
        status=ProviderStatus.SUCCESS,
        distance_meters=1500.0,
        duration_seconds=900.0,
        geometry=_points(),
        source="osrm",
        confidence=0.6,
    )

    dumped = result.model_dump(mode="json")
    reloaded = RouteResult.model_validate(dumped)

    assert reloaded.geometry == _points()
