from __future__ import annotations

import logging
from datetime import date as date_cls
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import NormalizedDailyWeather, ProviderResponse
from app.providers.base import WeatherProvider, failed_response, unavailable_response

logger = logging.getLogger(__name__)

_USER_AGENT = "TravelObligator/0.1 (dev; legit-data-only)"
_REQUEST_TIMEOUT_SECONDS = 15.0
_DAILY_FIELDS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
    "precipitation_sum",
    "weather_code",
)


class OpenMeteoWeatherAdapter(WeatherProvider):
    """WeatherProvider backed by the free Open-Meteo forecast API
    (docs/07_production_data_sources.md, docs/12_provider_architecture.md
    section 15).

    Only `get_weather_forecast` is implemented; `get_weather_alerts` falls
    back to the base class's honest `not_connected` response since the
    Open-Meteo forecast endpoint used here does not supply alerts.

    This adapter never geocodes on its own -- callers resolve real
    destination coordinates via the existing places/geocoding flow
    (`PlacesProvider.resolve_coordinates`, backed by
    `OpenStreetMapPlacesAdapter`) and pass them in, so geocoding logic is
    never duplicated. If no coordinates are available, this honestly
    reports weather as unavailable rather than guessing a location.

    Only fields Open-Meteo's daily forecast actually returns are normalized
    into `NormalizedDailyWeather`: max/min temperature, max precipitation
    probability, precipitation sum, and the raw numeric `weather_code`. No
    weather description/condition is invented from `weather_code` (this
    response has no text description field), and no humidity, UV, alert, or
    severe-weather value is ever fabricated.

    If the request itself fails (network error, timeout, non-2xx status),
    this reports `failed`. If the request succeeds but Open-Meteo reports an
    error for the requested range (e.g. a date range outside what the
    forecast endpoint supports) or returns no usable daily data, this
    reports `unavailable` instead -- both are honest; neither invents data.
    """

    provider_name = "open_meteo"

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.open_meteo_api_url

    def get_weather_forecast(
        self,
        destination: str,
        dates: dict[str, Any],
        coordinates: GeoPoint | None = None,
    ) -> ProviderResponse[Any]:
        field_name = "weather_forecast"

        if coordinates is None:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"No coordinates are available for '{destination}', so Open-Meteo "
                    "weather cannot be requested."
                ),
            )

        start_date = dates.get("start_date")
        end_date = dates.get("end_date")
        if not start_date or not end_date:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message="Trip start/end dates are required to request Open-Meteo weather.",
            )

        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT}
            ) as client:
                response = client.get(
                    f"{self._base_url}/v1/forecast",
                    params={
                        "latitude": coordinates.lat,
                        "longitude": coordinates.lng,
                        "start_date": start_date,
                        "end_date": end_date,
                        "daily": ",".join(_DAILY_FIELDS),
                        "timezone": "auto",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Open-Meteo request failed for %s: %s", destination, exc)
            return failed_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=f"Open-Meteo request failed for '{destination}'.",
            )

        if payload.get("error"):
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"Open-Meteo reported an error for '{destination}': "
                    f"{payload.get('reason') or 'unknown reason'}."
                ),
            )

        daily_weather = self._normalize(payload)
        if not daily_weather:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"Open-Meteo returned no usable daily forecast data for '{destination}'."
                ),
            )

        return ProviderResponse[list[NormalizedDailyWeather]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=daily_weather,
            confidence=0.6,
            message=(
                f"{len(daily_weather)} day(s) of daily forecast found via Open-Meteo "
                f"for '{destination}'."
            ),
        )

    def _normalize(self, payload: dict[str, Any]) -> list[NormalizedDailyWeather]:
        daily = payload.get("daily")
        if not isinstance(daily, dict):
            return []

        dates = daily.get("time")
        if not isinstance(dates, list) or not dates:
            return []

        temps_max = daily.get("temperature_2m_max") or []
        temps_min = daily.get("temperature_2m_min") or []
        precip_prob_max = daily.get("precipitation_probability_max") or []
        precip_sum = daily.get("precipitation_sum") or []
        weather_codes = daily.get("weather_code") or []

        results: list[NormalizedDailyWeather] = []
        for index, raw_date in enumerate(dates):
            try:
                parsed_date = date_cls.fromisoformat(str(raw_date))
            except ValueError:
                continue

            results.append(
                NormalizedDailyWeather(
                    date=parsed_date,
                    temperature_max_c=_float_at(temps_max, index),
                    temperature_min_c=_float_at(temps_min, index),
                    precipitation_probability_max=_float_at(precip_prob_max, index),
                    precipitation_sum_mm=_float_at(precip_sum, index),
                    weather_code=_int_at(weather_codes, index),
                    source=self.provider_name,
                    data_status=DataStatus.LIVE,
                )
            )

        return results


def _float_at(values: list[Any], index: int) -> float | None:
    if index >= len(values):
        return None
    value = values[index]
    return float(value) if value is not None else None


def _int_at(values: list[Any], index: int) -> int | None:
    if index >= len(values):
        return None
    value = values[index]
    return int(value) if value is not None else None
