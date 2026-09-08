from __future__ import annotations

import re
from typing import Any

from app.core.config import Settings, get_settings
from app.models.flight import FlightSearchRequest, FlightSearchResult, FlightSearchStatus
from app.providers.flights.base import FlightInventoryProvider
from app.providers.flights.kiwi_mcp_client import (
    KiwiMcpClient,
    KiwiMcpDiscoveryStatus,
    McpToolInfo,
)
from app.providers.flights.kiwi_mcp_parser import parse_kiwi_mcp_search_result

# Kiwi MCP flight provider (Step 178B: tool discovery only; Step 178C:
# real search-flight invocation, docs/12_provider_architecture.md,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
#
# As of Step 178C, this is the first `FlightInventoryProvider` in this
# codebase that can return a real, live, provider-backed offer -- but only
# when both `Settings.flight_provider == "kiwi_mcp"` and
# `Settings.kiwi_mcp_enabled` are explicitly set; the default remains
# `scraped_local`/disabled. Even when enabled, this adapter never calls
# any tool other than the one discovery classified as flight-search-
# looking (never `feedback-to-devs` or any other tool), never calls an
# LLM, never parses free-form natural-language text as flight data
# (`app.providers.flights.kiwi_mcp_parser` reads only the tool's
# structured JSON result, never its free-form `content` text blocks),
# never infers an airport code from a city name (Kiwi's own `search-
# flight` schema documents that it resolves free-form place names itself
# -- confirmed from that schema's own field descriptions, not assumed),
# and never constructs a booking URL (a returned `booking_url` is always
# exactly the third-party Kiwi link the tool itself returned).

_NOT_ENABLED_MESSAGE = (
    "Kiwi MCP is not enabled (set KIWI_MCP_ENABLED=true to allow tool "
    "discovery); flight inventory was not checked."
)
_DISCOVERY_FAILED_MESSAGE_TEMPLATE = (
    "Kiwi MCP tool discovery failed ({detail}); flight inventory could not be checked."
)
_NO_FLIGHT_TOOL_MESSAGE = (
    "Kiwi MCP is reachable, but no flight-search tool was found among its "
    "discovered tools; flight inventory could not be checked."
)

_FLIGHT_TOOL_NAME_HINT = "flight"


def _looks_like_flight_search_tool(tool: McpToolInfo, configured_tool_name: str | None) -> bool:
    """Conservative, non-fabricating classification of an already-
    discovered tool -- never a call, never a guess about what the tool
    does beyond its own advertised name.

    When `configured_tool_name` is set, only an exact name match counts.
    When unset, this falls back to a simple substring heuristic against
    the tool's own *name* only -- deliberately not its `description`,
    since a live discovery run during Step 178B found an unrelated
    `feedback-to-devs` tool whose description happens to mention
    "search-flight" in passing; matching on name only avoids that false
    positive. This classifies real, already-returned metadata -- it never
    invents a tool or its capabilities.
    """
    if configured_tool_name is not None:
        return tool.name == configured_tool_name
    return _FLIGHT_TOOL_NAME_HINT in tool.name.lower()


def _find_flight_search_tool(
    tools: list[McpToolInfo], configured_tool_name: str | None
) -> McpToolInfo | None:
    for tool in tools:
        if _looks_like_flight_search_tool(tool, configured_tool_name):
            return tool
    return None


# ---------------------------------------------------------------------------
# Step 178C: search-flight argument builder.
#
# Field names below (`flyFrom`/`flyTo`/`departureDate`/`returnDate`/
# `adults`/`children`/`cabinClass`/`currency`, dd/mm/yyyy date format) were
# read directly from a real, live `list_tools` discovery response against
# https://mcp.kiwi.com during this step -- never assumed from the public
# Tequila API docs (which use different field names, e.g. `fly_from`/
# `dateFrom`, and are a different product from this MCP tool). If Kiwi
# ever changes this tool's schema, `_required_fields_present` below
# refuses to guess a different shape rather than silently sending the old
# field names.
# ---------------------------------------------------------------------------

_FLY_FROM_FIELD = "flyFrom"
_FLY_TO_FIELD = "flyTo"
_DEPARTURE_DATE_FIELD = "departureDate"
_RETURN_DATE_FIELD = "returnDate"
_ADULTS_FIELD = "adults"
_CHILDREN_FIELD = "children"
_CABIN_CLASS_FIELD = "cabinClass"
_CURRENCY_FIELD = "currency"
_REQUIRED_ARGUMENT_FIELDS = (_FLY_FROM_FIELD, _FLY_TO_FIELD, _DEPARTURE_DATE_FIELD)

# Kiwi's own discovered enum for cabinClass -- M=economy, W=premium
# economy, C=business, F=first. `FlightSearchRequest.cabin_class` is a
# free-text field (this app never constrains its values), so a value that
# doesn't exactly match one of these is simply omitted from the call
# rather than guessed/translated into one of them.
_KIWI_CABIN_CLASS_VALUES = frozenset({"M", "W", "C", "F"})

_IATA_CODE_PATTERN = re.compile(r"^[A-Za-z]{3}$")
_IATA_REQUIRED_MESSAGE = "Kiwi MCP requires pre-resolved airport codes for this build."
_MISSING_ORIGIN_MESSAGE = "No origin was provided for this flight search."
_ZERO_ADULTS_MESSAGE = "Kiwi MCP requires at least one adult traveler for a flight search."


def _schema_properties(tool_schema: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(tool_schema, dict):
        return {}
    properties = tool_schema.get("properties")
    return properties if isinstance(properties, dict) else {}


def _schema_accepts_free_form_places(properties: dict[str, Any]) -> bool:
    """Reads only the schema's own `flyFrom` property description
    (already-returned server metadata, never LLM prose) to decide whether
    this tool documents accepting a free-form place name -- the live
    schema discovered during this step says "IATA code, city or airport
    name". Defaults to `False` (the more conservative, IATA-code-required
    assumption) if this can't be determined from the schema text, so a
    future schema that removes this wording never silently starts sending
    unresolved city names.
    """
    description = str(properties.get(_FLY_FROM_FIELD, {}).get("description", "")).lower()
    return "city" in description or "airport name" in description


def _looks_like_iata_code(value: str) -> bool:
    return bool(_IATA_CODE_PATTERN.fullmatch(value.strip()))


def build_search_flight_arguments(
    request: FlightSearchRequest, tool_schema: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, str | None]:
    """Builds `search-flight` tool arguments strictly from
    `FlightSearchRequest` fields and the tool's own discovered schema.

    Returns `(arguments, None)` when a safe call can be built, or `(None,
    message)` naming the honest reason it can't -- never a fuzzy/guessed
    airport code, never an empty/placeholder argument, and never a
    fabricated field. `departure_date`/`return_date` are reformatted to
    the schema's own documented `dd/mm/yyyy` format -- a direct
    reformatting of a date this request already carries, not a computed
    or estimated date.
    """
    properties = _schema_properties(tool_schema)
    missing_fields = [field for field in _REQUIRED_ARGUMENT_FIELDS if field not in properties]
    if missing_fields:
        return None, (
            "Kiwi MCP's search-flight tool schema no longer declares the expected "
            f"field(s) {', '.join(missing_fields)}; refusing to guess a different shape."
        )

    if not request.origin:
        return None, _MISSING_ORIGIN_MESSAGE

    if not _schema_accepts_free_form_places(properties) and not (
        _looks_like_iata_code(request.origin) and _looks_like_iata_code(request.destination)
    ):
        return None, _IATA_REQUIRED_MESSAGE

    if request.adults < 1:
        return None, _ZERO_ADULTS_MESSAGE

    arguments: dict[str, Any] = {
        _FLY_FROM_FIELD: request.origin,
        _FLY_TO_FIELD: request.destination,
        _DEPARTURE_DATE_FIELD: request.departure_date.strftime("%d/%m/%Y"),
        _ADULTS_FIELD: request.adults,
        _CHILDREN_FIELD: request.children,
    }
    if request.return_date is not None and _RETURN_DATE_FIELD in properties:
        arguments[_RETURN_DATE_FIELD] = request.return_date.strftime("%d/%m/%Y")
    if request.currency is not None and _CURRENCY_FIELD in properties:
        arguments[_CURRENCY_FIELD] = request.currency
    if (
        request.cabin_class is not None
        and _CABIN_CLASS_FIELD in properties
        and request.cabin_class in _KIWI_CABIN_CLASS_VALUES
    ):
        arguments[_CABIN_CLASS_FIELD] = request.cabin_class

    return arguments, None


class KiwiMcpFlightProvider(FlightInventoryProvider):
    """`FlightInventoryProvider` backed by Kiwi's hosted MCP server (Step
    178B: tool discovery; Step 178C: real flight-search invocation).

    Disabled by default (`Settings.kiwi_mcp_enabled=False`) -- selecting
    `flight_provider="kiwi_mcp"` alone never causes a network call.
    """

    provider_name = "kiwi_mcp_flight_provider"

    def __init__(self, client: KiwiMcpClient | None = None) -> None:
        self._client = client

    def _resolve_client(self, settings: Settings) -> KiwiMcpClient:
        return self._client or KiwiMcpClient(
            endpoint=settings.kiwi_mcp_endpoint,
            timeout_seconds=settings.kiwi_mcp_timeout_seconds,
        )

    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResult:
        settings = get_settings()

        if not settings.kiwi_mcp_enabled:
            return self._empty_result(request, FlightSearchStatus.NOT_CONNECTED, _NOT_ENABLED_MESSAGE)

        client = self._resolve_client(settings)
        discovery_result = client.discover_tools()

        if discovery_result.status == KiwiMcpDiscoveryStatus.FAILED:
            return self._empty_result(
                request,
                FlightSearchStatus.FAILED,
                _DISCOVERY_FAILED_MESSAGE_TEMPLATE.format(
                    detail=discovery_result.message or "unknown error"
                ),
            )

        flight_tool = _find_flight_search_tool(discovery_result.tools, settings.kiwi_mcp_tool_name)
        if flight_tool is None:
            return self._empty_result(request, FlightSearchStatus.UNAVAILABLE, _NO_FLIGHT_TOOL_MESSAGE)

        arguments, build_error_message = build_search_flight_arguments(
            request, flight_tool.input_schema
        )
        if arguments is None:
            return self._empty_result(
                request,
                FlightSearchStatus.UNAVAILABLE,
                build_error_message or "Kiwi MCP search arguments could not be safely built.",
            )

        # Calls exactly the one tool discovery classified as flight-search
        # -- never `feedback-to-devs` or any other discovered tool.
        call_result = client.call_tool(flight_tool.name, arguments)
        return parse_kiwi_mcp_search_result(call_result, request)

    def _empty_result(
        self,
        request: FlightSearchRequest,
        status: FlightSearchStatus,
        message: str,
    ) -> FlightSearchResult:
        return FlightSearchResult(
            provider=self.provider_name,
            status=status,
            offers=[],
            message=message,
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            adults=request.adults,
            children=request.children,
            currency=request.currency,
        )
