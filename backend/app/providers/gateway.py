from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchResult
from app.models.common import ProviderCoverage, ProviderStatusEntry
from app.models.flight import FlightSearchRequest, FlightSearchResult
from app.models.providers import ProviderResponse
from app.models.routing import RouteRequest, RouteResult
from app.providers.accommodation.base import AccommodationInventoryProvider
from app.providers.accommodation.factory import get_accommodation_provider
from app.providers.base import (
    AccommodationProvider,
    AIReasoningProvider,
    CurrencyProvider,
    FlightProvider,
    HolidayProvider,
    PlacesProvider,
    RoutesProvider,
    TransitProvider,
    WeatherProvider,
)
from app.providers.currency.frankfurter_adapter import FrankfurterCurrencyAdapter
from app.providers.flights.base import FlightInventoryProvider
from app.providers.flights.factory import get_flight_provider
from app.providers.holidays.nager_date_adapter import NagerDateHolidaysAdapter
from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter
from app.providers.routing.base import RoutingProvider
from app.providers.routing.factory import get_routing_provider
from app.providers.weather.open_meteo_adapter import OpenMeteoWeatherAdapter

logger = logging.getLogger(__name__)

# Step 187F (docs/14_backend_architecture.md section 125): one safe,
# structured summary log line per call to this gateway's three real
# central dispatch methods (`get_route`/`search_accommodations`/
# `search_flights` -- the only gateway methods any service actually
# calls; `places`/`weather`/`holiday`/`currency` are reached via direct
# attribute access from `destination_context_service.py` and have no
# central dispatch point to wrap here, so they stay covered only by
# their own existing adapter-level `logger.warning(...)` calls, exactly
# as before this step -- see that doc section for why this is a
# deliberate, documented scope decision, not an oversight).
#
# Only ever logs safe, bounded identifiers: a provider's own short
# `provider_name` class attribute, a fixed stage name, the result's
# `status` enum value, a derived `error_code`, and a monotonic-clock
# `duration_ms`. Never the request (destination/dates/traveler counts/
# coordinates), never the result's `offers`/`geometry`/`message`, never
# a `PlanningState`, never a raw HTML/file path. Adds no new exception
# boundary -- if the underlying provider raises, it propagates out of
# these methods exactly as it did before this step, unlogged by the
# gateway (matching pre-187F behavior byte-for-byte).

_MAX_SAFE_PROVIDER_NAME_LENGTH = 64
_SAFE_PROVIDER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_:.-]+$")

# `success` (a real ProviderStatus/AccommodationSearchStatus/
# FlightSearchStatus/etc. value) and `returned` (this module's own
# fallback for a result shape with no recognizable `.status` attribute,
# used only when the call completed without raising) are the only two
# outcomes logged at `info` -- everything else (not_connected/
# unavailable/failed/partial/retrying/fallback_used/not_requested) is a
# real, honest non-success outcome and logged at `warning`, matching
# this codebase's own "never quietly treat degraded data as success"
# convention.
_INFO_LEVEL_STATUSES = frozenset({"success", "returned"})

# Maps a result's `status` value to one of this codebase's existing,
# already-safe `schemas.errors.ErrorCode` values -- never a new code
# invented for this step. Only ever used for the *log* line; the
# response a caller sees is completely unaffected either way.
_STATUS_TO_ERROR_CODE = {
    "failed": "PROVIDER_FAILED",
    "not_connected": "PROVIDER_NOT_CONNECTED",
    "unavailable": "DATA_UNAVAILABLE",
}


def _safe_provider_name(raw: object) -> str:
    """Returns `raw` unchanged if it is a short, plain, bounded string
    (letters/digits/`_`/`-`/`.`/`:` only -- matches this codebase's real
    `provider_name` class-attribute values, e.g. `"osrm"`/
    `"scraped_accommodation_provider"`), otherwise `"unknown"`. Never
    raises, never logs a URL/file path/arbitrary dynamic text even if a
    future provider's `provider_name` ever carried one by mistake.
    """
    if not isinstance(raw, str) or not raw:
        return "unknown"
    if len(raw) > _MAX_SAFE_PROVIDER_NAME_LENGTH:
        return "unknown"
    if not _SAFE_PROVIDER_NAME_PATTERN.match(raw):
        return "unknown"
    return raw


def _provider_status(result: object) -> str:
    """Extracts `result.status.value` (or `result.status` itself, if
    already a plain string) -- `"returned"` for any shape without a
    recognizable string-valued `.status` attribute. Never inspects any
    other field, and never serializes `result` itself.
    """
    status = getattr(result, "status", None)
    value = getattr(status, "value", status)
    if isinstance(value, str) and value:
        return value
    return "returned"


def _provider_error_code(status: str) -> str | None:
    return _STATUS_TO_ERROR_CODE.get(status)


def _log_provider_call(*, provider: object, stage: str, status: str, duration_ms: float) -> None:
    extra: dict[str, object] = {
        "provider": _safe_provider_name(provider),
        "stage": stage,
        "status": status,
        "duration_ms": round(duration_ms, 3),
    }
    error_code = _provider_error_code(status)
    if error_code is not None:
        extra["error_code"] = error_code

    if status in _INFO_LEVEL_STATUSES:
        logger.info("Provider call completed.", extra=extra)
    else:
        logger.warning("Provider call did not return success.", extra=extra)


class ProviderGateway:
    """Central access point for all external data providers.

    Planning services must call providers through this gateway rather than
    importing provider adapters directly (docs/12_provider_architecture.md,
    docs/14_backend_architecture.md section 18).

    Each provider slot defaults to its interface's base implementation,
    which honestly reports `not_connected` for every method, except
    `places` (OpenStreetMap-backed), `weather` (Open-Meteo-backed),
    `holiday` (Nager.Date-backed), and `currency` (Frankfurter-backed),
    which default to real adapters. Further real adapters can be injected
    later without changing calling code.

    `routing` (Step 165B, docs/12_provider_architecture.md section 31) is
    separate from the pre-existing, still-unused `routes` slot above (the
    generic `app.providers.base.RoutesProvider` stub interface, untouched
    by this step). `routing` defaults to whatever
    `app.providers.routing.factory.get_routing_provider()` resolves from
    `Settings.routing_provider` (`"not_connected"` by default, so this
    gateway makes no network call by default either) -- the gateway itself
    never knows an OSRM base URL, timeout, or profile default; those stay
    entirely inside the factory/adapter. **Not consumed by
    `PlanningOrchestrator`, `ExperiencePlannerService`, or
    `PlanValidatorService` yet** -- `get_route` exists on this gateway so a
    future step has one place to call, but nothing calls it yet.

    `accommodation_inventory` (Step 167C, docs/12_provider_architecture.md
    section 43) is separate from the pre-existing, still-unused
    `accommodation` slot above (the generic `app.providers.base.
    AccommodationProvider` stub interface, untouched by this step) --
    mirroring how `routing` was kept distinct from `routes`.
    `accommodation_inventory` defaults to whatever
    `app.providers.accommodation.factory.get_accommodation_provider()`
    resolves from `Settings.accommodation_provider` (`"not_connected"` by
    default, so this gateway makes no network call by default either) --
    the gateway itself has no lodging-provider-specific knowledge; that
    stays entirely inside the factory/adapter. **Not consumed by
    `PlanningOrchestrator`, `StayTransportService`, or
    `PlanValidatorService` yet** -- `search_accommodations` exists on this
    gateway so a future step has one place to call, but nothing calls it
    yet.

    `flight_inventory` (Step 169E, docs/12_provider_architecture.md
    section 55) is separate from the pre-existing, still-unused `flight`
    slot above (the generic `app.providers.base.FlightProvider` stub
    interface, untouched by this step) -- mirroring how
    `accommodation_inventory` was kept distinct from `accommodation`.
    `flight_inventory` defaults to whatever
    `app.providers.flights.factory.get_flight_provider()` resolves from
    `Settings.flight_provider` (`"scraped_local"` by default, matching
    Section 168F's accommodation default) -- the gateway itself has no
    flight-provider-specific knowledge; that stays entirely inside the
    factory/adapter. `search_flights` is consumed by
    `FlightInventoryService` (Step 169E), which `PlanningOrchestrator`
    calls from `run_stay_transport_stage` -- but the gateway itself still
    performs no guessing or fallback: with no local scraped-flight HTML
    file present, this returns an honest `unavailable` result with an
    empty `offers` list, without any network call.
    """

    def __init__(
        self,
        places: PlacesProvider | None = None,
        routes: RoutesProvider | None = None,
        transit: TransitProvider | None = None,
        accommodation: AccommodationProvider | None = None,
        flight: FlightProvider | None = None,
        weather: WeatherProvider | None = None,
        holiday: HolidayProvider | None = None,
        currency: CurrencyProvider | None = None,
        ai_reasoning: AIReasoningProvider | None = None,
        routing: RoutingProvider | None = None,
        accommodation_inventory: AccommodationInventoryProvider | None = None,
        flight_inventory: FlightInventoryProvider | None = None,
    ) -> None:
        self.places = places or OpenStreetMapPlacesAdapter()
        self.routes = routes or RoutesProvider()
        self.transit = transit or TransitProvider()
        self.accommodation = accommodation or AccommodationProvider()
        self.flight = flight or FlightProvider()
        self.weather = weather or OpenMeteoWeatherAdapter()
        self.holiday = holiday or NagerDateHolidaysAdapter()
        self.currency = currency or FrankfurterCurrencyAdapter()
        self.ai_reasoning = ai_reasoning or AIReasoningProvider()
        self.routing = routing or get_routing_provider()
        self.accommodation_inventory = accommodation_inventory or get_accommodation_provider()
        self.flight_inventory = flight_inventory or get_flight_provider()

    def get_route(self, request: RouteRequest) -> RouteResult:
        """Look up a point-to-point route through the configured routing
        provider (Step 165B). Delegates entirely to `self.routing` -- the
        gateway does not add, guess, or backfill any distance/duration
        itself, and never falls back to a straight-line (haversine)
        estimate. With the default `not_connected` routing provider (no
        `OSRM_BASE_URL` configured), this returns an honest `not_connected`
        `RouteResult` without any network call.

        Not called by `PlanningOrchestrator`, `ExperiencePlannerService`,
        or `PlanValidatorService` yet -- this method exists so a future
        step has one place to request route data through, matching how
        every other provider slot is used through this gateway.

        Step 187F: emits one safe structured log line (`stage="routing"`)
        describing the call's outcome -- see this module's own top-of-
        file note for exactly what is/isn't logged.
        """
        provider_name = getattr(self.routing, "provider_name", None)
        started_at = time.monotonic()
        result = self.routing.get_route(request)
        duration_ms = (time.monotonic() - started_at) * 1000
        _log_provider_call(
            provider=provider_name,
            stage="routing",
            status=_provider_status(result),
            duration_ms=duration_ms,
        )
        return result

    def search_accommodations(
        self, request: AccommodationSearchRequest
    ) -> AccommodationSearchResult:
        """Look up bookable accommodation inventory through the configured
        accommodation inventory provider (Step 167C). Delegates entirely to
        `self.accommodation_inventory` -- the gateway does not add, guess,
        or backfill any property, price, availability, rating, amenity,
        cancellation policy, or booking link itself, and never inspects
        `request.destination` to invent anything. With the default
        `not_connected` accommodation provider (`Settings.
        accommodation_provider` unset/unrecognized), this returns an
        honest `not_connected` `AccommodationSearchResult` with an empty
        `offers` list, without any network call.

        Not called by `PlanningOrchestrator`, `StayTransportService`, or
        `PlanValidatorService` yet -- this method exists so a future step
        has one place to request accommodation inventory through, matching
        how `get_route` was added ahead of routing being consumed
        (Step 165B).

        Step 187F: emits one safe structured log line
        (`stage="accommodations"`) describing the call's outcome -- see
        this module's own top-of-file note for exactly what is/isn't
        logged.
        """
        provider_name = getattr(self.accommodation_inventory, "provider_name", None)
        started_at = time.monotonic()
        result = self.accommodation_inventory.search_accommodations(request)
        duration_ms = (time.monotonic() - started_at) * 1000
        _log_provider_call(
            provider=provider_name,
            stage="accommodations",
            status=_provider_status(result),
            duration_ms=duration_ms,
        )
        return result

    def search_flights(self, request: FlightSearchRequest) -> FlightSearchResult:
        """Look up bookable flight inventory through the configured flight
        inventory provider (Step 169E). Delegates entirely to
        `self.flight_inventory` -- the gateway does not add, guess, or
        backfill any airline, flight number, airport, departure/arrival
        time, duration, price, availability, baggage policy, cancellation
        policy, or booking link itself, and never inspects
        `request.destination`/`request.origin` to invent anything. With
        the default `scraped_local` flight provider and no local HTML
        file present at `Settings.scraped_flight_html_path`'s default
        location, this returns an honest `unavailable`
        `FlightSearchResult` with an empty `offers` list, without any
        network call.

        Consumed by `FlightInventoryService.build_report` (Step 169E),
        called from `PlanningOrchestrator.run_stay_transport_stage`.

        Step 187F: emits one safe structured log line (`stage="flights"`)
        describing the call's outcome -- see this module's own
        top-of-file note for exactly what is/isn't logged.
        """
        provider_name = getattr(self.flight_inventory, "provider_name", None)
        started_at = time.monotonic()
        result = self.flight_inventory.search_flights(request)
        duration_ms = (time.monotonic() - started_at) * 1000
        _log_provider_call(
            provider=provider_name,
            stage="flights",
            status=_provider_status(result),
            duration_ms=duration_ms,
        )
        return result

    @staticmethod
    def to_status_entry(response: ProviderResponse[Any]) -> ProviderStatusEntry:
        """Normalize a provider response into a PlanningState provider_status entry."""

        return ProviderStatusEntry(
            provider_name=response.provider_name,
            provider_type=response.provider_type.value,
            status=response.status,
            data_status=response.data_status,
            fallback_used=response.fallback_used,
            fallback_provider=response.fallback_provider,
            unavailable_fields=response.unavailable_fields,
            error_message=response.message,
            confidence=response.confidence,
            retrieved_at=response.retrieved_at.isoformat(),
        )

    def default_provider_coverage(self) -> ProviderCoverage:
        """Coverage snapshot for a trip where no provider has been called yet."""

        return ProviderCoverage(
            places="not_connected",
            routes="not_connected",
            restaurants="not_connected",
            accommodations="not_connected",
            hotel_prices="not_connected",
            vacation_rentals="not_connected",
            airbnb="not_connected",
            flights="not_connected",
            weather="not_connected",
            holidays="not_connected",
            currency="not_connected",
        )


provider_gateway = ProviderGateway()
