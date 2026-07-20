from __future__ import annotations

from typing import Any

from app.models.common import ProviderCoverage, ProviderStatusEntry
from app.models.providers import ProviderResponse
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
