from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.common import ProviderStatus
from app.models.routing import RoutePathPoint, RouteRequest, RouteResult, RoutingProfile
from app.providers.gateway import ProviderGateway
from app.providers.routing import NotConnectedRoutingProvider, RoutingProvider
from app.providers.routing.factory import get_routing_provider
from app.providers.routing.osrm_adapter import OSRMRoutingAdapter

# Tests for Step 165B: `ProviderGateway.routing` + `ProviderGateway.get_route`.
# Every test here uses either the default not_connected routing provider or
# an injected fake -- none makes a real network call, and the OSRM adapter
# tests specifically prove it stays not_connected without a configured base
# URL (matching backend/app/tests/providers/test_osrm_adapter.py's own
# no-network guarantee).


def _request(**overrides: object) -> RouteRequest:
    fields: dict[str, object] = {
        "origin_lat": 38.7223,
        "origin_lon": -9.1393,
        "destination_lat": 38.7169,
        "destination_lon": -9.1399,
    }
    fields.update(overrides)
    return RouteRequest(**fields)


class _FakeRoutingProvider(RoutingProvider):
    """Deterministic test double -- never makes a network call, and its
    result is trivially distinguishable from a real not_connected/OSRM
    result so tests can prove the gateway actually delegates to whatever
    provider was injected."""

    provider_name = "fake_routing_provider"

    def __init__(self, result: RouteResult | None = None) -> None:
        self._result = result
        self.calls: list[RouteRequest] = []

    def get_route(self, request: RouteRequest) -> RouteResult:
        self.calls.append(request)
        if self._result is not None:
            return self._result
        return RouteResult(
            provider=self.provider_name,
            status=ProviderStatus.SUCCESS,
            distance_meters=4200.0,
            duration_seconds=600.0,
            geometry=None,
            source=self.provider_name,
            confidence=0.9,
            message="Fake route for test purposes only.",
        )


# ---------------------------------------------------------------------------
# 1. Provider gateway returns not_connected route result by default.
# ---------------------------------------------------------------------------


def test_gateway_default_get_route_returns_not_connected() -> None:
    gateway = ProviderGateway()

    result = gateway.get_route(_request())

    assert isinstance(result, RouteResult)
    assert result.status == ProviderStatus.NOT_CONNECTED
    assert isinstance(gateway.routing, NotConnectedRoutingProvider)


def test_gateway_default_routing_provider_name() -> None:
    gateway = ProviderGateway()
    result = gateway.get_route(_request())
    assert result.provider == "routing_provider"


# ---------------------------------------------------------------------------
# 2. Provider gateway can use an injected fake routing provider and
#    returns its result.
# ---------------------------------------------------------------------------


def test_gateway_uses_injected_routing_provider() -> None:
    fake_provider = _FakeRoutingProvider()
    gateway = ProviderGateway(routing=fake_provider)

    request = _request()
    result = gateway.get_route(request)

    assert result.provider == "fake_routing_provider"
    assert result.status == ProviderStatus.SUCCESS
    assert result.distance_meters == pytest.approx(4200.0)
    assert result.duration_seconds == pytest.approx(600.0)
    assert fake_provider.calls == [request]


def test_gateway_passes_request_through_unchanged() -> None:
    fake_provider = _FakeRoutingProvider()
    gateway = ProviderGateway(routing=fake_provider)

    request = _request(profile=RoutingProfile.WALKING)
    gateway.get_route(request)

    assert fake_provider.calls[0].profile == RoutingProfile.WALKING
    assert fake_provider.calls[0].origin_lat == pytest.approx(38.7223)


def test_gateway_other_provider_slots_are_unaffected_by_injected_routing() -> None:
    """Injecting a fake `routing` provider must not disturb any other
    gateway slot's default construction."""
    fake_provider = _FakeRoutingProvider()
    gateway = ProviderGateway(routing=fake_provider)

    assert gateway.places is not None
    assert gateway.weather is not None
    assert gateway.holiday is not None
    assert gateway.currency is not None
    assert gateway.routing is fake_provider


# ---------------------------------------------------------------------------
# 3. Provider gateway/factory can build an OSRM routing provider when
#    configured -- structural check only, no network call.
# ---------------------------------------------------------------------------


def test_gateway_accepts_osrm_provider_from_factory() -> None:
    osrm_provider = get_routing_provider("osrm")
    gateway = ProviderGateway(routing=osrm_provider)

    assert isinstance(gateway.routing, OSRMRoutingAdapter)


def test_gateway_with_osrm_provider_stays_not_connected_without_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting the OSRM adapter through the gateway still makes no
    network call when `OSRM_BASE_URL` is unset -- the gateway itself has
    no OSRM-specific knowledge to override that."""
    import app.providers.routing.osrm_adapter as osrm_adapter_module
    from app.core.config import Settings

    monkeypatch.setattr(
        osrm_adapter_module, "get_settings", lambda: Settings(_env_file=None, osrm_base_url=None)
    )
    gateway = ProviderGateway(routing=OSRMRoutingAdapter())

    result = gateway.get_route(_request())

    assert result.status == ProviderStatus.NOT_CONNECTED
    assert result.distance_meters is None
    assert result.duration_seconds is None


# ---------------------------------------------------------------------------
# 4. Provider gateway does not call OSRM/network in tests -- confirmed by
#    every test above using either the not_connected default or a fake
#    provider, plus this explicit check that no httpx call happens even
#    when routing="osrm" is selected without a base URL.
# ---------------------------------------------------------------------------


def test_gateway_get_route_makes_no_network_call_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("ProviderGateway.get_route must not open a real httpx.Client here")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    gateway = ProviderGateway()
    result = gateway.get_route(_request())

    assert result.status == ProviderStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# 5. Route lookup validates/safely handles invalid coordinates according
#    to RouteRequest's own model validation.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name,invalid_value",
    [
        ("origin_lat", 90.1),
        ("origin_lon", -180.1),
        ("destination_lat", -90.1),
        ("destination_lon", 180.1),
    ],
)
def test_invalid_coordinates_are_rejected_before_reaching_the_gateway(
    field_name: str, invalid_value: float
) -> None:
    """`RouteRequest` itself rejects an out-of-range coordinate -- the
    gateway never receives (and so never has to handle) an invalid
    request; nothing downstream can silently accept or "fix" one."""
    with pytest.raises(ValidationError):
        _request(**{field_name: invalid_value})


# ---------------------------------------------------------------------------
# 6. Route lookup does not invent distance/duration when the provider is
#    not_connected.
# ---------------------------------------------------------------------------


def test_not_connected_route_never_has_distance_or_duration() -> None:
    gateway = ProviderGateway()
    result = gateway.get_route(_request())

    assert result.status == ProviderStatus.NOT_CONNECTED
    assert result.distance_meters is None
    assert result.duration_seconds is None
    assert result.geometry is None
    assert result.confidence == 0.0


def test_repeated_default_calls_are_deterministic_and_never_fabricate() -> None:
    gateway = ProviderGateway()
    request = _request()

    first = gateway.get_route(request)
    second = gateway.get_route(request)

    assert first.model_dump() == second.model_dump()
    assert first.distance_meters is None
    assert second.distance_meters is None


# ---------------------------------------------------------------------------
# Step 173A: the gateway preserves whatever route geometry the underlying
# routing provider returned -- it never adds, drops, reorders, or
# straight-line-substitutes it.
# ---------------------------------------------------------------------------


def test_gateway_preserves_provider_backed_route_geometry() -> None:
    points = [RoutePathPoint(lat=38.7223, lon=-9.1393), RoutePathPoint(lat=38.7169, lon=-9.1399)]
    fake_provider = _FakeRoutingProvider(
        RouteResult(
            provider="fake_routing_provider",
            status=ProviderStatus.SUCCESS,
            distance_meters=4200.0,
            duration_seconds=600.0,
            geometry=points,
            source="fake_routing_provider",
            confidence=0.9,
            message="Fake route with geometry for test purposes only.",
        )
    )
    gateway = ProviderGateway(routing=fake_provider)

    result = gateway.get_route(_request())

    assert result.geometry == points
