from __future__ import annotations

from typing import Any

from app.models.common import ProviderCoverage, ProviderStatusEntry
from app.models.providers import ProviderResponse
from app.models.routing import RouteRequest, RouteResult
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
