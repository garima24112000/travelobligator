"""One guarded HTTP GET for every Geoapify API (Section 203C.2B).

Geocoding, Places, Place Details and Routing share the same key handling,
outage breaker, per-generation credit accounting and failure
classification. Geoapify only accepts its key as the `apiKey` query
parameter, so the key is part of the request URL: nothing here logs, and
no httpx exception (whose text contains the URL) ever leaves this module
-- every failure is a `ProviderRequestError` with a fixed kind and message.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Callable

import httpx

from app.core import performance
from app.core.config import get_settings
from app.providers.errors import (
    REASON_PROVIDER_BAD_REQUEST,
    CooldownBreaker,
    ProviderRequestError,
    classify_bad_request,
)
from app.core.provider_usage import BudgetExhausted, ProviderUsageTracker

# Shared by every Geoapify API: a 429 or a rejected key on one of them
# means the others would fail the same way.
request_breaker = CooldownBreaker()


def _bad_request_reason(response: httpx.Response) -> str:
    """The fixed reason code for a 4xx response (see
    `errors.classify_bad_request`). Reads only the error message field of a
    JSON error body; an unreadable body is `provider_bad_request`."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - any unreadable body is simply unclassified
        return REASON_PROVIDER_BAD_REQUEST
    message = body.get("message") if isinstance(body, dict) else None
    if isinstance(message, list):  # some APIs report a list of messages
        message = " ".join(part for part in message if isinstance(part, str))
    return classify_bad_request(message)


def is_production() -> bool:
    return (get_settings().app_env or "").strip().lower() == "production"


def geoapify_get(
    client: httpx.Client,
    *,
    base_url: str,
    path: str,
    params: dict[str, Any],
    api_key: str,
    timeout: float,
    api: str,
    usage: ProviderUsageTracker | None,
    reserve_credits: int = 1,
    actual_credits: Callable[[dict[str, Any]], int] | None = None,
) -> dict[str, Any]:
    """Returns the decoded JSON object of a successful response.

    `reserve_credits` is the conservative cost reserved before the request;
    `actual_credits(payload)` reconciles it afterwards and the unused part
    is released. A failed request releases the whole reservation. Raises
    `ProviderRequestError` (`not_connected`, `no_generation_context`,
    `budget_exhausted`, `rate_limited`, `auth`, `server`, `bad_request`,
    `timeout`, `network`, `malformed`).
    """
    if not api_key:
        raise ProviderRequestError("not_connected")
    if usage is None and is_production():
        # Production spends credits only inside a generation's own budget.
        raise ProviderRequestError("no_generation_context")
    request_breaker.check()

    reservation = None
    if usage is not None:
        try:
            reservation = usage.reserve(api, reserve_credits)
        except BudgetExhausted:
            raise ProviderRequestError("budget_exhausted") from None

    def _fail(kind: str, reason: str | None = None) -> ProviderRequestError:
        if reservation is not None:
            reservation.release()
        return ProviderRequestError(kind, reason)

    # Section 1A (measurement only): the wall-clock of this request, and
    # whether the same request was already made in this generation. The
    # fingerprint is hashed and never includes the key.
    performance.note_request(api, path, params)
    try:
        # Section 1B: at most `GEOAPIFY_MAX_CONCURRENT_REQUESTS` requests of
        # this generation are on the wire at once, across every Geoapify API.
        # Waiting for a slot is not part of the request's own wall-clock.
        with usage.request_limiter.slot() if usage is not None else nullcontext():
            with performance.provider_call(performance.GEOAPIFY_PROVIDER_KEYS.get(api, "geoapify_other")):
                response = client.get(
                    f"{base_url}{path}", params={**params, "apiKey": api_key}, timeout=timeout
                )
        status = response.status_code
    except httpx.TimeoutException:
        raise _fail("timeout") from None
    except httpx.HTTPError:
        raise _fail("network") from None

    if status == 429:
        request_breaker.trip("rate_limited", response.headers.get("Retry-After"))
        raise _fail("rate_limited")
    if status in (401, 403):
        request_breaker.trip("auth")
        raise _fail("auth")
    if status >= 500:
        raise _fail("server")
    if status >= 400:
        # Section 3C.2: a 400 is classified into a fixed reason code from the
        # response's own error message; the message itself never leaves here.
        raise _fail("bad_request", _bad_request_reason(response))

    try:
        payload = response.json()
    except ValueError:
        raise _fail("malformed") from None
    if not isinstance(payload, dict):
        raise _fail("malformed")

    if reservation is not None:
        reservation.settle(actual_credits(payload) if actual_credits is not None else None)
    return payload
