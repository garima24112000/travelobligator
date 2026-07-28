from __future__ import annotations

from app.models.planning_state import PlanningState, TravelGroupType, TripPace, TripRequest
from app.providers.base import (
    CurrencyProvider,
    HolidayProvider,
    PlacesProvider,
    RoutesProvider,
    WeatherProvider,
    unavailable_response,
)
from app.providers.gateway import ProviderGateway
from app.services.destination_context_service import DestinationContextService

# ---------------------------------------------------------------------------
# Step 155C: destination string preservation
# ---------------------------------------------------------------------------
#
# Spy provider doubles that record every destination string they were
# actually called with, then honestly report `unavailable` -- never real
# network calls, never invented data. Used to prove the full destination
# string reaches every provider call site unchanged (never truncated, e.g.
# "New York" -> "New").


class _SpyPlacesProvider(PlacesProvider):
    provider_name = "spy_places"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def search_attractions(self, destination, filters=None):
        self.calls.append(destination)
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["attractions"]
        )

    def search_restaurants(self, area, filters=None):
        self.calls.append(area)
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["restaurants"]
        )

    def search_accommodation_pois(self, destination, filters=None):
        self.calls.append(destination)
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["accommodation_pois"]
        )

    def search_must_visit_place(self, must_visit_term, primary_destination, filters=None):
        self.calls.append(primary_destination)
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["must_visit_place"]
        )

    def resolve_coordinates(self, destination):
        self.calls.append(destination)
        return None


class _SpyHolidayProvider(HolidayProvider):
    provider_name = "spy_holiday"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_public_holidays(self, destination, dates):
        self.calls.append(destination)
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["public_holidays"]
        )


class _SpyCurrencyProvider(CurrencyProvider):
    provider_name = "spy_currency"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_exchange_rate(self, base_currency, destination):
        self.calls.append(destination)
        return unavailable_response(
            self.provider_name, self.provider_type, unavailable_fields=["exchange_rate"]
        )


def _trip_request(**overrides) -> TripRequest:
    fields = {
        "primary_destination": "New York",
        "origin_city": "Seattle",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": TravelGroupType.COUPLE,
        "pace": TripPace.BALANCED,
        "must_visit": ["Empire State Building"],
    }
    fields.update(overrides)
    return TripRequest(**fields)


def test_full_destination_string_reaches_every_provider_call_unchanged() -> None:
    """Requirement 1: "New York" must stay "New York" -- never truncated to
    "New" -- at every provider call site `DestinationContextService`
    reaches through `ProviderGateway` (attractions, restaurants,
    accommodation POIs, the must-visit targeted lookup, coordinate
    resolution for weather, holidays, and currency).
    """
    spy_places = _SpyPlacesProvider()
    spy_holiday = _SpyHolidayProvider()
    spy_currency = _SpyCurrencyProvider()
    gateway = ProviderGateway(
        places=spy_places,
        routes=RoutesProvider(),
        weather=WeatherProvider(),
        holiday=spy_holiday,
        currency=spy_currency,
    )
    service = DestinationContextService(gateway=gateway)
    planning_state = PlanningState(trip_request=_trip_request())

    service.run(planning_state)

    # search_attractions, search_restaurants, search_accommodation_pois,
    # resolve_coordinates (for weather), and search_must_visit_place (since
    # "Empire State Building" was never matched, given the spy returns no
    # candidates) -- five calls, every one with the untruncated string.
    assert len(spy_places.calls) == 5
    assert all(call == "New York" for call in spy_places.calls)
    assert "New" not in spy_places.calls

    assert spy_holiday.calls == ["New York"]
    assert spy_currency.calls == ["New York"]

    # The trip request itself was never mutated either.
    assert planning_state.trip_request.primary_destination == "New York"
