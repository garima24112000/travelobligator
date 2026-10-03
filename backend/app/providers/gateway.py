from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.models.accommodation import AccommodationSearchRequest, AccommodationSearchResult
from app.models.common import ProviderCoverage, ProviderStatusEntry
from app.models.flight import FlightSearchRequest, FlightSearchResult
from app.models.providers import ProviderResponse
from app.models.routing import RouteRequest, RouteResult, RoutingProfile
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
from app.providers.places.factory import get_places_provider
from app.providers.routing.base import RoutingProvider
from app.providers.routing.factory import get_routing_provider
from app.core import performance
from app.core.provider_usage import GenerationProviderContext
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
# Section 1B: the gateway operations whose provider is OPTIONAL in V1, and
# the statuses that only mean "nothing to offer" for them.
_OPTIONAL_INVENTORY_STAGES = frozenset({"accommodations", "flights"})
_EXPECTED_NO_DATA_STATUSES = frozenset({"unavailable", "not_connected"})

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


_KNOWN_OUTCOMES = frozenset(
    {"success", "returned", "failed", "not_connected", "unavailable", "partial", "retrying", "fallback_used", "not_requested"}
)


def _outcome_label(status: str) -> str:
    """A fixed low-cardinality outcome label (anything unrecognised -> `other`)."""
    return status if status in _KNOWN_OUTCOMES else "other"


def _record_provider_metrics(provider: str, stage: str, status: str, duration_ms: float | None = None) -> None:
    """Section 200D: safe dimensions only -- provider name (a fixed class attribute), stage (a fixed
    label) and outcome. Never coordinates, queries, dates, destinations or payloads."""
    from app.core.metrics import registry

    stage_label = stage if _SAFE_PROVIDER_NAME_PATTERN.match(stage or "") else "unknown"
    registry.inc(
        "travelobligator_provider_calls_total",
        {"provider": provider, "stage": stage_label, "outcome": _outcome_label(status)},
    )
    if duration_ms is not None:
        registry.observe(
            "travelobligator_provider_call_duration_seconds",
            duration_ms / 1000.0,
            {"provider": provider, "stage": stage_label},
        )


def _log_provider_call(*, provider: object, stage: str, status: str, duration_ms: float) -> None:
    provider_label = _safe_provider_name(provider)
    _record_provider_metrics(provider_label, stage, status, duration_ms)
    outcome = _outcome_label(status)
    # Section 1B: an optional inventory provider that has nothing to offer
    # (no bookable accommodation/flight inventory is connected in V1) is an
    # expected outcome, not a failure. It is still logged, with its real
    # status, but at INFO. Every other non-success -- failed, partial, a
    # routing provider that is unavailable -- stays a WARNING. Logging only:
    # the result the caller receives is never changed here.
    expected_no_data = stage in _OPTIONAL_INVENTORY_STAGES and status in _EXPECTED_NO_DATA_STATUSES
    extra: dict[str, object] = {
        "event": (
            "provider.success"
            if status in _INFO_LEVEL_STATUSES
            else "provider.no_data"
            if expected_no_data
            else "provider.failure"
        ),
        "provider": provider_label,
        "stage": stage,
        "operation": stage,
        "status": status,
        "outcome": outcome,
        "duration_ms": round(duration_ms, 3),
    }
    error_code = _provider_error_code(status)
    if error_code is not None:
        extra["error_code"] = error_code
        extra["error_kind"] = status

    # The message itself names provider, operation and status (fixed, bounded
    # identifiers only -- never a URL, a key, a query, a payload or a prompt).
    arguments = (provider_label, stage, status)
    if status in _INFO_LEVEL_STATUSES:
        logger.info("Provider call completed (provider=%s, operation=%s, status=%s).", *arguments, extra=extra)
    elif expected_no_data:
        logger.info(
            "Optional provider returned no data (provider=%s, operation=%s, status=%s).", *arguments, extra=extra
        )
    else:
        logger.warning("Provider call failed (provider=%s, operation=%s, status=%s).", *arguments, extra=extra)


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
        self.places = places or get_places_provider()
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

    # -- Section 203C.2B: explicit per-generation provider views ---------------------
    #
    # A generation's `GenerationProviderContext` is passed in by the caller
    # and handed to the provider itself (`bound_to`), which returns a view
    # that charges that generation's usage tracker. Nothing here reads a
    # global: with `provider_context=None` the process-level provider is
    # returned unchanged (and a credit-spending provider refuses to run
    # unaccounted in production).

    def places_for(self, provider_context: GenerationProviderContext | None = None) -> PlacesProvider:
        bind = getattr(self.places, "bound_to", None)
        return bind(provider_context) if callable(bind) and provider_context is not None else self.places

    def routing_for(self, provider_context: GenerationProviderContext | None = None) -> RoutingProvider:
        bind = getattr(self.routing, "bound_to", None)
        return bind(provider_context) if callable(bind) and provider_context is not None else self.routing

    def get_route_sequence(
        self,
        points: list[tuple[float, float]],
        provider_context: GenerationProviderContext | None = None,
    ) -> list[RouteResult]:
        """Real routes for the consecutive legs of ONE day's ordered stops
        (`(lat, lon)` pairs) -- a single provider request when the routing
        provider supports several waypoints. Never an all-pairs lookup."""
        provider = self.routing_for(provider_context)
        provider_name = getattr(provider, "provider_name", None)
        # Section 1A (measurement only): the stage's wall-clock, and how often
        # the same day route was asked for in this generation.
        performance.note_request("route_sequence_lookup", points)
        started_at = time.monotonic()
        with performance.stage("walking_routing"):
            results = provider.get_route_sequence(points)
        duration_ms = (time.monotonic() - started_at) * 1000
        statuses = {_provider_status(result) for result in results}
        _log_provider_call(
            provider=provider_name,
            stage="routing",
            status=statuses.pop() if len(statuses) == 1 else "partial",
            duration_ms=duration_ms,
        )
        return results

    def get_route_sequences(
        self,
        days: list[list[tuple[float, float]]],
        provider_context: GenerationProviderContext | None = None,
    ) -> list[list[RouteResult] | Exception]:
        """`get_route_sequence` for several INDEPENDENT days (Section 1B):
        one entry per day, in the order given -- that day's leg results, or
        the exception its lookup raised (which never affects another day).
        A routing provider that can fetch several sequences as one bounded
        concurrent batch is asked to; any other is called day by day."""
        provider = self.routing_for(provider_context)
        provider_name = getattr(provider, "provider_name", None)
        for points in days:
            performance.note_request("route_sequence_lookup", points)
        started_at = time.monotonic()
        with performance.stage("walking_routing"):
            results = self._route_sequences(provider, days, None)
        duration_ms = (time.monotonic() - started_at) * 1000
        for result in results:
            statuses = {"failed"} if isinstance(result, Exception) else {_provider_status(leg) for leg in result}
            _log_provider_call(
                provider=provider_name,
                stage="routing",
                status=statuses.pop() if len(statuses) == 1 else "partial",
                duration_ms=duration_ms,
            )
        return results

    def get_alternate_mode_routes(
        self,
        legs: list[tuple[tuple[float, float], tuple[float, float]]],
        provider_context: GenerationProviderContext | None = None,
    ) -> list[RouteResult | Exception | None]:
        """`get_alternate_mode_route` for several INDEPENDENT legs (Section
        1B): one entry per `(origin, destination)` leg, in the order given
        -- its driving result, None when the provider offers no second mode,
        or the exception its lookup raised."""
        provider = self.routing_for(provider_context)
        if not getattr(provider, "supports_alternate_mode", False):
            return [None] * len(legs)
        for origin, destination in legs:
            performance.note_request("alternate_mode_lookup", origin, destination)
        started_at = time.monotonic()
        with performance.stage("alternate_mode_routing"):
            sequences = self._route_sequences(
                provider, [[origin, destination] for origin, destination in legs], RoutingProfile.DRIVING
            )
        duration_ms = (time.monotonic() - started_at) * 1000
        results: list[RouteResult | Exception | None] = []
        for sequence in sequences:
            result = sequence if isinstance(sequence, Exception) else (sequence[0] if sequence else None)
            _log_provider_call(
                provider=getattr(provider, "provider_name", None),
                stage="routing",
                status=(
                    "failed"
                    if isinstance(result, Exception)
                    else _provider_status(result)
                    if result is not None
                    else "unavailable"
                ),
                duration_ms=duration_ms,
            )
            results.append(result)
        return results

    @staticmethod
    def _route_sequences(
        provider: RoutingProvider,
        sequences: list[list[tuple[float, float]]],
        profile: RoutingProfile | None,
    ) -> list[list[RouteResult] | Exception]:
        batch = getattr(provider, "get_route_sequences", None)
        if callable(batch) and len(sequences) > 1:
            try:
                return list(batch(sequences, profile))
            except Exception as exc:  # noqa: BLE001 - reported per sequence, as a per-call failure would be
                return [exc] * len(sequences)
        results: list[list[RouteResult] | Exception] = []
        for points in sequences:
            try:
                results.append(
                    provider.get_route_sequence(points)
                    if profile is None
                    else provider.get_route_sequence(points, profile)
                )
            except Exception as exc:  # noqa: BLE001 - reported for this sequence only
                results.append(exc)
        return results

    def get_alternate_mode_route(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        provider_context: GenerationProviderContext | None = None,
    ) -> RouteResult | None:
        """ONE driving route for ONE leg (Section 203C.2B, mixed-mode
        transfers) -- asked only for a leg whose walking route is too long.
        None when the routing provider does not offer a second mode; never
        a matrix, never a search over modes."""
        provider = self.routing_for(provider_context)
        if not getattr(provider, "supports_alternate_mode", False):
            return None
        performance.note_request("alternate_mode_lookup", origin, destination)
        started_at = time.monotonic()
        with performance.stage("alternate_mode_routing"):
            results = provider.get_route_sequence([origin, destination], RoutingProfile.DRIVING)
        duration_ms = (time.monotonic() - started_at) * 1000
        result = results[0] if results else None
        _log_provider_call(
            provider=getattr(provider, "provider_name", None),
            stage="routing",
            status=_provider_status(result) if result is not None else "unavailable",
            duration_ms=duration_ms,
        )
        return result

    def get_route(
        self, request: RouteRequest, provider_context: GenerationProviderContext | None = None
    ) -> RouteResult:
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
        routing = self.routing_for(provider_context)
        provider_name = getattr(routing, "provider_name", None)
        performance.note_request(
            "route_leg_lookup",
            request.origin_lat, request.origin_lon, request.destination_lat, request.destination_lon,
        )
        started_at = time.monotonic()
        with performance.stage("walking_routing"):
            result = routing.get_route(request)
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

        # Section 200D: the shared point where places/weather/holiday/currency responses are
        # normalised -- count the outcome (no duration is available here).
        try:
            status_value = getattr(response.status, "value", response.status)
            _record_provider_metrics(
                _safe_provider_name(response.provider_name),
                _safe_provider_name(getattr(response.provider_type, "value", "unknown")),
                str(status_value),
            )
        except Exception:  # noqa: BLE001 - metrics never break normalisation
            pass
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
