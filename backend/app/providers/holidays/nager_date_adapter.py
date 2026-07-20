from __future__ import annotations

import logging
from datetime import date as date_cls
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.common import DataStatus, ProviderStatus
from app.models.providers import NormalizedHoliday, ProviderResponse
from app.providers.base import HolidayProvider, failed_response, unavailable_response

logger = logging.getLogger(__name__)

_USER_AGENT = "TravelObligator/0.1 (dev; legit-data-only)"
_REQUEST_TIMEOUT_SECONDS = 15.0

# Small, deterministic, conservative destination -> ISO 3166-1 alpha-2
# country code mapping for common country names/cities already used in
# tests/demo. Matched only against the last comma-separated segment of the
# destination (the conventional "City, Country" format) or the whole
# destination when there's no comma -- never a loose substring match, so
# this never guesses a country from an unrelated word.
_COUNTRY_CODE_BY_NAME: dict[str, str] = {
    "portugal": "PT",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "france": "FR",
    "spain": "ES",
    "italy": "IT",
    "united kingdom": "GB",
    "uk": "GB",
}


def infer_country_code(destination: str) -> str | None:
    """Conservatively infer an ISO 3166-1 alpha-2 country code from
    `destination`, using only a small deterministic mapping of common
    country names (docs/12_provider_architecture.md section 16). No LLM, no
    fuzzy matching, no substring guessing: only an exact match against the
    last comma-separated segment (the conventional "City, Country" format)
    or the whole destination when there's no comma. Returns None (never a
    guessed code) when the country can't be determined this way.
    """
    normalized = destination.strip().lower()
    if not normalized:
        return None

    segments = [part.strip() for part in normalized.split(",") if part.strip()]
    candidate = segments[-1] if segments else normalized
    return _COUNTRY_CODE_BY_NAME.get(candidate)


class NagerDateHolidaysAdapter(HolidayProvider):
    """HolidayProvider backed by the free Nager.Date public holidays API
    (docs/07_production_data_sources.md, docs/12_provider_architecture.md
    section 16).

    Only `get_public_holidays` is implemented; `get_city_events` falls back
    to the base class's honest `not_connected` response since Nager.Date
    only supplies public holidays, not events.

    `get_public_holidays`'s first argument is the raw trip destination
    (e.g. "Lisbon, Portugal"), not a pre-resolved country code -- this
    adapter infers a country code itself via `infer_country_code`, a small
    conservative deterministic mapping (never an LLM, never a fuzzy/
    substring guess). If no country code can be inferred, this honestly
    reports holidays as unavailable rather than guessing a country.

    Fetches public holidays for every calendar year the trip's date range
    touches (Nager.Date's API is per-year), then keeps only the holidays
    whose date actually falls within `dates["start_date"]`..
    `dates["end_date"]`. If the provider has usable holiday data for the
    relevant year(s) but none of it happens to fall inside the trip's date
    range, this still succeeds with an empty list -- the provider was
    reached and returned real data, it just doesn't overlap this trip.

    Only fields Nager.Date's response actually returns are normalized into
    `NormalizedHoliday`: date, local name, name, country code, whether it's
    a global holiday, counties, and holiday types. No closures, crowds,
    opening hours, events, festivals, strikes, or risk assessment is ever
    invented -- Nager.Date does not supply any of that.

    If the request itself fails (network error, timeout, non-2xx status)
    for any requested year, this reports `failed`. If no country code can
    be inferred, or every requested year comes back with no usable data,
    this reports `unavailable` instead -- both are honest; neither invents
    data.
    """

    provider_name = "nager_date"

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.nager_date_api_url

    def get_public_holidays(
        self, destination: str, dates: dict[str, Any]
    ) -> ProviderResponse[Any]:
        field_name = "public_holidays"

        country_code = infer_country_code(destination)
        if country_code is None:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"Could not conservatively infer a country from '{destination}', "
                    "so Nager.Date public holidays cannot be requested."
                ),
            )

        start_date_str = dates.get("start_date")
        end_date_str = dates.get("end_date")
        if not start_date_str or not end_date_str:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message="Trip start/end dates are required to request Nager.Date holidays.",
            )

        try:
            start_date = date_cls.fromisoformat(start_date_str)
            end_date = date_cls.fromisoformat(end_date_str)
        except ValueError:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"Trip start/end dates are not valid ISO dates: "
                    f"{start_date_str}..{end_date_str}."
                ),
            )

        years = sorted({start_date.year, end_date.year})

        try:
            all_holidays: list[NormalizedHoliday] = []
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT}
            ) as client:
                for year in years:
                    response = client.get(
                        f"{self._base_url}/api/v3/PublicHolidays/{year}/{country_code}"
                    )
                    response.raise_for_status()
                    payload = response.json()
                    all_holidays.extend(self._normalize(payload, country_code))
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "Nager.Date request failed for %s (%s): %s", destination, country_code, exc
            )
            return failed_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=f"Nager.Date request failed for '{destination}' ({country_code}).",
            )

        if not all_holidays:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"Nager.Date returned no usable public holiday data for "
                    f"{country_code} in {years}."
                ),
            )

        in_range_holidays = [
            holiday for holiday in all_holidays if start_date <= holiday.date <= end_date
        ]

        # The provider genuinely has data for the relevant year(s), even if
        # none of it falls inside this trip's specific date range -- that is
        # still a successful, usable response, not an unavailable one.
        return ProviderResponse[list[NormalizedHoliday]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=in_range_holidays,
            confidence=0.6,
            message=(
                f"{len(in_range_holidays)} public holiday(s) found within the trip date "
                f"range via Nager.Date for {country_code} (out of {len(all_holidays)} "
                f"found for {years})."
            ),
        )

    def _normalize(self, payload: Any, country_code: str) -> list[NormalizedHoliday]:
        if not isinstance(payload, list):
            return []

        holidays: list[NormalizedHoliday] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            raw_date = entry.get("date")
            name = entry.get("name")
            if not raw_date or not name:
                continue
            try:
                parsed_date = date_cls.fromisoformat(str(raw_date))
            except ValueError:
                continue

            holidays.append(
                NormalizedHoliday(
                    date=parsed_date,
                    local_name=entry.get("localName") or name,
                    name=name,
                    country_code=entry.get("countryCode") or country_code,
                    is_global=bool(entry.get("global", True)),
                    counties=list(entry.get("counties") or []),
                    types=list(entry.get("types") or []),
                    source=self.provider_name,
                    data_status=DataStatus.LIVE,
                )
            )

        return holidays
