from __future__ import annotations

from typing import Any

from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import ProviderResponse, ProviderType


def not_connected_response(
    provider_name: str,
    provider_type: ProviderType,
    unavailable_fields: list[str] | None = None,
    message: str | None = None,
) -> ProviderResponse[Any]:
    return ProviderResponse[Any](
        provider_name=provider_name,
        provider_type=provider_type,
        status=ProviderStatus.NOT_CONNECTED,
        data_status=DataStatus.NOT_CONNECTED,
        data=None,
        unavailable_fields=unavailable_fields or [],
        confidence=0.0,
        message=message or f"{provider_type.value} provider is not connected.",
    )


def unavailable_response(
    provider_name: str,
    provider_type: ProviderType,
    unavailable_fields: list[str] | None = None,
    message: str | None = None,
) -> ProviderResponse[Any]:
    return ProviderResponse[Any](
        provider_name=provider_name,
        provider_type=provider_type,
        status=ProviderStatus.UNAVAILABLE,
        data_status=DataStatus.UNAVAILABLE,
        data=None,
        unavailable_fields=unavailable_fields or [],
        confidence=0.0,
        message=message or f"{provider_type.value} data is unavailable.",
    )


def failed_response(
    provider_name: str,
    provider_type: ProviderType,
    unavailable_fields: list[str] | None = None,
    message: str | None = None,
) -> ProviderResponse[Any]:
    return ProviderResponse[Any](
        provider_name=provider_name,
        provider_type=provider_type,
        status=ProviderStatus.FAILED,
        data_status=DataStatus.FAILED,
        data=None,
        unavailable_fields=unavailable_fields or [],
        confidence=0.0,
        message=message or f"{provider_type.value} provider request failed.",
    )


class BaseProvider:
    """Shared behavior for provider interfaces.

    Concrete adapters (e.g. an OpenStreetMap-backed PlacesProvider) should
    subclass one of the interfaces below and override its methods. Until an
    adapter is implemented, the interface's default methods honestly report
    that the provider is not connected rather than returning invented data.
    """

    provider_name: str = "not_connected"
    provider_type: ProviderType

    def not_connected(
        self,
        unavailable_fields: list[str] | None = None,
        message: str | None = None,
    ) -> ProviderResponse[Any]:
        return not_connected_response(
            self.provider_name, self.provider_type, unavailable_fields, message
        )


class PlacesProvider(BaseProvider):
    provider_name = "places_provider"
    provider_type = ProviderType.PLACES

    def search_places(
        self,
        destination: str,
        categories: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["places"])

    def get_place_details(self, place_id: str) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["place_details"])

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["restaurants"])

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["attractions"])

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["accommodation_pois"])

    def search_must_visit_place(
        self,
        must_visit_term: str,
        primary_destination: str,
        filters: dict[str, Any] | None = None,
    ) -> ProviderResponse[Any]:
        """Targeted lookup for one explicit must-visit place, used as a
        fallback when general attraction search misses it. Must never
        return an invented place; only a real, named, coordinate-backed
        provider result."""
        return self.not_connected(unavailable_fields=["must_visit_place"])

    def resolve_coordinates(self, destination: str) -> GeoPoint | None:
        """Best-effort geocode of `destination` for other providers/services
        that need real coordinates (e.g. WeatherProvider) without
        duplicating geocoding logic. Returns None (never a guessed
        coordinate) unless a concrete adapter overrides this."""
        return None


class RoutesProvider(BaseProvider):
    provider_name = "routes_provider"
    provider_type = ProviderType.ROUTES

    def get_route(
        self, origin: dict[str, Any], destination: dict[str, Any], mode: str
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["distance_km", "travel_time_minutes"])

    def get_route_matrix(
        self, origins: list[dict[str, Any]], destinations: list[dict[str, Any]], mode: str
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["route_matrix"])

    def estimate_walking_distance(
        self, origin: dict[str, Any], destination: dict[str, Any]
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["walking_distance_km"])

    def estimate_transit_feasibility(
        self, origin: dict[str, Any], destination: dict[str, Any], date_time: str | None = None
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["transit_feasibility"])


class TransitProvider(BaseProvider):
    provider_name = "transit_provider"
    provider_type = ProviderType.TRANSIT

    def get_transit_options(
        self, origin: dict[str, Any], destination: dict[str, Any], date_time: str | None = None
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["transit_options"])

    def get_nearby_transit_stops(self, location: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["nearby_transit_stops"])

    def check_transit_feasibility(
        self, area: str, destination_clusters: list[dict[str, Any]]
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["transit_feasibility"])


class AccommodationProvider(BaseProvider):
    provider_name = "accommodation_provider"
    provider_type = ProviderType.ACCOMMODATION

    def search_accommodation_options(
        self, destination: str, area: str | None = None, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["accommodation_options"])

    def get_accommodation_details(self, accommodation_id: str) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["accommodation_details"])

    def get_accommodation_price(
        self, accommodation_id: str, dates: dict[str, Any]
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["price"])

    def get_accommodation_availability(
        self, accommodation_id: str, dates: dict[str, Any]
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["availability"])


class FlightProvider(BaseProvider):
    provider_name = "flight_provider"
    provider_type = ProviderType.FLIGHT

    def search_flights(
        self,
        origin: str,
        destination: str,
        dates: dict[str, Any],
        travelers: int,
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["flight_options"])

    def get_flight_details(self, flight_id: str) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["flight_details"])


class WeatherProvider(BaseProvider):
    provider_name = "weather_provider"
    provider_type = ProviderType.WEATHER

    def get_weather_forecast(
        self,
        destination: str,
        dates: dict[str, Any],
        coordinates: GeoPoint | None = None,
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["weather_forecast"])

    def get_weather_alerts(self, destination: str, dates: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["weather_alerts"])


class HolidayProvider(BaseProvider):
    provider_name = "holiday_provider"
    provider_type = ProviderType.HOLIDAY

    def get_public_holidays(self, country: str, dates: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["public_holidays"])

    def get_city_events(self, destination: str, dates: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["city_events"])


class CurrencyProvider(BaseProvider):
    provider_name = "currency_provider"
    provider_type = ProviderType.CURRENCY

    def convert_currency(
        self, amount: float, from_currency: str, to_currency: str
    ) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["converted_amount"])

    def get_exchange_rate(self, from_currency: str, to_currency: str) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["exchange_rate"])


class AIReasoningProvider(BaseProvider):
    provider_name = "ai_reasoning_provider"
    provider_type = ProviderType.AI_REASONING

    def generate_traveler_profile(self, input: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["traveler_profile"])

    def generate_trip_strategy(self, input: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["trip_strategy"])

    def generate_decision_card(self, input: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["decision_card"])

    def generate_experience_explanation(self, input: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["experience_explanation"])

    def generate_validation_reasoning(self, input: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["validation_reasoning"])

    def interpret_feedback(self, input: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["feedback_interpretation"])

    def summarize_change(self, input: dict[str, Any]) -> ProviderResponse[Any]:
        return self.not_connected(unavailable_fields=["change_summary"])
