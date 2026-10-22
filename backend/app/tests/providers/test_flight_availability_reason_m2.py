"""M2 Phase 1 (D2): every flight result names WHY it has the offers it has.
`search_completed_no_offers` -- the only cause Traveler view may word as
"no flight offers were found" -- is set ONLY when a live search completed
and returned an empty itinerary list. Fakes and local files only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.models.flight import (
    FlightAvailabilityReason as Reason,
    FlightSearchRequest,
    FlightSearchResult,
    FlightSearchStatus,
)
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.flights import KiwiMcpFlightProvider
from app.providers.flights import kiwi_mcp_adapter as kiwi_adapter_module
from app.providers.flights import scraped_adapter as scraped_adapter_module
from app.providers.flights.kiwi_mcp_client import (
    KiwiMcpDiscoveryStatus,
    KiwiMcpToolCallResult,
    KiwiMcpToolCallStatus,
    KiwiMcpToolDiscoveryResult,
    McpToolInfo,
)
from app.providers.flights.kiwi_mcp_parser import parse_kiwi_mcp_search_result
from app.providers.flights.not_connected_adapter import NotConnectedFlightProvider
from app.providers.flights.scraped_adapter import ScrapedLocalFlightProvider
from app.services.flight_inventory_service import FlightInventoryService, ensure_availability_reason

_SCHEMA = {
    "type": "object",
    "properties": {
        "flyFrom": {"description": "Origin: IATA code, city or airport name", "type": "string"},
        "flyTo": {"description": "Destination: IATA code, city or airport name", "type": "string"},
        "departureDate": {"description": "dd/mm/yyyy", "type": "string"},
    },
}
_SEGMENT = {
    "from": "LGW",
    "to": "CDG",
    "departureTime": "2026-12-15T16:45:00",
    "arrivalTime": "2026-12-15T19:00:00",
    "durationSeconds": 4500,
    "carrier": "U2",
    "carrierName": "easyJet",
    "flightNumber": "U28407",
}
_ITINERARY = {"id": "itinerary-1", "price": 53.0, "outbound": {"segments": [_SEGMENT]}, "inbound": None}


def _request(**overrides: Any) -> FlightSearchRequest:
    fields: dict[str, Any] = {"origin": "London", "destination": "Paris", "departure_date": date(2026, 12, 15)}
    fields.update(overrides)
    return FlightSearchRequest(**fields)


@dataclass
class _FakeKiwiClient:
    discovery: KiwiMcpToolDiscoveryResult
    call_result: KiwiMcpToolCallResult | None = None
    calls: list[str] = field(default_factory=list)

    def discover_tools(self) -> KiwiMcpToolDiscoveryResult:
        return self.discovery

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> KiwiMcpToolCallResult:
        self.calls.append(tool_name)
        assert self.call_result is not None
        return self.call_result


def _discovered(*names: str) -> KiwiMcpToolDiscoveryResult:
    return KiwiMcpToolDiscoveryResult(
        status=KiwiMcpDiscoveryStatus.SUCCESS,
        tools=[McpToolInfo(name=name, input_schema=_SCHEMA) for name in names],
    )


def _kiwi(monkeypatch: pytest.MonkeyPatch, client: _FakeKiwiClient, enabled: bool = True) -> KiwiMcpFlightProvider:
    monkeypatch.setattr(
        kiwi_adapter_module, "get_settings", lambda: Settings(_env_file=None, kiwi_mcp_enabled=enabled)
    )
    return KiwiMcpFlightProvider(client=client)  # type: ignore[arg-type]


def _structured(payload: Any) -> KiwiMcpToolCallResult:
    return KiwiMcpToolCallResult(status=KiwiMcpToolCallStatus.SUCCESS, structured_content=payload)


# -- Kiwi MCP --------------------------------------------------------------------------------


def test_kiwi_disabled_is_no_source(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeKiwiClient(_discovered("search-flight"))
    result = _kiwi(monkeypatch, client, enabled=False).search_flights(_request())
    assert result.availability_reason == Reason.NO_SOURCE_CONFIGURED
    assert client.calls == []


def test_kiwi_discovery_failure_is_a_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeKiwiClient(KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.FAILED, message="timeout"))
    result = _kiwi(monkeypatch, client).search_flights(_request())
    assert result.status == FlightSearchStatus.FAILED
    assert result.availability_reason == Reason.PROVIDER_FAILED


def test_kiwi_without_a_flight_tool_cannot_search(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeKiwiClient(_discovered("feedback-to-devs"))
    result = _kiwi(monkeypatch, client).search_flights(_request())
    assert result.availability_reason == Reason.SEARCH_NOT_POSSIBLE
    assert client.calls == []


def test_kiwi_without_an_origin_cannot_search(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeKiwiClient(_discovered("search-flight"))
    result = _kiwi(monkeypatch, client).search_flights(_request(origin=None))
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.availability_reason == Reason.SEARCH_NOT_POSSIBLE
    assert client.calls == []


def test_kiwi_empty_itinerary_list_is_the_only_completed_zero_result(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeKiwiClient(
        _discovered("search-flight"), _structured({"itineraries": [], "resultsCount": 0, "error": None})
    )
    result = _kiwi(monkeypatch, client).search_flights(_request())
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.availability_reason == Reason.SEARCH_COMPLETED_NO_OFFERS
    assert client.calls == ["search-flight"]


def test_kiwi_success_is_offers_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeKiwiClient(
        _discovered("search-flight"),
        _structured({"itineraries": [_ITINERARY], "resultsCount": 1, "currency": "EUR"}),
    )
    result = _kiwi(monkeypatch, client).search_flights(_request())
    assert result.status == FlightSearchStatus.SUCCESS and result.offers
    assert result.availability_reason == Reason.OFFERS_FOUND


@pytest.mark.parametrize(
    "payload",
    [
        {"itineraries": [{"id": "x"}], "resultsCount": 1},  # itineraries that cannot be parsed
        {"itineraries": [{"renamed": True}]},  # a renamed shape, no count
        {"itineraries": [], "resultsCount": 4},  # results reported, none delivered
    ],
    ids=["unparseable", "renamed-shape", "count-without-items"],
)
def test_kiwi_unusable_answers_are_never_a_zero_result(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    client = _FakeKiwiClient(_discovered("search-flight"), _structured(payload))
    result = _kiwi(monkeypatch, client).search_flights(_request())
    assert result.availability_reason == Reason.RESPONSE_UNUSABLE
    assert result.availability_reason != Reason.SEARCH_COMPLETED_NO_OFFERS


@pytest.mark.parametrize(
    ("call_result", "expected"),
    [
        (KiwiMcpToolCallResult(status=KiwiMcpToolCallStatus.FAILED, message="timed out"), Reason.PROVIDER_FAILED),
        (KiwiMcpToolCallResult(status=KiwiMcpToolCallStatus.SUCCESS, is_error=True), Reason.PROVIDER_FAILED),
        (_structured({"error": "Unknown city"}), Reason.PROVIDER_FAILED),
        (_structured(["not", "a", "dict"]), Reason.RESPONSE_UNUSABLE),
        (_structured({"itineraries": "not a list"}), Reason.RESPONSE_UNUSABLE),
    ],
    ids=["call-failed", "tool-error", "reported-error", "not-a-dict", "itineraries-not-a-list"],
)
def test_kiwi_parser_failures_name_their_cause(call_result: KiwiMcpToolCallResult, expected: Reason) -> None:
    result = parse_kiwi_mcp_search_result(call_result, _request())
    assert result.status == FlightSearchStatus.FAILED
    assert result.availability_reason == expected


# -- not connected / scraped local files ------------------------------------------------------


def test_not_connected_is_no_source() -> None:
    result = NotConnectedFlightProvider().search_flights(_request())
    assert result.availability_reason == Reason.NO_SOURCE_CONFIGURED


def _scraped(monkeypatch: pytest.MonkeyPatch, html_path: str | None, **overrides: Any) -> FlightSearchResult:
    fields: dict[str, Any] = {
        "scraping_enabled": True,
        "scraped_flight_provider_enabled": True,
        "scraped_flight_html_path": html_path,
        "scraped_flight_html_path_skyscanner": None,
        "scraped_flight_html_path_google_flights": None,
        "scraped_flight_html_path_kiwi_manual": None,
        "provider_cache_enabled": False,
    }
    fields.update(overrides)
    settings = Settings(_env_file=None, **fields)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)
    return ScrapedLocalFlightProvider().search_flights(_request(origin="TST", destination="DMO"))


def test_scraped_without_a_file_is_no_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The production default: the scraped provider is on, no file exists.
    result = _scraped(monkeypatch, str(tmp_path / "missing.html"))
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.availability_reason == Reason.NO_SOURCE_CONFIGURED


def test_scraped_with_no_path_is_no_source(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _scraped(monkeypatch, None).availability_reason == Reason.NO_SOURCE_CONFIGURED


def test_scraped_disabled_is_no_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    result = _scraped(monkeypatch, str(tmp_path / "missing.html"), scraped_flight_provider_enabled=False)
    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.availability_reason == Reason.NO_SOURCE_CONFIGURED


def test_scraped_file_without_offers_is_not_a_search_result(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    html = tmp_path / "flights.html"
    html.write_text("<html><body>no flight offers here</body></html>", encoding="utf-8")
    result = _scraped(monkeypatch, str(html))
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.availability_reason == Reason.LOCAL_SOURCE_NO_MATCHING_OFFERS


def test_scraped_file_with_an_offer_is_offers_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    html = tmp_path / "flights.html"
    html.write_text(
        '<html><body><div class="flight-offer" data-offer-id="alpha-1"><div class="outbound-segment">'
        '<span class="origin-airport">TST</span><span class="destination-airport">DMO</span>'
        "</div></div></body></html>",
        encoding="utf-8",
    )
    result = _scraped(monkeypatch, str(html))
    assert result.status == FlightSearchStatus.SUCCESS
    assert result.availability_reason == Reason.OFFERS_FOUND


# -- the service fallback ------------------------------------------------------------------


def _bare(status: FlightSearchStatus) -> FlightSearchResult:
    return FlightSearchResult(provider="p", status=status, destination="Paris", departure_date=date(2026, 12, 15))


def test_fallback_classifies_only_what_the_status_proves() -> None:
    assert ensure_availability_reason(_bare(FlightSearchStatus.NOT_CONNECTED)).availability_reason == (
        Reason.NO_SOURCE_CONFIGURED
    )
    assert ensure_availability_reason(_bare(FlightSearchStatus.FAILED)).availability_reason == Reason.PROVIDER_FAILED
    # An unclassified `unavailable` is NEVER assumed to be a completed search.
    assert ensure_availability_reason(_bare(FlightSearchStatus.UNAVAILABLE)).availability_reason is None


def test_fallback_keeps_an_adapter_reason() -> None:
    result = _bare(FlightSearchStatus.UNAVAILABLE).with_reason(Reason.SEARCH_NOT_POSSIBLE)
    assert ensure_availability_reason(result).availability_reason == Reason.SEARCH_NOT_POSSIBLE


def _planning_state() -> PlanningState:
    return PlanningState(
        trip_request=TripRequest(
            primary_destination="Paris",
            origin_city="London",
            start_date="2026-12-15",
            end_date="2026-12-18",
            travelers_count=1,
            travel_group_type=TravelGroupType.SOLO,
        )
    )


class _RaisingGateway:
    class flight_inventory:  # noqa: N801 - mimics the gateway attribute
        provider_name = "raising"

    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResult:
        raise RuntimeError("boom")


def test_service_unexpected_failure_is_a_provider_failure() -> None:
    result = FlightInventoryService(gateway=_RaisingGateway()).build_report(_planning_state())  # type: ignore[arg-type]
    assert result.status == FlightSearchStatus.FAILED
    assert result.availability_reason == Reason.PROVIDER_FAILED


@pytest.mark.parametrize(
    ("status", "reason", "must_contain", "must_not_contain"),
    [
        (FlightSearchStatus.UNAVAILABLE, Reason.NO_SOURCE_CONFIGURED, "No flight source is set up", "was checked"),
        (FlightSearchStatus.UNAVAILABLE, Reason.SEARCH_NOT_POSSIBLE, "could not be made", "was checked"),
        (FlightSearchStatus.UNAVAILABLE, Reason.RESPONSE_UNUSABLE, "does not mean no flights exist", "was checked"),
        (FlightSearchStatus.FAILED, Reason.RESPONSE_UNUSABLE, "does not mean no flights exist", "request failed"),
        (FlightSearchStatus.UNAVAILABLE, Reason.SEARCH_COMPLETED_NO_OFFERS, "was checked", "No flight source"),
        (FlightSearchStatus.UNAVAILABLE, None, "was checked", "No flight source"),  # an older report
        (FlightSearchStatus.FAILED, Reason.PROVIDER_FAILED, "request failed", "was checked"),
    ],
)
def test_validation_message_follows_the_reason(
    status: FlightSearchStatus, reason: Reason | None, must_contain: str, must_not_contain: str
) -> None:
    from app.services.plan_validator_service import _build_flight_inventory_warning

    report = _bare(status)
    if reason is not None:
        report = report.with_reason(reason)
    message = _build_flight_inventory_warning(report).message
    assert must_contain in message
    assert must_not_contain not in message


def test_reason_survives_serialization_and_older_reports_still_load() -> None:
    result = _bare(FlightSearchStatus.UNAVAILABLE).with_reason(Reason.SEARCH_COMPLETED_NO_OFFERS)
    assert result.model_dump(mode="json")["availability_reason"] == "search_completed_no_offers"
    legacy = _bare(FlightSearchStatus.UNAVAILABLE).model_dump(mode="json")
    legacy.pop("availability_reason")
    assert FlightSearchResult.model_validate(legacy).availability_reason is None
