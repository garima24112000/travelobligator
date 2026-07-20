from __future__ import annotations

import logging
from datetime import date as date_cls
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.common import DataStatus, ProviderStatus
from app.models.providers import NormalizedExchangeRate, ProviderResponse
from app.providers.base import CurrencyProvider, failed_response, unavailable_response

logger = logging.getLogger(__name__)

_USER_AGENT = "TravelObligator/0.1 (dev; legit-data-only)"
_REQUEST_TIMEOUT_SECONDS = 15.0

# Small, deterministic, conservative destination -> ISO 4217 currency code
# mapping for common country names/cities already used in tests/demo.
# Matched only against the last comma-separated segment of the destination
# (the conventional "City, Country" format) or the whole destination when
# there's no comma -- never a loose substring match, so this never guesses
# a currency from an unrelated word.
_CURRENCY_BY_COUNTRY_NAME: dict[str, str] = {
    "portugal": "EUR",
    "france": "EUR",
    "spain": "EUR",
    "italy": "EUR",
    "united states": "USD",
    "united states of america": "USD",
    "usa": "USD",
    "united kingdom": "GBP",
    "uk": "GBP",
    "india": "INR",
    "canada": "CAD",
}


def infer_destination_currency(destination: str) -> str | None:
    """Conservatively infer an ISO 4217 currency code for `destination`,
    using only a small deterministic mapping of common country names
    (docs/12_provider_architecture.md section 17). No LLM, no fuzzy
    matching, no substring guessing: only an exact match against the last
    comma-separated segment (the conventional "City, Country" format) or
    the whole destination when there's no comma. Returns None (never a
    guessed currency) when it can't be determined this way.
    """
    normalized = destination.strip().lower()
    if not normalized:
        return None

    segments = [part.strip() for part in normalized.split(",") if part.strip()]
    candidate = segments[-1] if segments else normalized
    return _CURRENCY_BY_COUNTRY_NAME.get(candidate)


class FrankfurterCurrencyAdapter(CurrencyProvider):
    """CurrencyProvider backed by the free Frankfurter exchange-rate API
    (docs/07_production_data_sources.md, docs/12_provider_architecture.md
    section 17).

    Only `get_exchange_rate` is implemented; `convert_currency` falls back
    to the base class's honest `not_connected` response since this adapter
    only supplies a single unit exchange rate, never a converted trip cost.

    `get_exchange_rate`'s second argument is the raw trip destination (e.g.
    "Lisbon, Portugal"), not a pre-resolved currency code -- this adapter
    infers a destination currency itself via `infer_destination_currency`,
    a small conservative deterministic mapping (never an LLM, never a
    fuzzy/substring guess). If no destination currency can be inferred,
    this honestly reports the exchange rate as unavailable rather than
    guessing a currency.

    If the destination currency equals `base_currency`, this returns a
    successful `exchange_rate=1.0` result without making any HTTP request
    -- no conversion is needed, and that fact is itself provider-backed
    (identity), not guessed.

    Only fields Frankfurter's response actually returns are normalized
    into `NormalizedExchangeRate`: the base/destination currency codes, the
    rate, and the rate date. No trip cost, budget, hotel price, restaurant
    price, attraction price, fee, tax, or total-cost value is ever
    invented -- Frankfurter does not supply any of that, and this adapter
    never calculates a trip total.

    If the request itself fails (network error, timeout, non-2xx status),
    this reports `failed`. If no destination currency can be inferred, or
    the response has no usable rate for the requested currency, this
    reports `unavailable` instead -- both are honest; neither invents
    data.
    """

    provider_name = "frankfurter"

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.frankfurter_api_url

    def get_exchange_rate(
        self, base_currency: str, destination: str
    ) -> ProviderResponse[Any]:
        field_name = "exchange_rate"
        base_currency = base_currency.strip().upper()

        destination_currency = infer_destination_currency(destination)
        if destination_currency is None:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"Could not conservatively infer a destination currency from "
                    f"'{destination}', so Frankfurter exchange rate cannot be requested."
                ),
            )

        if destination_currency == base_currency:
            # No conversion is needed -- this identity result is itself
            # honest/provider-backed, not a network call away from being
            # invented, so no HTTP request is made.
            return ProviderResponse[NormalizedExchangeRate](
                provider_name=self.provider_name,
                provider_type=self.provider_type,
                status=ProviderStatus.SUCCESS,
                data_status=DataStatus.LIVE,
                data=NormalizedExchangeRate(
                    base_currency=base_currency,
                    destination_currency=destination_currency,
                    exchange_rate=1.0,
                    rate_date=None,
                    source=self.provider_name,
                    data_status=DataStatus.LIVE,
                ),
                confidence=0.6,
                message=(
                    f"Destination currency for '{destination}' matches the base "
                    f"currency ({base_currency}); no conversion is needed."
                ),
            )

        try:
            with httpx.Client(
                timeout=_REQUEST_TIMEOUT_SECONDS,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                response = client.get(
                    f"{self._base_url}/latest",
                    params={"from": base_currency, "to": destination_currency},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "Frankfurter request failed for %s -> %s: %s",
                base_currency,
                destination_currency,
                exc,
            )
            return failed_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"Frankfurter request failed for {base_currency} -> "
                    f"{destination_currency}."
                ),
            )

        exchange_rate = self._normalize(payload, destination_currency)
        if exchange_rate is None:
            return unavailable_response(
                self.provider_name,
                self.provider_type,
                unavailable_fields=[field_name],
                message=(
                    f"Frankfurter returned no usable exchange rate for "
                    f"{base_currency} -> {destination_currency}."
                ),
            )

        return ProviderResponse[NormalizedExchangeRate](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=exchange_rate,
            confidence=0.6,
            message=(
                f"Exchange rate found via Frankfurter for {base_currency} -> "
                f"{destination_currency}."
            ),
        )

    def _normalize(
        self, payload: Any, destination_currency: str
    ) -> NormalizedExchangeRate | None:
        if not isinstance(payload, dict):
            return None

        base = payload.get("base")
        rates = payload.get("rates")
        if not isinstance(base, str) or not isinstance(rates, dict):
            return None

        rate = rates.get(destination_currency)
        if not isinstance(rate, (int, float)):
            return None

        rate_date: date_cls | None = None
        raw_date = payload.get("date")
        if isinstance(raw_date, str):
            try:
                rate_date = date_cls.fromisoformat(raw_date)
            except ValueError:
                rate_date = None

        return NormalizedExchangeRate(
            base_currency=base,
            destination_currency=destination_currency,
            exchange_rate=float(rate),
            rate_date=rate_date,
            source=self.provider_name,
            data_status=DataStatus.LIVE,
        )
