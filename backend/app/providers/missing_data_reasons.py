"""Machine-readable reasons why weather, holiday or currency context is
missing (M2 Phase 1, D1/D5).

An adapter sets one of these as `ProviderResponse.failure_reason` whenever it
returns no data; `DestinationContextService` copies it to the context's
`missing_reason`, and the frontend words it for a traveller. They name what
happened, never a guess about the destination: `request_failed` is not "no
holidays", and `currency_not_supported` is not "no exchange rate exists".
"""

from __future__ import annotations

import httpx

from app.utils.country_currency import CURRENCY_AMBIGUOUS, CURRENCY_NOT_DETERMINED

# Inputs the request needs were not available.
COUNTRY_UNRESOLVED = "country_unresolved"
COORDINATES_UNAVAILABLE = "destination_coordinates_unavailable"
INVALID_DATES = "invalid_dates"
FORECAST_NOT_YET_AVAILABLE = "forecast_not_yet_available"
# The data source does not cover what was asked for.
COUNTRY_NOT_SUPPORTED = "country_not_supported"
CURRENCY_NOT_SUPPORTED = "currency_not_supported"
# What the country's currency is cannot be stated as one code.
CURRENCY_AMBIGUOUS_REASON = CURRENCY_AMBIGUOUS
CURRENCY_NOT_DETERMINED_REASON = CURRENCY_NOT_DETERMINED
# The request was made and did not produce data.
REQUEST_FAILED = "request_failed"
REQUEST_TIMED_OUT = "request_timed_out"
MALFORMED_RESPONSE = "malformed_response"
PROVIDER_REPORTED_ERROR = "provider_reported_error"
HOLIDAY_DATA_NOT_PROVIDED = "holiday_data_not_provided"
NO_USABLE_DATA = "no_usable_data"

MISSING_DATA_REASONS: frozenset[str] = frozenset(
    {
        COUNTRY_UNRESOLVED,
        COORDINATES_UNAVAILABLE,
        INVALID_DATES,
        FORECAST_NOT_YET_AVAILABLE,
        COUNTRY_NOT_SUPPORTED,
        CURRENCY_NOT_SUPPORTED,
        CURRENCY_AMBIGUOUS_REASON,
        CURRENCY_NOT_DETERMINED_REASON,
        REQUEST_FAILED,
        REQUEST_TIMED_OUT,
        MALFORMED_RESPONSE,
        PROVIDER_REPORTED_ERROR,
        HOLIDAY_DATA_NOT_PROVIDED,
        NO_USABLE_DATA,
    }
)


# Place discovery (D4): some of a broad Places batch's requests failed (or were
# refused by the per-generation credit cap), so the pool is smaller than the
# destination's real supply. Set on a PARTIAL places response; never set for
# a merely small result.
PLACES_PARTIAL_REQUEST_FAILURES = "partial_request_failures"


def reason_for_request_error(exc: Exception) -> str:
    """The reason for an exception raised while requesting or decoding a
    context provider's answer."""
    if isinstance(exc, httpx.TimeoutException):
        return REQUEST_TIMED_OUT
    if isinstance(exc, ValueError) and not isinstance(exc, httpx.HTTPError):
        return MALFORMED_RESPONSE
    return REQUEST_FAILED
