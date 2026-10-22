"""M2 Phase 1 (D1/D5): `DestinationContextService` reads the destination's
ISO country code from the places provider's cached resolution (one
`describe_destination` call, no extra geocoding request), passes it to the
holiday and currency lookups, and stores each missing-data reason on the
context. Fakes only -- no provider is called."""

from __future__ import annotations

from typing import Any

from app.models.common import DataStatus, ProviderStatus
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.models.providers import ProviderResponse, ProviderType
from app.providers import missing_data_reasons as reasons
from app.providers.base import RoutesProvider, failed_response, unavailable_response
from app.services.destination_context_service import DestinationContextService


def _state() -> PlanningState:
    return PlanningState(
        trip_request=TripRequest(
            primary_destination="Kyoto",
            start_date="2026-08-10",
            end_date="2026-08-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        )
    )


class _Places:
    provider_name = "fake_places"

    def __init__(self, address: dict[str, str] | None) -> None:
        self.address = address
        self.describe_calls = 0
        self.resolve_calls = 0

    def _empty(self) -> ProviderResponse[Any]:
        return unavailable_response(self.provider_name, ProviderType.PLACES, ["places"], "none")

    def search_attractions(self, destination: str, filters: Any = None) -> ProviderResponse[Any]:
        return self._empty()

    def search_restaurants(self, destination: str, filters: Any = None) -> ProviderResponse[Any]:
        return self._empty()

    def search_accommodation_pois(self, destination: str) -> ProviderResponse[Any]:
        return self._empty()

    def resolve_coordinates(self, destination: str) -> None:
        self.resolve_calls += 1
        return None

    def describe_destination(self, destination: str) -> dict[str, str] | None:
        self.describe_calls += 1
        return self.address


class _Holiday:
    provider_name = "fake_holiday"

    def __init__(self) -> None:
        self.kwargs: list[dict[str, Any]] = []

    def get_public_holidays(self, destination: str, dates: dict[str, Any], **kwargs: Any) -> ProviderResponse[Any]:
        self.kwargs.append(kwargs)
        response = unavailable_response(self.provider_name, ProviderType.HOLIDAY, ["public_holidays"], "none")
        response.failure_reason = reasons.COUNTRY_NOT_SUPPORTED
        return response


class _Currency:
    provider_name = "fake_currency"

    def __init__(self) -> None:
        self.kwargs: list[dict[str, Any]] = []

    def get_exchange_rate(self, base_currency: str, destination: str, **kwargs: Any) -> ProviderResponse[Any]:
        self.kwargs.append(kwargs)
        # A failed request whose adapter named no reason: the service
        # classifies it as `request_failed`.
        return failed_response(self.provider_name, ProviderType.CURRENCY, ["exchange_rate"], "down")


class _Weather:
    provider_name = "fake_weather"

    def get_weather_forecast(self, destination: str, dates: dict[str, Any], coordinates: Any = None):
        response = unavailable_response(self.provider_name, ProviderType.WEATHER, ["weather_forecast"], "none")
        response.failure_reason = reasons.COORDINATES_UNAVAILABLE
        return response


class _Gateway:
    def __init__(self, places: _Places) -> None:
        self.places = places
        self.routes = RoutesProvider()
        self.weather = _Weather()
        self.holiday = _Holiday()
        self.currency = _Currency()


def test_the_resolved_country_reaches_both_lookups_once() -> None:
    places = _Places({"display_name": "Kyoto, Japan", "city": "Kyoto", "country_code": "jp"})
    gateway = _Gateway(places)

    state = DestinationContextService(gateway=gateway).run(_state())  # type: ignore[arg-type]

    assert gateway.holiday.kwargs == [{"country_code": "JP"}]
    assert gateway.currency.kwargs == [{"country_code": "JP"}]
    assert places.describe_calls == 1  # moved, not added: no second resolution
    assert state.holiday_context.country_code == "JP"
    assert state.destination_context.resolved_destination["country_code"] == "jp"


def test_missing_reasons_are_stored_on_each_context() -> None:
    gateway = _Gateway(_Places({"display_name": "Kyoto, Japan", "country_code": "JP"}))

    state = DestinationContextService(gateway=gateway).run(_state())  # type: ignore[arg-type]

    assert state.weather_context.missing_reason == reasons.COORDINATES_UNAVAILABLE
    assert state.holiday_context.missing_reason == reasons.COUNTRY_NOT_SUPPORTED
    assert state.currency_context.missing_reason == reasons.REQUEST_FAILED
    assert state.currency_context.data_status == DataStatus.FAILED


def test_without_a_resolved_country_no_code_is_passed() -> None:
    gateway = _Gateway(_Places(None))

    state = DestinationContextService(gateway=gateway).run(_state())  # type: ignore[arg-type]

    # Adapters keep their own conservative name inference in this case.
    assert gateway.holiday.kwargs == [{}]
    assert gateway.currency.kwargs == [{}]
    assert state.holiday_context.country_code is None


def test_a_malformed_country_code_is_not_passed() -> None:
    gateway = _Gateway(_Places({"display_name": "Somewhere", "country_code": "J1"}))
    DestinationContextService(gateway=gateway).run(_state())  # type: ignore[arg-type]
    assert gateway.holiday.kwargs == [{}]


def test_data_present_means_no_missing_reason() -> None:
    from app.models.providers import NormalizedExchangeRate

    gateway = _Gateway(_Places({"display_name": "Kyoto, Japan", "country_code": "JP"}))

    class _OkCurrency(_Currency):
        def get_exchange_rate(self, base_currency: str, destination: str, **kwargs: Any) -> ProviderResponse[Any]:
            return ProviderResponse[NormalizedExchangeRate](
                provider_name=self.provider_name,
                provider_type=ProviderType.CURRENCY,
                status=ProviderStatus.SUCCESS,
                data_status=DataStatus.LIVE,
                data=NormalizedExchangeRate(
                    base_currency="USD",
                    destination_currency="JPY",
                    exchange_rate=150.0,
                    rate_date=None,
                    source=self.provider_name,
                    data_status=DataStatus.LIVE,
                ),
            )

    gateway.currency = _OkCurrency()
    state = DestinationContextService(gateway=gateway).run(_state())  # type: ignore[arg-type]
    assert state.currency_context.exchange_rate == 150.0
    assert state.currency_context.missing_reason is None
