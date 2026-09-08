from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from datetime import date

import pytest

from app.core.config import Settings
from app.models.flight import FlightSearchRequest, FlightSearchStatus
from app.providers.flights import KiwiMcpFlightProvider
from app.providers.flights.kiwi_mcp_adapter import build_search_flight_arguments
from app.providers.flights.kiwi_mcp_client import (
    KiwiMcpClient,
    KiwiMcpDiscoveryStatus,
    KiwiMcpToolCallResult,
    KiwiMcpToolCallStatus,
    KiwiMcpToolDiscoveryResult,
    McpToolInfo,
)

# Step 178B/178C: KiwiMcpFlightProvider tests. Every test injects a fake
# KiwiMcpClient/settings -- never a real MCP connection.

# Trimmed version of the real `search-flight` input schema discovered
# live against https://mcp.kiwi.com during Step 178B/178C -- used here so
# argument-builder/full-flow tests exercise the actual field names/
# descriptions this tool declares, not an invented shape.
_REAL_SEARCH_FLIGHT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "flyFrom": {
            "description": 'Origin: IATA code, city or airport name (e.g. "PRG", "Prague", "London Heathrow")',
            "type": "string",
        },
        "flyTo": {
            "description": "Destination: IATA code, city or airport name",
            "type": "string",
        },
        "departureDate": {
            "description": "Departure date in dd/mm/yyyy",
            "type": "string",
        },
        "returnDate": {
            "description": "Return date in dd/mm/yyyy",
            "type": "string",
        },
        "adults": {"default": 1, "ge": 1, "le": 9, "type": "integer"},
        "children": {"default": 0, "maximum": 8, "minimum": 0, "type": "integer"},
        "cabinClass": {"enum": ["M", "W", "C", "F"], "type": "string"},
        "currency": {"default": "EUR", "type": "string"},
    },
    "required": ["flyFrom", "flyTo", "departureDate"],
}

# A schema shaped like it might require exact IATA codes only (no
# "city"/"airport name" wording) -- used to test the conservative refusal
# path.
_IATA_ONLY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "flyFrom": {"description": "Origin IATA code", "type": "string"},
        "flyTo": {"description": "Destination IATA code", "type": "string"},
        "departureDate": {"description": "Departure date", "type": "string"},
    },
    "required": ["flyFrom", "flyTo", "departureDate"],
}


@dataclass
class _FakeKiwiMcpClient:
    result: KiwiMcpToolDiscoveryResult
    call_tool_result: KiwiMcpToolCallResult | None = None
    calls: int = 0
    call_tool_calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def discover_tools(self) -> KiwiMcpToolDiscoveryResult:
        self.calls += 1
        return self.result

    def call_tool(self, tool_name: str, arguments: dict[str, object]) -> KiwiMcpToolCallResult:
        self.call_tool_calls.append((tool_name, arguments))
        return self.call_tool_result or KiwiMcpToolCallResult(
            status=KiwiMcpToolCallStatus.SUCCESS,
            structured_content={"itineraries": [], "resultsCount": 0, "error": None},
        )


def _request(**overrides: object) -> FlightSearchRequest:
    fields: dict[str, object] = {
        "origin": "London",
        "destination": "Paris",
        "departure_date": date(2026, 10, 10),
    }
    fields.update(overrides)
    return FlightSearchRequest(**fields)


def _settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {"_env_file": None}
    fields.update(overrides)
    return Settings(**fields)


# ---------------------------------------------------------------------------
# 7. Disabled provider returns not_connected, empty offers -- never calls
#    the MCP client at all.
# ---------------------------------------------------------------------------


def test_disabled_provider_returns_not_connected_without_calling_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=False))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.SUCCESS)
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []
    assert fake_client.calls == 0


# ---------------------------------------------------------------------------
# 8. Enabled provider calls only tool discovery, never a search tool.
# ---------------------------------------------------------------------------


def test_enabled_provider_calls_discovery_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS,
            tools=[McpToolInfo(name="search-flight")],
        )
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    provider.search_flights(_request())

    assert fake_client.calls == 1


# ---------------------------------------------------------------------------
# Step 178C: discovery success with a flight-search-looking tool now
# proceeds to build arguments and call the tool (never
# "mapping is not enabled until Step 178C" anymore -- that placeholder
# message no longer exists once 178C implements the real mapping).
# ---------------------------------------------------------------------------


def test_discovery_success_with_flight_tool_calls_search_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS,
            tools=[
                McpToolInfo(
                    name="search-flight",
                    description="Search for flights",
                    input_schema=_REAL_SEARCH_FLIGHT_SCHEMA,
                ),
                McpToolInfo(name="feedback-to-devs", description="mentions search-flight"),
            ],
        )
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert fake_client.call_tool_calls == [
        (
            "search-flight",
            {
                "flyFrom": "London",
                "flyTo": "Paris",
                "departureDate": "10/10/2026",
                "adults": 1,
                "children": 0,
            },
        )
    ]
    # No results in the fake's default call_tool_result -> honest unavailable.
    assert result.status == FlightSearchStatus.UNAVAILABLE


def test_flight_tool_with_no_schema_never_calls_search_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the discovered tool carries no input schema, the argument
    builder refuses to guess field names -- search-flight is never
    called."""
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS,
            tools=[McpToolInfo(name="search-flight", description="Search for flights")],
        )
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert fake_client.call_tool_calls == []
    assert result.status == FlightSearchStatus.UNAVAILABLE


def test_configured_tool_name_requires_exact_match(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(
        adapter_module,
        "get_settings",
        lambda: _settings(kiwi_mcp_enabled=True, kiwi_mcp_tool_name="search-flight"),
    )
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS,
            tools=[McpToolInfo(name="flight-search-v2")],
        )
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    # "flight-search-v2" != the configured exact name "search-flight" --
    # never a fuzzy/substring match once an exact name is configured.
    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert "no flight-search tool was found" in (result.message or "")


# ---------------------------------------------------------------------------
# 10. Discovery success with no flight-search-looking tool returns
#     unavailable, empty offers, honest message.
# ---------------------------------------------------------------------------


def test_discovery_success_with_no_flight_tool_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS,
            tools=[McpToolInfo(name="feedback-to-devs")],
        )
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []
    assert "no flight-search tool was found" in (result.message or "").lower()


def test_discovery_success_with_zero_tools_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.SUCCESS, tools=[])
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


# ---------------------------------------------------------------------------
# 11. Discovery failure returns failed, empty offers.
# ---------------------------------------------------------------------------


def test_discovery_failure_returns_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.FAILED,
            tools=[],
            message="Could not reach the Kiwi MCP endpoint or list its tools.",
        )
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert result.status == FlightSearchStatus.FAILED
    assert result.offers == []
    assert result.message is not None


# ---------------------------------------------------------------------------
# 12. Provider echoes request search context exactly, in every branch.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "discovery_result",
    [
        KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.FAILED, message="x"),
        KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.SUCCESS, tools=[]),
        KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS, tools=[McpToolInfo(name="search-flight")]
        ),
    ],
)
def test_provider_echoes_request_context_in_every_branch(
    monkeypatch: pytest.MonkeyPatch, discovery_result: KiwiMcpToolDiscoveryResult
) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(result=discovery_result)
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    request = _request(
        origin="New York",
        destination="Lisbon, Portugal",
        departure_date=date(2026, 11, 1),
        return_date=date(2026, 11, 8),
        adults=2,
        children=1,
        currency="EUR",
    )
    result = provider.search_flights(request)

    assert result.origin == "New York"
    assert result.destination == "Lisbon, Portugal"
    assert result.departure_date == date(2026, 11, 1)
    assert result.return_date == date(2026, 11, 8)
    assert result.adults == 2
    assert result.children == 1
    assert result.currency == "EUR"


# ---------------------------------------------------------------------------
# 13. Provider never populates offers in 178B, in any branch.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kiwi_mcp_enabled,discovery_result",
    [
        (False, None),
        (True, KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.FAILED)),
        (True, KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.SUCCESS, tools=[])),
        (
            True,
            KiwiMcpToolDiscoveryResult(
                status=KiwiMcpDiscoveryStatus.SUCCESS, tools=[McpToolInfo(name="search-flight")]
            ),
        ),
    ],
)
def test_provider_never_populates_offers(
    monkeypatch: pytest.MonkeyPatch,
    kiwi_mcp_enabled: bool,
    discovery_result: KiwiMcpToolDiscoveryResult | None,
) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(
        adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=kiwi_mcp_enabled)
    )
    fake_client = _FakeKiwiMcpClient(
        result=discovery_result or KiwiMcpToolDiscoveryResult(status=KiwiMcpDiscoveryStatus.FAILED)
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert result.offers == []


# ---------------------------------------------------------------------------
# 3/4/5. build_search_flight_arguments tests.
# ---------------------------------------------------------------------------


def test_argument_builder_uses_discovered_schema_field_names() -> None:
    """Required test 3: uses flyFrom/flyTo/departureDate (the real,
    discovered field names), never Tequila-API-style fly_from/dateFrom."""
    request = _request(departure_date=date(2026, 12, 15))

    arguments, error = build_search_flight_arguments(request, _REAL_SEARCH_FLIGHT_SCHEMA)

    assert error is None
    assert arguments == {
        "flyFrom": "London",
        "flyTo": "Paris",
        "departureDate": "15/12/2026",
        "adults": 1,
        "children": 0,
    }
    assert "fly_from" not in (arguments or {})
    assert "dateFrom" not in (arguments or {})


def test_argument_builder_includes_return_date_currency_cabin_class_when_supported() -> None:
    request = _request(
        return_date=date(2026, 12, 20),
        currency="USD",
        cabin_class="C",
    )

    arguments, error = build_search_flight_arguments(request, _REAL_SEARCH_FLIGHT_SCHEMA)

    assert error is None
    assert arguments is not None
    assert arguments["returnDate"] == "20/12/2026"
    assert arguments["currency"] == "USD"
    assert arguments["cabinClass"] == "C"


def test_argument_builder_omits_cabin_class_when_not_a_recognized_kiwi_value() -> None:
    """A free-text cabin_class that doesn't exactly match Kiwi's own
    enum is omitted, never translated/guessed."""
    request = _request(cabin_class="economy")

    arguments, error = build_search_flight_arguments(request, _REAL_SEARCH_FLIGHT_SCHEMA)

    assert error is None
    assert arguments is not None
    assert "cabinClass" not in arguments


def test_argument_builder_refuses_when_required_fields_missing_from_schema() -> None:
    schema_missing_departure_date = {
        "properties": {"flyFrom": {}, "flyTo": {}},
        "required": ["flyFrom", "flyTo"],
    }

    arguments, error = build_search_flight_arguments(_request(), schema_missing_departure_date)

    assert arguments is None
    assert error is not None
    assert "departureDate" in error


# ---------------------------------------------------------------------------
# 4. Argument builder passes free-form origin/destination only when the
#    schema documents that it accepts them.
# ---------------------------------------------------------------------------


def test_argument_builder_passes_free_form_places_when_schema_accepts_them() -> None:
    request = _request(origin="London", destination="Paris")

    arguments, error = build_search_flight_arguments(request, _REAL_SEARCH_FLIGHT_SCHEMA)

    assert error is None
    assert arguments is not None
    assert arguments["flyFrom"] == "London"
    assert arguments["flyTo"] == "Paris"


# ---------------------------------------------------------------------------
# 5. Argument builder refuses unsafe airport-code guessing when the
#    schema requires IATA codes and the request values don't look like
#    IATA codes.
# ---------------------------------------------------------------------------


def test_argument_builder_refuses_city_names_when_schema_requires_iata() -> None:
    request = _request(origin="London", destination="Paris")

    arguments, error = build_search_flight_arguments(request, _IATA_ONLY_SCHEMA)

    assert arguments is None
    assert error == "Kiwi MCP requires pre-resolved airport codes for this build."


def test_argument_builder_allows_iata_codes_when_schema_requires_iata() -> None:
    request = _request(origin="LHR", destination="CDG")

    arguments, error = build_search_flight_arguments(request, _IATA_ONLY_SCHEMA)

    assert error is None
    assert arguments is not None
    assert arguments["flyFrom"] == "LHR"
    assert arguments["flyTo"] == "CDG"


def test_argument_builder_refuses_when_origin_missing() -> None:
    request = _request(origin=None)

    arguments, error = build_search_flight_arguments(request, _REAL_SEARCH_FLIGHT_SCHEMA)

    assert arguments is None
    assert error == "No origin was provided for this flight search."


def test_argument_builder_refuses_zero_adults() -> None:
    request = _request(adults=0)

    arguments, error = build_search_flight_arguments(request, _REAL_SEARCH_FLIGHT_SCHEMA)

    assert arguments is None
    assert error == "Kiwi MCP requires at least one adult traveler for a flight search."


# ---------------------------------------------------------------------------
# 14/16/17/18: full enabled-search flow -- discovery, then search-flight,
# then the deterministic parser.
# ---------------------------------------------------------------------------

_REAL_ONE_WAY_ITINERARY = {
    "id": "22f525c35142000074e76ad3_0",
    "price": 53.0,
    "bookingUrl": "https://kiwi.com/u/m9fxvz",
    "baggage": {"personalItem": 1, "cabinBag": 0, "checkedBag": 0},
    "outbound": {
        "segments": [
            {
                "from": "LGW",
                "to": "CDG",
                "departureTime": "2026-12-15T16:45:00",
                "arrivalTime": "2026-12-15T19:00:00",
                "durationSeconds": 4500,
                "carrier": "U2",
                "carrierName": "easyJet",
                "flightNumber": "U28407",
            }
        ],
    },
    "inbound": None,
}


def _flight_search_tool() -> McpToolInfo:
    return McpToolInfo(
        name="search-flight",
        description="Search for flights",
        input_schema=_REAL_SEARCH_FLIGHT_SCHEMA,
    )


def test_enabled_path_calls_discovery_then_search_flight_then_parses_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required test 14: the full pipeline -- discovery, search-flight,
    parser -- produces a real success result with real offer data."""
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS, tools=[_flight_search_tool()]
        ),
        call_tool_result=KiwiMcpToolCallResult(
            status=KiwiMcpToolCallStatus.SUCCESS,
            structured_content={
                "currency": "EUR",
                "resultsCount": 1,
                "itineraries": [_REAL_ONE_WAY_ITINERARY],
                "error": None,
            },
        ),
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert fake_client.calls == 1
    assert fake_client.call_tool_calls[0][0] == "search-flight"
    assert result.status == FlightSearchStatus.SUCCESS
    assert len(result.offers) == 1
    assert result.offers[0].provider == "kiwi_mcp"
    assert result.offers[0].total_price_amount == pytest.approx(53.0)


# ---------------------------------------------------------------------------
# 15. Provider never calls feedback-to-devs (or any tool other than the
#     one classified as flight-search).
# ---------------------------------------------------------------------------


def test_provider_never_calls_feedback_to_devs(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS,
            tools=[
                McpToolInfo(name="feedback-to-devs", description="Send feedback"),
                _flight_search_tool(),
            ],
        ),
        call_tool_result=KiwiMcpToolCallResult(
            status=KiwiMcpToolCallStatus.SUCCESS,
            structured_content={"itineraries": [], "resultsCount": 0, "error": None},
        ),
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    provider.search_flights(_request())

    called_tool_names = {name for name, _ in fake_client.call_tool_calls}
    assert called_tool_names == {"search-flight"}
    assert "feedback-to-devs" not in called_tool_names


# ---------------------------------------------------------------------------
# 16. Provider returns failed on MCP call failure.
# ---------------------------------------------------------------------------


def test_provider_returns_failed_on_call_tool_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS, tools=[_flight_search_tool()]
        ),
        call_tool_result=KiwiMcpToolCallResult(
            status=KiwiMcpToolCallStatus.FAILED,
            message="Could not call the Kiwi MCP 'search-flight' tool.",
        ),
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert result.status == FlightSearchStatus.FAILED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 17. Provider never returns success with zero offers.
# ---------------------------------------------------------------------------


def test_provider_never_returns_success_with_zero_offers(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS, tools=[_flight_search_tool()]
        ),
        call_tool_result=KiwiMcpToolCallResult(
            status=KiwiMcpToolCallStatus.SUCCESS,
            structured_content={"itineraries": [], "resultsCount": 0, "error": None},
        ),
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert not (result.status == FlightSearchStatus.SUCCESS and not result.offers)
    assert result.status == FlightSearchStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# 18. Provider never fabricates price/schedule/booking data -- every
#     value in a successful result traces back to the fake payload.
# ---------------------------------------------------------------------------


def test_provider_never_fabricates_offer_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.flights.kiwi_mcp_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "get_settings", lambda: _settings(kiwi_mcp_enabled=True))
    minimal_itinerary = {
        "id": "minimal-1",
        "outbound": {"segments": [{"from": "LGW", "to": "CDG"}]},
        "inbound": None,
    }
    fake_client = _FakeKiwiMcpClient(
        result=KiwiMcpToolDiscoveryResult(
            status=KiwiMcpDiscoveryStatus.SUCCESS, tools=[_flight_search_tool()]
        ),
        call_tool_result=KiwiMcpToolCallResult(
            status=KiwiMcpToolCallStatus.SUCCESS,
            structured_content={
                "currency": None,
                "itineraries": [minimal_itinerary],
                "resultsCount": 1,
                "error": None,
            },
        ),
    )
    provider = KiwiMcpFlightProvider(client=fake_client)  # type: ignore[arg-type]

    result = provider.search_flights(_request())

    assert result.status == FlightSearchStatus.SUCCESS
    offer = result.offers[0]
    # Nothing the fake payload didn't provide is ever filled in.
    assert offer.total_price_amount is None
    assert offer.booking_url is None
    assert offer.baggage_policy is None
    assert offer.currency is None
    segment = offer.outbound_segments[0]
    assert segment.carrier_name is None
    assert segment.flight_number is None
    assert segment.departure_time is None


# ---------------------------------------------------------------------------
# 14. No LLM call/prompt-parsing anywhere in this adapter or its client.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_name",
    [
        "app.providers.flights.kiwi_mcp_adapter",
        "app.providers.flights.kiwi_mcp_client",
        "app.providers.flights.kiwi_mcp_parser",
    ],
)
def test_kiwi_mcp_modules_never_import_an_llm_provider(module_name: str) -> None:
    """AST-based (not raw substring) so an explanatory comment mentioning
    e.g. "anthropic/groq" by name (as this module's own docstring does,
    to explain its lazy-import precedent) never false-positives here --
    only an actual `import`/`from ... import` statement counts."""
    module = __import__(module_name, fromlist=["_"])
    tree = ast.parse(inspect.getsource(module))
    disallowed = ("anthropic", "groq", "openai", "gemini", "google.generativeai", "langgraph")

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for banned in disallowed:
            assert banned not in lowered, f"{module_name}: disallowed import found: {name}"


def test_only_kiwi_mcp_client_module_imports_the_mcp_sdk() -> None:
    """`kiwi_mcp_adapter.py`, `kiwi_mcp_parser.py`, and `factory.py` must
    reach the MCP SDK only indirectly, through `KiwiMcpClient` -- none of
    them imports `mcp` itself."""
    import app.providers.flights.factory as factory_module
    import app.providers.flights.kiwi_mcp_adapter as adapter_module
    import app.providers.flights.kiwi_mcp_parser as parser_module

    for module in (adapter_module, factory_module, parser_module):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "mcp" and not node.module.startswith("mcp."), (
                    f"{module.__name__} should not import the mcp SDK directly"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "mcp" and not alias.name.startswith("mcp."), (
                        f"{module.__name__} should not import the mcp SDK directly"
                    )


# ---------------------------------------------------------------------------
# 15. No fake flight data anywhere -- provider_name is stable, and no
#     module constructs a FlightOffer.
# ---------------------------------------------------------------------------


def test_kiwi_mcp_adapter_never_constructs_a_flight_offer() -> None:
    """The adapter/client hand the raw MCP call result to
    `kiwi_mcp_parser` -- only that module ever constructs a `FlightOffer`/
    `FlightSegment`."""
    import app.providers.flights.kiwi_mcp_adapter as adapter_module
    import app.providers.flights.kiwi_mcp_client as client_module

    for module in (adapter_module, client_module):
        source = inspect.getsource(module)
        assert "FlightOffer(" not in source
        assert "FlightSegment(" not in source


def test_provider_name_is_stable() -> None:
    assert KiwiMcpFlightProvider.provider_name == "kiwi_mcp_flight_provider"
