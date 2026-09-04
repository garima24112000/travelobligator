from __future__ import annotations

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
        """
        return self.routing.get_route(request)

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
        """
        return self.accommodation_inventory.search_accommodations(request)

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
        """
        return self.flight_inventory.search_flights(request)

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
