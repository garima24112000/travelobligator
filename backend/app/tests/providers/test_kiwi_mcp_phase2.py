"""M2 Phase 2 (Workstream A): the Kiwi MCP flight flow, offline.

Covers what the audit found: place names sent as the trip's free text
("Jersey City, NJ" -> "Orlando, FL" returned zero offers, bare "London" ->
"Paris" returned offers), prices labelled with the TRIP's currency when Kiwi
stated none, no total time limit, discovery repeated on every search, and
failures without a kind. Fixtures: tests/fixtures/kiwi_mcp_search_flight.json.
No MCP connection is ever made."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.models.flight import FlightAvailabilityReason as Reason
from app.models.flight import FlightSearchRequest, FlightSearchStatus
from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripRequest
from app.providers.flights import KiwiMcpFlightProvider
from app.providers.flights import kiwi_mcp_adapter as adapter_module
from app.providers.flights.kiwi_mcp_adapter import build_search_flight_arguments, place_query
from app.providers.flights.kiwi_mcp_client import (
    FAILURE_CONNECTION,
    FAILURE_TIMEOUT,
    KiwiMcpClient,
    KiwiMcpDiscoveryStatus,
    KiwiMcpToolCallResult,
    KiwiMcpToolCallStatus,
    KiwiMcpToolDiscoveryResult,
    McpToolInfo,
)
from app.providers.flights.kiwi_mcp_parser import parse_kiwi_mcp_search_result
from app.services.flight_inventory_service import FlightInventoryService

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "kiwi_mcp_search_flight.json").read_text(encoding="utf-8")
)
_SCHEMA = {
    "type": "object",
    "properties": {
        "flyFrom": {"description": "Origin: IATA code, city or airport name", "type": "string"},
        "flyTo": {"description": "Destination: IATA code, city or airport name", "type": "string"},
        "departureDate": {"description": "dd/mm/yyyy", "type": "string"},
        "returnDate": {"type": "string"},
        "adults": {"type": "integer"},
        "children": {"type": "integer"},
        "currency": {"type": "string"},
    },
}
_FUTURE = date.today() + timedelta(days=60)


def _request(**overrides: Any) -> FlightSearchRequest:
    fields: dict[str, Any] = {"origin": "London", "destination": "Paris", "departure_date": _FUTURE, "currency": "USD"}
    fields.update(overrides)
    return FlightSearchRequest(**fields)


def _structured(payload: Any) -> KiwiMcpToolCallResult:
    return KiwiMcpToolCallResult(status=KiwiMcpToolCallStatus.SUCCESS, structured_content=payload)


# -- search inputs (the zero-offer investigation) -----------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Orlando, FL", "Orlando"), ("Jersey City, NJ", "Jersey City"), ("London", "London"),
     ("  Lisbon , Portugal", "Lisbon"), ("", None), (None, None), (", Portugal", None)],
)
def test_place_query_sends_the_city_part(text: str | None, expected: str | None) -> None:
    assert place_query(text) == expected


def test_the_recorded_zero_offer_inputs_are_sent_as_city_names() -> None:
    arguments, error = build_search_flight_arguments(
        _request(origin="Jersey City, NJ", destination="Orlando, FL"), _SCHEMA
    )
    assert error is None
    assert arguments["flyFrom"] == "Jersey City" and arguments["flyTo"] == "Orlando"


def test_the_geocoder_resolved_city_is_preferred_for_the_destination() -> None:
    arguments, _ = build_search_flight_arguments(
        _request(destination="Lisbon, Portugal", destination_city="Lisboa"), _SCHEMA
    )
    assert arguments["flyTo"] == "Lisboa"


def test_a_past_departure_is_not_searched() -> None:
    arguments, error = build_search_flight_arguments(_request(departure_date=date.today() - timedelta(days=1)), _SCHEMA)
    assert arguments is None and "past" in error


def test_undeclared_fields_are_never_sent() -> None:
    schema = {"properties": {key: _SCHEMA["properties"][key] for key in ("flyFrom", "flyTo", "departureDate")}}
    arguments, error = build_search_flight_arguments(_request(), schema)
    assert error is None
    assert set(arguments) == {"flyFrom", "flyTo", "departureDate"}


def test_the_service_passes_the_resolved_city() -> None:
    captured: list[FlightSearchRequest] = []

    class _Gateway:
        class flight_inventory:  # noqa: N801
            provider_name = "fake"

        def search_flights(self, request: FlightSearchRequest) -> Any:
            captured.append(request)
            raise RuntimeError("stop here")

    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Orlando, FL", origin_city="Jersey City, NJ", start_date=_FUTURE,
            end_date=_FUTURE + timedelta(days=3), travelers_count=2, travel_group_type=TravelGroupType.COUPLE,
        )
    )
    state.destination_context = DestinationContext(
        destination_name="Orlando, FL", resolved_destination={"display_name": "Orlando, Florida", "city": "Orlando"}
    )
    FlightInventoryService(gateway=_Gateway()).build_report(state)  # type: ignore[arg-type]
    assert captured[0].destination_city == "Orlando"
    assert captured[0].destination == "Orlando, FL"  # the trip's own text is kept on the request


# -- currency: never the trip's ---------------------------------------------------------------


def test_offers_keep_the_currency_kiwi_stated() -> None:
    result = parse_kiwi_mcp_search_result(_structured(FIXTURES["success_with_currency"]), _request(currency="USD"))
    assert result.status == FlightSearchStatus.SUCCESS and result.availability_reason == Reason.OFFERS_FOUND
    assert [offer.currency for offer in result.offers] == ["EUR", "EUR"]
    assert result.offers[0].total_price_amount == Decimal("53.0")
    assert result.currency == "EUR"


def test_a_price_without_any_stated_currency_is_dropped_never_relabelled() -> None:
    result = parse_kiwi_mcp_search_result(
        _structured(FIXTURES["success_without_payload_currency"]), _request(currency="USD")
    )
    labelled, unlabelled = result.offers
    # the itinerary's own label names its currency
    assert labelled.currency == "GBP" and labelled.total_price_amount == Decimal("70.0")
    # nothing names a currency: the offer stays, its price does not
    assert unlabelled.total_price_amount is None and unlabelled.currency is None
    assert "USD" not in {offer.currency for offer in result.offers}
    assert result.currency is None


def test_a_lower_case_payload_currency_is_normalised() -> None:
    payload = {**FIXTURES["success_with_currency"], "currency": "eur"}
    result = parse_kiwi_mcp_search_result(_structured(payload), _request())
    assert {offer.currency for offer in result.offers} == {"EUR"}


# -- outcomes ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call_result", "status", "reason", "failure_kind"),
    [
        (_structured(FIXTURES["empty"]), FlightSearchStatus.UNAVAILABLE, Reason.SEARCH_COMPLETED_NO_OFFERS, None),
        (_structured(FIXTURES["provider_error"]), FlightSearchStatus.FAILED, Reason.PROVIDER_FAILED, "tool_error"),
        (_structured(FIXTURES["malformed"]), FlightSearchStatus.FAILED, Reason.RESPONSE_UNUSABLE, None),
        (KiwiMcpToolCallResult(status=KiwiMcpToolCallStatus.FAILED, failure_kind=FAILURE_TIMEOUT, message="t"),
         FlightSearchStatus.FAILED, Reason.PROVIDER_FAILED, FAILURE_TIMEOUT),
        (KiwiMcpToolCallResult(status=KiwiMcpToolCallStatus.FAILED, failure_kind=FAILURE_CONNECTION, message="c"),
         FlightSearchStatus.FAILED, Reason.PROVIDER_FAILED, FAILURE_CONNECTION),
    ],
    ids=["empty", "provider-error", "malformed", "timeout", "connection"],
)
def test_each_outcome_is_told_apart(call_result: Any, status: Any, reason: Reason, failure_kind: str | None) -> None:
    result = parse_kiwi_mcp_search_result(call_result, _request())
    assert result.status == status
    assert result.availability_reason == reason
    assert result.failure_kind == failure_kind
    assert result.offers == []


# -- the client: one total time limit, cleanup ----------------------------------------------


class _SlowSession:
    def __init__(self, delay: float, exits: list[str]) -> None:
        self.delay = delay
        self.exits = exits

    async def __aenter__(self) -> "_SlowSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.exits.append("closed")

    async def list_tools(self) -> Any:
        await asyncio.sleep(self.delay)
        return type("R", (), {"tools": []})()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(self.delay)
        return type("R", (), {"structured_content": {}, "is_error": False})()


def test_discovery_stops_at_the_total_limit_and_closes_the_session() -> None:
    exits: list[str] = []
    client = KiwiMcpClient("https://mcp.invalid", 10.0, client_factory=lambda: _SlowSession(5.0, exits),
                           total_timeout_seconds=0.05)
    result = client.discover_tools()
    assert result.status == KiwiMcpDiscoveryStatus.FAILED and result.failure_kind == FAILURE_TIMEOUT
    assert exits == ["closed"]


def test_the_limit_covers_discovery_and_the_call_together() -> None:
    exits: list[str] = []
    ticks = iter([0.0, 0.0, 10.0, 10.0, 10.0])  # the limit is used up after discovery
    client = KiwiMcpClient("https://mcp.invalid", 10.0, client_factory=lambda: _SlowSession(0.0, exits),
                           total_timeout_seconds=5.0, clock=lambda: next(ticks))
    assert client.discover_tools().status == KiwiMcpDiscoveryStatus.SUCCESS
    called = client.call_tool("search-flight", {})
    assert called.status == KiwiMcpToolCallStatus.FAILED and called.failure_kind == FAILURE_TIMEOUT
    assert exits == ["closed"]  # the call never opened a second session


def test_a_connection_error_is_labelled() -> None:
    class _Broken:
        async def __aenter__(self) -> Any:
            raise ConnectionError("refused")

        async def __aexit__(self, *exc: object) -> None:
            return None

    client = KiwiMcpClient("https://mcp.invalid", 10.0, client_factory=_Broken, total_timeout_seconds=5.0)
    assert client.discover_tools().failure_kind == FAILURE_CONNECTION


# -- discovery cache (real-client path only) ----------------------------------------------------


@dataclass
class _CountingClient:
    discovery: KiwiMcpToolDiscoveryResult
    discover_calls: int = 0
    calls: list[str] = field(default_factory=list)

    def discover_tools(self) -> KiwiMcpToolDiscoveryResult:
        self.discover_calls += 1
        return self.discovery

    def call_tool(self, name: str, arguments: dict[str, Any]) -> KiwiMcpToolCallResult:
        self.calls.append(name)
        return _structured(FIXTURES["success_with_currency"])


@pytest.fixture()
def real_client_path(monkeypatch: pytest.MonkeyPatch) -> _CountingClient:
    """The provider builds its own client (no injection) -- the cacheable path --
    but the client it builds is a counting fake."""
    adapter_module.clear_discovery_cache()
    counting = _CountingClient(
        KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS, tools=[McpToolInfo(name="search-flight", input_schema=_SCHEMA)]
        )
    )
    monkeypatch.setattr(adapter_module, "KiwiMcpClient", lambda **kwargs: counting)
    yield counting
    adapter_module.clear_discovery_cache()


def _enable(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    settings = Settings(_env_file=None, kiwi_mcp_enabled=True, **overrides)
    monkeypatch.setattr(adapter_module, "get_settings", lambda: settings)


def test_a_successful_discovery_is_reused(monkeypatch: pytest.MonkeyPatch, real_client_path: _CountingClient) -> None:
    _enable(monkeypatch)
    provider = KiwiMcpFlightProvider()
    first = provider.search_flights(_request())
    second = provider.search_flights(_request())
    assert first.availability_reason == second.availability_reason == Reason.OFFERS_FOUND
    assert real_client_path.discover_calls == 1  # tool schemas, never flight data
    assert real_client_path.calls == ["search-flight", "search-flight"]  # every search asks Kiwi


def test_the_cache_can_be_switched_off(monkeypatch: pytest.MonkeyPatch, real_client_path: _CountingClient) -> None:
    _enable(monkeypatch, kiwi_mcp_discovery_cache_seconds=0)
    provider = KiwiMcpFlightProvider()
    provider.search_flights(_request())
    provider.search_flights(_request())
    assert real_client_path.discover_calls == 2


def test_a_failed_discovery_is_never_cached(monkeypatch: pytest.MonkeyPatch, real_client_path: _CountingClient) -> None:
    _enable(monkeypatch)
    real_client_path.discovery = KiwiMcpToolDiscoveryResult(
        status=KiwiMcpDiscoveryStatus.FAILED, message="down", failure_kind=FAILURE_TIMEOUT
    )
    provider = KiwiMcpFlightProvider()
    result = provider.search_flights(_request())
    assert result.availability_reason == Reason.PROVIDER_FAILED and result.failure_kind == FAILURE_TIMEOUT
    provider.search_flights(_request())
    assert real_client_path.discover_calls == 2


def test_the_result_names_what_was_searched(monkeypatch: pytest.MonkeyPatch, real_client_path: _CountingClient) -> None:
    _enable(monkeypatch)
    result = KiwiMcpFlightProvider().search_flights(_request(origin="Jersey City, NJ", destination="Orlando, FL"))
    assert (result.origin, result.destination) == ("Jersey City", "Orlando")


def test_kiwi_stays_off_by_default() -> None:
    assert Settings(_env_file=None).kiwi_mcp_enabled is False
    assert Settings(_env_file=None).flight_provider != "kiwi_mcp"
