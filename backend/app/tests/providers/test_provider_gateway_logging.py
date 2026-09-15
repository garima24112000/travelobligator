from __future__ import annotations

import json
import logging
from datetime import date

import pytest

from app.core.logging_config import APP_LOGGER_NAME, JsonFormatter
from app.core.request_context import request_id_scope
from app.models.accommodation import (
    AccommodationOffer,
    AccommodationSearchRequest,
    AccommodationSearchResult,
    AccommodationSearchStatus,
)
from app.models.common import DataStatus, ProviderStatus
from app.models.flight import FlightSearchRequest, FlightSearchResult, FlightSearchStatus
from app.models.routing import RouteRequest, RouteResult
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.flights.base import FlightInventoryProvider
from app.providers.gateway import ProviderGateway
from app.providers.routing import RoutingProvider

# Tests for the Step 187F provider/gateway observability
# (docs/14_backend_architecture.md section 125). Every test attaches a
# small capture handler directly to the shared "app" logger (which has
# `propagate=False`, matching the pattern Steps 187C/187D/187E already
# established) rather than asserting on stdout text.

_FORBIDDEN_LOG_FIELDS = (
    "email",
    "password",
    "password_hash",
    "session",
    "session_cookie",
    "cookie",
    "authorization",
    "token",
    "secret",
    "api_key",
    "request_body",
    "response_body",
    "provider_payload",
    "planning_state",
    "itinerary",
    "destination",
    "origin",
    "coordinates",
    "prompt",
)


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def capture() -> _CaptureHandler:
    app_logger = logging.getLogger(APP_LOGGER_NAME)
    handler = _CaptureHandler()
    app_logger.addHandler(handler)
    try:
        yield handler
    finally:
        app_logger.removeHandler(handler)


def _assert_no_forbidden_fields(record: logging.LogRecord) -> None:
    for name in _FORBIDDEN_LOG_FIELDS:
        assert not hasattr(record, name), f"log record must never carry {name!r}"


def _records_with(capture: _CaptureHandler, **attrs: object) -> list[logging.LogRecord]:
    return [
        record
        for record in capture.records
        if all(getattr(record, key, None) == value for key, value in attrs.items())
    ]


class _FakeRoutingProvider(RoutingProvider):
    provider_name = "fake_routing_provider"

    def __init__(self, result: RouteResult | None = None) -> None:
        self._result = result

    def get_route(self, request: RouteRequest) -> RouteResult:
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


class _FakeAccommodationInventoryProvider(AccommodationInventoryProvider):
    provider_name = "fake_accommodation_inventory_provider"

    def __init__(self, result: AccommodationSearchResult | None = None) -> None:
        self._result = result

    def search_accommodations(
        self, request: AccommodationSearchRequest
    ) -> AccommodationSearchResult:
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


class _FakeFlightInventoryProvider(FlightInventoryProvider):
    provider_name = "fake_flight_inventory_provider"

    def __init__(self, result: FlightSearchResult | None = None) -> None:
        self._result = result

    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResult:
        if self._result is not None:
            return self._result
        return FlightSearchResult(
            provider=self.provider_name,
            status=FlightSearchStatus.UNAVAILABLE,
            offers=[],
            message="Fake provider for test purposes only.",
            destination=request.destination,
            departure_date=request.departure_date,
        )


def _route_request() -> RouteRequest:
    return RouteRequest(
        origin_lat=38.7223, origin_lon=-9.1393, destination_lat=38.7169, destination_lon=-9.1399
    )


def _accommodation_request() -> AccommodationSearchRequest:
    return AccommodationSearchRequest(
        destination="Lisbon, Portugal",
        check_in_date=date(2026, 10, 10),
        check_out_date=date(2026, 10, 14),
        adults=2,
        rooms=1,
    )


def _flight_request() -> FlightSearchRequest:
    return FlightSearchRequest(origin="JFK", destination="LIS", departure_date=date(2026, 10, 10))


# ---------------------------------------------------------------------------
# Success -> info, with real, safe fields
# ---------------------------------------------------------------------------


def test_get_route_success_logs_info_with_safe_fields(capture: _CaptureHandler) -> None:
    gateway = ProviderGateway(routing=_FakeRoutingProvider())

    with request_id_scope("req_gateway_route_test"):
        result = gateway.get_route(_route_request())

    assert result.status == ProviderStatus.SUCCESS
    matching = _records_with(capture, stage="routing", status="success")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "INFO"
    assert record.provider == "fake_routing_provider"
    assert record.request_id == "req_gateway_route_test"
    assert isinstance(record.duration_ms, float)
    assert record.duration_ms >= 0
    assert not hasattr(record, "error_code")
    _assert_no_forbidden_fields(record)


def test_search_accommodations_success_logs_info(capture: _CaptureHandler) -> None:
    gateway = ProviderGateway(accommodation_inventory=_FakeAccommodationInventoryProvider())

    gateway.search_accommodations(_accommodation_request())

    matching = _records_with(capture, stage="accommodations", status="success")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "INFO"
    assert record.provider == "fake_accommodation_inventory_provider"
    _assert_no_forbidden_fields(record)


def test_search_flights_success_logs_info(capture: _CaptureHandler) -> None:
    success_result = FlightSearchResult(
        provider="fake_flight_inventory_provider",
        status=FlightSearchStatus.SUCCESS,
        offers=[],
        message="ok",
        destination="LIS",
        departure_date=date(2026, 10, 10),
    )
    gateway = ProviderGateway(
        flight_inventory=_FakeFlightInventoryProvider(result=success_result)
    )

    gateway.search_flights(_flight_request())

    matching = _records_with(capture, stage="flights", status="success")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "INFO"
    _assert_no_forbidden_fields(record)


# ---------------------------------------------------------------------------
# Non-success -> warning, with a safe error_code
# ---------------------------------------------------------------------------


def test_get_route_not_connected_logs_warning_with_error_code(capture: _CaptureHandler) -> None:
    """Default gateway construction uses the not_connected routing
    provider -- confirms an honest non-success result is logged as a
    warning, never disguised as a success."""
    gateway = ProviderGateway()

    result = gateway.get_route(_route_request())

    assert result.status == ProviderStatus.NOT_CONNECTED
    matching = _records_with(capture, stage="routing", status="not_connected")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    assert record.error_code == "PROVIDER_NOT_CONNECTED"
    _assert_no_forbidden_fields(record)


def test_search_flights_unavailable_logs_warning_with_error_code(
    capture: _CaptureHandler,
) -> None:
    gateway = ProviderGateway(flight_inventory=_FakeFlightInventoryProvider())

    result = gateway.search_flights(_flight_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    matching = _records_with(capture, stage="flights", status="unavailable")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    assert record.error_code == "DATA_UNAVAILABLE"
    _assert_no_forbidden_fields(record)


def test_search_accommodations_failed_logs_warning(capture: _CaptureHandler) -> None:
    failed_result = AccommodationSearchResult(
        provider="fake_accommodation_inventory_provider",
        status=AccommodationSearchStatus.FAILED,
        offers=[],
        message="Simulated failure for test purposes.",
    )
    gateway = ProviderGateway(
        accommodation_inventory=_FakeAccommodationInventoryProvider(result=failed_result)
    )

    gateway.search_accommodations(_accommodation_request())

    matching = _records_with(capture, stage="accommodations", status="failed")
    assert len(matching) == 1
    record = matching[0]
    assert record.levelname == "WARNING"
    assert record.error_code == "PROVIDER_FAILED"
    _assert_no_forbidden_fields(record)


# ---------------------------------------------------------------------------
# No payload/request leakage, safe provider names, duration_ms
# ---------------------------------------------------------------------------


def test_gateway_logs_never_include_request_params(capture: _CaptureHandler) -> None:
    gateway = ProviderGateway(accommodation_inventory=_FakeAccommodationInventoryProvider())

    gateway.search_accommodations(_accommodation_request())

    matching = _records_with(capture, stage="accommodations")
    assert len(matching) == 1
    record = matching[0]
    rendered = json.dumps(
        {k: v for k, v in record.__dict__.items() if not k.startswith("_")}, default=str
    )
    assert "Lisbon" not in rendered
    assert "2026-10-10" not in rendered
    assert "adults" not in rendered.lower() or "2" not in rendered


def test_gateway_logs_never_include_offers_or_message(capture: _CaptureHandler) -> None:
    gateway = ProviderGateway(accommodation_inventory=_FakeAccommodationInventoryProvider())

    gateway.search_accommodations(_accommodation_request())

    matching = _records_with(capture, stage="accommodations")
    record = matching[0]
    assert not hasattr(record, "offers")
    # `record.message` is a *standard* LogRecord attribute the logging
    # module itself always sets (from `record.getMessage()`) -- distinct
    # from the AccommodationSearchResult's own `.message` field value,
    # which is what must never leak. Confirmed below via the rendered
    # JSON, not via hasattr (which would always be True for `message`
    # regardless of anything this module does).
    rendered = JsonFormatter().format(record)
    assert "Fake Property" not in rendered
    assert "Fake provider for test purposes only" not in rendered


def test_unsafe_provider_name_normalizes_to_unknown(capture: _CaptureHandler) -> None:
    class _UnsafeNamedProvider(RoutingProvider):
        provider_name = "unsafe name\nwith control chars"

        def get_route(self, request: RouteRequest) -> RouteResult:
            return RouteResult(
                provider=self.provider_name,
                status=ProviderStatus.SUCCESS,
                distance_meters=1.0,
                duration_seconds=1.0,
                geometry=None,
                source=self.provider_name,
                confidence=1.0,
                message="ok",
            )

    gateway = ProviderGateway(routing=_UnsafeNamedProvider())

    gateway.get_route(_route_request())

    matching = _records_with(capture, stage="routing")
    assert len(matching) == 1
    record = matching[0]
    assert record.provider == "unknown"
    assert "unsafe name" not in JsonFormatter().format(record)


def test_duration_ms_is_numeric_and_non_negative(capture: _CaptureHandler) -> None:
    gateway = ProviderGateway(routing=_FakeRoutingProvider())

    gateway.get_route(_route_request())

    matching = _records_with(capture, stage="routing")
    record = matching[0]
    assert isinstance(record.duration_ms, (int, float))
    assert record.duration_ms >= 0


def test_gateway_call_outside_request_context_has_no_request_id(
    capture: _CaptureHandler,
) -> None:
    gateway = ProviderGateway(routing=_FakeRoutingProvider())

    gateway.get_route(_route_request())

    matching = _records_with(capture, stage="routing")
    record = matching[0]
    assert not hasattr(record, "request_id")


def test_gateway_call_does_not_change_return_value(capture: _CaptureHandler) -> None:
    """Confirms logging is purely observational -- the exact same
    RouteResult object semantics as before this step."""
    fake_result = RouteResult(
        provider="fake_routing_provider",
        status=ProviderStatus.SUCCESS,
        distance_meters=1234.5,
        duration_seconds=99.0,
        geometry=None,
        source="fake_routing_provider",
        confidence=0.5,
        message="distinctive test message",
    )
    gateway = ProviderGateway(routing=_FakeRoutingProvider(result=fake_result))

    result = gateway.get_route(_route_request())

    assert result is fake_result
    assert result.message == "distinctive test message"


def test_gateway_exception_propagates_unlogged_by_gateway(capture: _CaptureHandler) -> None:
    """No exception boundary exists in the gateway's dispatch methods
    before or after Step 187F -- a raising provider must still raise,
    completely unchanged, and the gateway itself logs nothing for it."""

    class _RaisingRoutingProvider(RoutingProvider):
        provider_name = "raising_routing_provider"

        def get_route(self, request: RouteRequest) -> RouteResult:
            raise RuntimeError("simulated provider failure")

    gateway = ProviderGateway(routing=_RaisingRoutingProvider())

    with pytest.raises(RuntimeError, match="simulated provider failure"):
        gateway.get_route(_route_request())

    assert _records_with(capture, stage="routing") == []
