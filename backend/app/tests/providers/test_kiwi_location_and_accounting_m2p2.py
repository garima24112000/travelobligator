"""M2 Phase 2 Kiwi remediation, offline: location resolution and request
accounting.

What the bounded live run showed: London -> Porto returned 15 offers, all
priced in the USD Kiwi stated; "Jersey City" -> "Orlando" returned zero offers
with no error; and the verification counted 0 HTTP requests although Kiwi
answered. The causes these tests pin down:

  * Kiwi's `search-flight` takes a place NAME or an airport code and never
    says which airports it matched to a name, so zero offers for a name is
    not proof that no flight exists (`location_unconfirmed`);
  * a place with no airport of its own needs a NEARBY airport, found by a
    lookup provider with provenance -- never hardcoded;
  * the MCP SDK sends over `httpx2`, which an `httpx` guard never sees, so the
    Kiwi client now counts and caps its own transport.

No network: the MCP tests run the real MCP SDK against an in-process server.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core import performance
from app.core.config import Settings
from app.models.flight import FlightAvailabilityReason as Reason
from app.models.flight import FlightSearchRequest, FlightSearchResult, FlightSearchStatus
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.flights import airport_lookup
from app.providers.flights import kiwi_mcp_adapter as adapter_module
from app.providers.flights.airport_lookup import (
    AirportLookupResult,
    NearbyAirport,
    OurAirportsLookup,
    straight_line_km,
)
from app.providers.flights.kiwi_mcp_adapter import KiwiMcpFlightProvider, is_airport_code
from app.providers.flights.kiwi_mcp_client import (
    FAILURE_REQUEST_CAP,
    FAILURE_TIMEOUT,
    KiwiMcpClient,
    KiwiMcpDiscoveryStatus,
    KiwiMcpToolCallResult,
    KiwiMcpToolCallStatus,
    KiwiMcpToolDiscoveryResult,
    McpToolInfo,
)
from app.providers.geocoding.base import GeocodeHit
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
        "adults": {"type": "integer"},
        "currency": {"type": "string"},
    },
}
_FUTURE = date.today() + timedelta(days=45)


@pytest.fixture(autouse=True)
def _kiwi_on(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter_module.clear_discovery_cache()
    monkeypatch.setattr(adapter_module, "get_settings", lambda: Settings(_env_file=None, kiwi_mcp_enabled=True))
    yield
    adapter_module.clear_discovery_cache()


# -- a scripted Kiwi: answers by what it is asked ------------------------------------------


@dataclass
class _ScriptedKiwi:
    """Returns `answers[(flyFrom, flyTo)]`, else the empty payload (what the
    live run saw for a name Kiwi did not match). Records every search."""

    answers: dict[tuple[str, str], Any] = field(default_factory=dict)
    searches: list[dict[str, Any]] = field(default_factory=list)
    http_per_exchange: int | None = 3

    def discover_tools(self) -> KiwiMcpToolDiscoveryResult:
        return KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS,
            tools=[McpToolInfo(name="search-flight", input_schema=_SCHEMA)],
            http_requests=self.http_per_exchange,
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> KiwiMcpToolCallResult:
        self.searches.append(dict(arguments))
        payload = self.answers.get((arguments["flyFrom"], arguments["flyTo"]), FIXTURES["empty"])
        return KiwiMcpToolCallResult(
            status=KiwiMcpToolCallStatus.SUCCESS, structured_content=payload, http_requests=self.http_per_exchange
        )


class _Lookup:
    """A stand-in airport-lookup provider: a fixed answer per place text."""

    provider_name = "fake"

    def __init__(self, answers: dict[str, AirportLookupResult]) -> None:
        self.answers = answers
        self.asked: list[str] = []
        self.bound_to_context: Any = None

    def bound_to(self, provider_context: Any) -> "_Lookup":
        self.bound_to_context = provider_context
        return self

    def nearby_airports(self, place_text: str) -> AirportLookupResult:
        self.asked.append(place_text)
        return self.answers.get(place_text, AirportLookupResult(status=airport_lookup.STATUS_NOT_CONNECTED))


def _found(name: str, km: float, code: str | None) -> AirportLookupResult:
    return AirportLookupResult(
        status=airport_lookup.STATUS_FOUND, radius_km=60.0, source=airport_lookup.LOOKUP_SOURCE, requests=2,
        airports=(NearbyAirport(name=name, straight_line_km=km, provider_place_id=f"ourairports/{name}",
                                iata_code=code, airport_type="large_airport",
                                commercial_service_evidence=airport_lookup.EVIDENCE_SCHEDULED_SERVICE,
                                rationale="test rationale"),),
    )


class _Gateway:
    def __init__(self, kiwi: _ScriptedKiwi, lookup: _Lookup) -> None:
        self.flight_inventory = KiwiMcpFlightProvider(client=kiwi)
        self.lookup = lookup

    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResult:
        return self.flight_inventory.search_flights(request)

    def nearby_airports(self, place_text: str, provider_context: Any = None) -> AirportLookupResult:
        provider = self.lookup.bound_to(provider_context) if provider_context is not None else self.lookup
        return provider.nearby_airports(place_text)


def _state(origin: str, destination: str, currency: str = "USD") -> PlanningState:
    return PlanningState(
        trip_id="trip-loc",
        trip_request=TripRequest(
            primary_destination=destination, origin_city=origin, start_date=_FUTURE, end_date=_FUTURE,
            travelers_count=1, travel_group_type=TravelGroupType.SOLO, budget_currency=currency,
        ),
    )


def _report(kiwi: _ScriptedKiwi, lookup: _Lookup, origin: str, destination: str, **kwargs: Any) -> FlightSearchResult:
    return FlightInventoryService(gateway=_Gateway(kiwi, lookup)).build_report(_state(origin, destination), **kwargs)


# -- the two live routes -------------------------------------------------------------------


def test_control_route_keeps_kiwis_stated_currency_and_records_what_was_searched() -> None:
    kiwi = _ScriptedKiwi(answers={("London", "Porto"): FIXTURES["success_with_currency"]})
    lookup = _Lookup({})
    result = _report(kiwi, lookup, "London", "Porto")
    assert result.availability_reason == Reason.OFFERS_FOUND
    stated = FIXTURES["success_with_currency"]["currency"]
    assert stated != "USD"  # the trip's currency is USD: a wrong label would show here
    assert {offer.currency for offer in result.offers} == {stated}
    assert [(e.role, e.method, e.searched) for e in result.location_resolution] == [
        ("origin", "place_name", "London"), ("destination", "place_name", "Porto"),
    ]
    assert result.search_attempts == 1 and lookup.asked == []  # no lookup when the name search worked


def test_zero_offers_for_place_names_is_not_reported_as_no_flights() -> None:
    kiwi = _ScriptedKiwi()  # "Jersey City" -> "Orlando": empty, no error (the live answer)
    result = _report(kiwi, _Lookup({}), "Jersey City, NJ", "Orlando, FL")
    assert result.availability_reason == Reason.LOCATION_UNCONFIRMED
    assert result.availability_reason != Reason.SEARCH_COMPLETED_NO_OFFERS
    assert "does not say which airports" in result.message
    assert [s["flyFrom"] for s in kiwi.searches] == ["Jersey City"]  # no second search without an airport
    assert any("could not be looked up (not_connected)" in w for w in result.warnings)


def test_nearby_airports_are_searched_with_provenance_when_the_names_found_nothing() -> None:
    kiwi = _ScriptedKiwi(answers={("EWR", "MCO"): FIXTURES["success_with_currency"]})
    lookup = _Lookup({
        "Jersey City, NJ": _found("Newark Liberty International Airport", 12.4, "EWR"),
        "Orlando, FL": _found("Orlando International Airport", 14.9, "MCO"),
    })
    result = _report(kiwi, lookup, "Jersey City, NJ", "Orlando, FL")
    assert [(s["flyFrom"], s["flyTo"]) for s in kiwi.searches] == [("Jersey City", "Orlando"), ("EWR", "MCO")]
    assert result.availability_reason == Reason.OFFERS_FOUND and result.offers
    assert result.search_attempts == 2
    origin = next(e for e in result.location_resolution if e.role == "origin")
    assert origin.method == "nearby_airport" and origin.requested == "Jersey City, NJ"
    assert origin.airport_code == "EWR" and origin.straight_line_km == 12.4
    assert origin.lookup_source == airport_lookup.LOOKUP_SOURCE
    assert "straight line" in origin.note and "not in the place itself" in origin.note


def test_an_airport_without_a_code_is_not_substituted() -> None:
    kiwi = _ScriptedKiwi()
    lookup = _Lookup({"Jersey City, NJ": _found("Some International Airport", 20.0, None)})
    result = _report(kiwi, lookup, "Jersey City, NJ", "MCO")
    assert [s["flyFrom"] for s in kiwi.searches] == ["Jersey City"]
    assert result.availability_reason == Reason.LOCATION_UNCONFIRMED


def test_a_verified_airport_on_one_side_and_a_name_on_the_other_stays_unconfirmed() -> None:
    kiwi = _ScriptedKiwi()
    lookup = _Lookup({"Jersey City, NJ": _found("Newark Liberty International Airport", 12.4, "EWR")})
    result = _report(kiwi, lookup, "Jersey City, NJ", "Orlando, FL")  # Orlando: lookup not connected
    assert [(s["flyFrom"], s["flyTo"]) for s in kiwi.searches] == [("Jersey City", "Orlando"), ("EWR", "Orlando")]
    assert result.availability_reason == Reason.LOCATION_UNCONFIRMED
    assert "scheduled airline service was searched as well" in result.message


def test_codes_on_both_sides_with_zero_offers_is_a_true_empty_search() -> None:
    kiwi = _ScriptedKiwi()
    lookup = _Lookup({
        "Jersey City, NJ": _found("Newark Liberty International Airport", 12.4, "EWR"),
        "Orlando, FL": _found("Orlando International Airport", 14.9, "MCO"),
    })
    result = _report(kiwi, lookup, "Jersey City, NJ", "Orlando, FL")
    assert result.availability_reason == Reason.SEARCH_COMPLETED_NO_OFFERS
    direct = _report(_ScriptedKiwi(), _Lookup({}), "JFK", "MCO")
    assert direct.availability_reason == Reason.SEARCH_COMPLETED_NO_OFFERS


@pytest.mark.parametrize(
    ("status", "words"),
    [(airport_lookup.STATUS_NONE_NEARBY, "no airport with scheduled airline service was found within 60 km"),
     (airport_lookup.STATUS_PLACE_NOT_FOUND, "could not be found on the map")],
)
def test_a_place_without_a_searchable_airport_is_said_so(status: str, words: str) -> None:
    kiwi = _ScriptedKiwi()
    lookup = _Lookup({"Jersey City, NJ": AirportLookupResult(status=status, radius_km=60.0)})
    result = _report(kiwi, lookup, "Jersey City, NJ", "Orlando, FL")
    assert result.availability_reason == Reason.NO_SEARCHABLE_AIRPORT
    assert words in result.message and len(kiwi.searches) == 1
    origin = next(e for e in result.location_resolution if e.role == "origin")
    assert origin.method == "not_resolved" and origin.searched is None


def test_the_lookup_is_charged_to_the_generations_context() -> None:
    lookup = _Lookup({})
    context = object()
    _report(_ScriptedKiwi(), lookup, "Jersey City, NJ", "Orlando, FL", provider_context=context)
    assert lookup.bound_to_context is context and lookup.asked == ["Jersey City, NJ", "Orlando, FL"]


# -- location ambiguity --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [("LHR", True), ("MCO", True), ("lhr", False), ("Ufa", False), ("Bar", False), ("Rome", False),
     ("JFK ", True), ("", False), (None, False)],
)
def test_only_an_all_capitals_three_letter_entry_is_an_airport_code(value: str | None, code: bool) -> None:
    assert is_airport_code(value) is code


def test_a_short_place_name_is_searched_as_a_name_not_as_a_code() -> None:
    kiwi = _ScriptedKiwi()
    result = _report(kiwi, _Lookup({}), "Ufa", "Orlando, FL")
    origin = next(e for e in result.location_resolution if e.role == "origin")
    assert origin.method == "place_name" and origin.airport_code is None
    assert result.availability_reason == Reason.LOCATION_UNCONFIRMED


class _Geocoder:
    def __init__(self, hits: dict[str, GeocodeHit | None]) -> None:
        self.hits = hits
        self.queries: list[str] = []
        self.bound: Any = None

    def bound_to(self, context: Any) -> "_Geocoder":
        self.bound = context
        return self

    def search_destination(self, client: Any, query: str) -> GeocodeHit | None:
        self.queries.append(query)
        return self.hits.get(query)


def _place(lat: float, lon: float, feature_class: str = "place") -> GeocodeHit:
    return GeocodeHit(lat=lat, lon=lon, name="x", display_name="x", source="test",
                      provider_place_id="test/x", feature_class=feature_class)


# Geocoded centres used below (illustrative; the fixture rows are in OurAirports format).
_JERSEY_CITY = _place(40.7178, -74.0431)
_ORLANDO = _place(28.5384, -81.3789)
_NEAR_SANFORD = _place(28.7500, -81.2500)
_ISLAND = _place(10.0000, 20.0000)
_AIRPORTS_CSV = Path(__file__).resolve().parents[1] / "fixtures" / "ourairports_sample.csv"


def _ourairports(hits: dict[str, GeocodeHit | None], path: Any = _AIRPORTS_CSV, radius: float = 60.0) -> Any:
    airport_lookup.clear_dataset_cache()
    lookup = OurAirportsLookup(geocoder=_Geocoder(hits), data_path=str(path) if path else None)
    lookup._radius_km = radius
    return lookup


def test_a_seaplane_base_heliport_or_airport_without_scheduled_service_is_never_chosen() -> None:
    """The live run: the nearest 'international airport' features were a
    seaplane base (NYS) and an executive airport (ORL)."""
    result = _ourairports({"Jersey City, NJ": _JERSEY_CITY}).nearby_airports("Jersey City, NJ")
    codes = [a.iata_code for a in result.airports]
    assert codes[0] == "EWR" and set(codes) <= {"EWR", "LGA"}
    assert not {"NYS", "JRB", "TEB"} & set(codes)  # seaplane base, heliport, no scheduled service
    assert result.excluded == {"no_scheduled_service": 1}  # TEB (NYS/JRB are never loaded)
    orlando = _ourairports({"Orlando, FL": _ORLANDO}).nearby_airports("Orlando, FL")
    assert [a.iata_code for a in orlando.airports] == ["MCO", "SFB"]  # not ORL (no service), not the closed field


def test_selection_is_not_simply_the_nearest_airport() -> None:
    result = _ourairports({"p": _NEAR_SANFORD}).nearby_airports("p")
    first, second = result.airports[0], result.airports[1]
    assert first.iata_code == "MCO" and second.iata_code == "SFB"
    assert first.straight_line_km > second.straight_line_km  # the larger service class wins over the nearer one
    assert "larger service classes are preferred" in first.rationale


def test_every_selected_airport_carries_its_evidence_and_provenance() -> None:
    airport = _ourairports({"Jersey City, NJ": _JERSEY_CITY}).nearby_airports("Jersey City, NJ").airports[0]
    assert airport.commercial_service_evidence == airport_lookup.EVIDENCE_SCHEDULED_SERVICE
    assert airport.airport_type == "large_airport" and airport.country == "US"
    assert (airport.lat, airport.lon) == (40.6925, -74.1687)
    assert airport.provider_place_id == "ourairports/T002"
    assert airport.straight_line_km == round(straight_line_km(40.7178, -74.0431, 40.6925, -74.1687), 1)


def test_a_small_airport_is_chosen_only_with_scheduled_service() -> None:
    result = _ourairports({"i": _ISLAND}).nearby_airports("i")
    assert [a.iata_code for a in result.airports] == ["ISL"]  # RGN (medium, no service) is left out
    assert result.airports[0].airport_type == "small_airport"


@pytest.mark.parametrize(("path", "kind"), [(None, "airport_data_not_configured"),
                                             ("/nonexistent/airports.csv", "airport_data_missing")])
def test_without_the_data_set_nothing_is_looked_up_or_claimed(path: Any, kind: str) -> None:
    lookup = _ourairports({"Jersey City, NJ": _JERSEY_CITY}, path=path)
    result = lookup.nearby_airports("Jersey City, NJ")
    assert result.status == airport_lookup.STATUS_NOT_CONNECTED and result.failure_kind == kind
    assert lookup._geocoder.queries == []  # no geocoder request without the evidence


def test_a_malformed_data_set_is_not_connected(tmp_path: Path) -> None:
    bad = tmp_path / "airports.csv"
    bad.write_text("id,name\n1,x\n", encoding="utf-8")
    result = _ourairports({"p": _ORLANDO}, path=bad).nearby_airports("p")
    assert result.status == airport_lookup.STATUS_NOT_CONNECTED and result.failure_kind == "airport_data_malformed"


def test_a_place_that_is_not_a_settlement_or_has_no_airport_in_range() -> None:
    lookup = _ourairports({"p": _place(40.7, -74.0, feature_class="amenity")})
    assert lookup.nearby_airports("p").status == airport_lookup.STATUS_PLACE_NOT_FOUND
    far = _ourairports({"p": _place(0.0, 0.0)}).nearby_airports("p")
    assert far.status == airport_lookup.STATUS_NONE_NEARBY and far.requests == 1


def test_the_lookup_makes_one_geocoder_request_charged_to_the_context() -> None:
    lookup = _ourairports({"Jersey City, NJ": _JERSEY_CITY})
    context = object()
    bound = lookup.bound_to(context)
    assert bound.nearby_airports("Jersey City, NJ").requests == 1
    assert bound._geocoder.bound is context


def test_the_default_lookup_is_not_connected_and_sends_nothing() -> None:
    assert Settings(_env_file=None).flight_airport_lookup_provider == "not_connected"
    provider = airport_lookup.get_airport_lookup_provider("not_connected")
    assert provider.nearby_airports("Anywhere").status == airport_lookup.STATUS_NOT_CONNECTED
    with pytest.raises(ValueError):
        Settings(_env_file=None, FLIGHT_AIRPORT_LOOKUP_PROVIDER="geoapify")  # the category path is gone


def test_an_airport_without_commercial_evidence_is_never_searched_or_called_empty() -> None:
    """Even with an IATA code (as the live NYS/ORL pair had), an airport
    without scheduled-service evidence is not substituted; the name search's
    empty answer stays unconfirmed."""
    unverified = AirportLookupResult(
        status=airport_lookup.STATUS_FOUND, radius_km=60.0, source="other",
        airports=(NearbyAirport(name="Some Seaplane Base", straight_line_km=6.5, provider_place_id="x/1",
                                iata_code="NYS", commercial_service_evidence=None),),
    )
    kiwi = _ScriptedKiwi()
    result = _report(kiwi, _Lookup({"Jersey City, NJ": unverified, "Orlando, FL": unverified}),
                     "Jersey City, NJ", "Orlando, FL")
    assert [s["flyFrom"] for s in kiwi.searches] == ["Jersey City"]
    assert result.availability_reason == Reason.LOCATION_UNCONFIRMED


def test_no_offers_needs_commercial_evidence_on_every_substituted_side() -> None:
    from app.models.flight import FlightLocationResolution
    from app.services.flight_inventory_service import _has_commercial_evidence

    code = FlightLocationResolution(role="origin", requested="X", searched="ABC", method="airport_code")
    verified = FlightLocationResolution(role="origin", requested="X", searched="ABC", method="nearby_airport",
                                        commercial_service_evidence="ourairports:scheduled_service=yes")
    unverified = verified.model_copy(update={"commercial_service_evidence": None})
    assert _has_commercial_evidence(code) and _has_commercial_evidence(verified)
    assert not _has_commercial_evidence(unverified)


def test_end_to_end_the_commercial_airports_are_searched_and_offers_come_back() -> None:
    lookup = _ourairports({"Jersey City, NJ": _JERSEY_CITY, "Orlando, FL": _ORLANDO})
    kiwi = _ScriptedKiwi(answers={("EWR", "MCO"): FIXTURES["success_with_currency"]})
    result = _report(kiwi, lookup, "Jersey City, NJ", "Orlando, FL")
    assert [(s["flyFrom"], s["flyTo"]) for s in kiwi.searches] == [("Jersey City", "Orlando"), ("EWR", "MCO")]
    assert result.availability_reason == Reason.OFFERS_FOUND and result.offers
    sides = {e.role: e for e in result.location_resolution}
    assert sides["origin"].airport_code == "EWR" and sides["destination"].airport_code == "MCO"
    for entry in sides.values():
        assert entry.method == "nearby_airport" and entry.lookup_source == airport_lookup.LOOKUP_SOURCE
        assert entry.commercial_service_evidence == airport_lookup.EVIDENCE_SCHEDULED_SERVICE
        assert entry.airport_type == "large_airport" and entry.airport_lat is not None
        assert entry.selection_rationale and entry.dataset_version.startswith("sha256:")


def test_end_to_end_commercial_airports_with_no_offers_is_a_true_empty_search() -> None:
    lookup = _ourairports({"Jersey City, NJ": _JERSEY_CITY, "Orlando, FL": _ORLANDO})
    result = _report(_ScriptedKiwi(), lookup, "Jersey City, NJ", "Orlando, FL")
    assert result.availability_reason == Reason.SEARCH_COMPLETED_NO_OFFERS


# -- request accounting through the REAL MCP SDK (in-process server, no network) -----------


def _in_process_kiwi(payload: Any) -> Any:
    """A transport factory serving a real MCP server in-process: the client's
    counting transport wraps it exactly as it wraps the network transport."""
    import httpx2
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings

    def factory() -> Any:
        server = MCPServer("in-process-kiwi")

        @server.tool(name="search-flight", description="Search for flights")
        def search_flight(flyFrom: str, flyTo: str, departureDate: str) -> dict:
            return payload

        app = server.streamable_http_app(
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
        )

        class _Serve(httpx2.AsyncBaseTransport):
            def __init__(self) -> None:
                self._stack = contextlib.AsyncExitStack()
                self._inner = httpx2.ASGITransport(app=app)

            async def __aenter__(self) -> "_Serve":
                await self._stack.enter_async_context(server.session_manager.run())
                return self

            async def handle_async_request(self, request: Any) -> Any:
                return await self._inner.handle_async_request(request)

            async def aclose(self) -> None:
                await self._stack.aclose()

        return _Serve()

    return factory


def _real_client(payload: Any, cap: int = 8, total: float = 20.0) -> KiwiMcpClient:
    return KiwiMcpClient("http://127.0.0.1/mcp", 10.0, total_timeout_seconds=total,
                         max_http_requests_per_session=cap, http_transport_factory=_in_process_kiwi(payload))


def test_every_mcp_http_request_is_counted_with_its_status() -> None:
    client = _real_client(FIXTURES["empty"])
    discovery = client.discover_tools()
    call = client.call_tool("search-flight", {"flyFrom": "A", "flyTo": "B", "departureDate": "01/01/2030"})
    assert discovery.status == KiwiMcpDiscoveryStatus.SUCCESS and call.status == KiwiMcpToolCallStatus.SUCCESS
    assert discovery.http_requests and discovery.http_requests > 0
    assert call.http_requests and call.http_requests > 0
    assert client.http_requests_total == discovery.http_requests + call.http_requests
    assert sum(client.http_statuses.values()) == client.http_requests_total
    assert set(client.http_statuses) <= {200, 202, 204}


def test_a_session_beyond_its_request_cap_is_stopped_and_named() -> None:
    call = _real_client(FIXTURES["empty"], cap=1, total=5.0).call_tool(
        "search-flight", {"flyFrom": "A", "flyTo": "B", "departureDate": "01/01/2030"}
    )
    assert call.status == KiwiMcpToolCallStatus.FAILED
    assert call.failure_kind == FAILURE_REQUEST_CAP and call.http_requests == 1


def test_the_adapter_reports_the_requests_of_discovery_and_search(monkeypatch: pytest.MonkeyPatch) -> None:
    clients: list[KiwiMcpClient] = []

    def make(**kwargs: Any) -> KiwiMcpClient:
        assert kwargs["max_http_requests_per_session"] == 8
        client = _real_client(FIXTURES["empty"])
        clients.append(client)
        return client

    monkeypatch.setattr(adapter_module, "KiwiMcpClient", make)
    recorder = performance.PerformanceRecorder()
    token = performance._ACTIVE.set(recorder)
    try:
        first = KiwiMcpFlightProvider().search_flights(
            FlightSearchRequest(origin="LHR", destination="OPO", departure_date=_FUTURE, adults=1)
        )
        second = KiwiMcpFlightProvider().search_flights(
            FlightSearchRequest(origin="LHR", destination="OPO", departure_date=_FUTURE, adults=1)
        )
    finally:
        performance._ACTIVE.reset(token)
    assert first.provider_http_requests == clients[0].http_requests_total > 0
    # the second search reused the cached discovery: only the search was sent
    assert 0 < second.provider_http_requests == clients[1].http_requests_total < first.provider_http_requests
    counted = recorder.snapshot()["counts"]["kiwi_mcp_http_requests"]
    assert counted == first.provider_http_requests + second.provider_http_requests


def test_an_injected_client_claims_no_request_count() -> None:
    client = KiwiMcpClient("http://127.0.0.1/mcp", 10.0, client_factory=lambda: _NeverEntered())
    assert client._start_session() is None  # unobservable: nothing is claimed


class _NeverEntered:
    async def __aenter__(self) -> Any:  # pragma: no cover - never used
        raise AssertionError

    async def __aexit__(self, *args: Any) -> None:  # pragma: no cover
        return None


def test_a_timeout_is_a_named_provider_failure() -> None:
    class _Slow:
        async def __aenter__(self) -> Any:
            await asyncio.sleep(5)

        async def __aexit__(self, *args: Any) -> None:
            return None

    client = KiwiMcpClient("http://127.0.0.1/mcp", 10.0, client_factory=_Slow, total_timeout_seconds=0.05)
    assert client.discover_tools().failure_kind == FAILURE_TIMEOUT
    result = KiwiMcpFlightProvider(client=client).search_flights(
        FlightSearchRequest(origin="LHR", destination="OPO", departure_date=_FUTURE, adults=1)
    )
    assert result.status == FlightSearchStatus.FAILED and result.availability_reason == Reason.PROVIDER_FAILED
