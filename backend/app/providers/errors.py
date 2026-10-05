"""Generic external-provider request failure (Section 203C.2B).

Shared by every keyed HTTP provider API (Geoapify Geocoding, Places, Place
Details, Routing). A failure is a fixed `kind` plus a fixed, secret-free
message -- never the request URL, the query, the response body or an API
key. `app.providers.geocoding.base.GeocoderError` is the geocoder-worded
subclass.
"""

from __future__ import annotations

import threading
import time

_SAFE_MESSAGES = {
    "not_connected": "The provider is not connected.",
    "rate_limited": "The provider rate limit was reached.",
    "auth": "The provider rejected the configured credentials.",
    "timeout": "The provider timed out.",
    "server": "The provider returned a server error.",
    "malformed": "The provider returned an unusable response.",
    "network": "The provider could not be reached.",
    "bad_request": "The provider rejected the request.",
    "budget_exhausted": "The provider call budget for this generation was reached.",
    "no_generation_context": "The provider was called outside a generation.",
}


# Section 3C.2: what an HTTP 400 meant. A provider answers 400 both for a
# request it cannot parse and for a perfectly well-formed request it cannot
# satisfy (a point that is not on its routing network, no route between two
# points). Those need different handling, so the response's own error
# message is CLASSIFIED into one of these fixed codes -- the message itself
# is never stored, logged or returned.
REASON_INVALID_COORDINATES = "invalid_coordinates"
REASON_UNROUTABLE_ENDPOINT = "unroutable_endpoint"
REASON_NO_ROUTE = "no_route"
REASON_MALFORMED_REQUEST = "malformed_request"
REASON_PROVIDER_BAD_REQUEST = "provider_bad_request"  # a 400 this code does not recognise
BAD_REQUEST_REASONS = frozenset(
    {
        REASON_INVALID_COORDINATES, REASON_UNROUTABLE_ENDPOINT, REASON_NO_ROUTE, REASON_MALFORMED_REQUEST,
        REASON_PROVIDER_BAD_REQUEST,
    }
)

# Fixed phrases looked for in a provider's 400 message, most specific first.
# Wording of this kind is what routing engines report for each condition;
# anything else stays `provider_bad_request`.
_BAD_REQUEST_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        REASON_UNROUTABLE_ENDPOINT,
        ("no suitable edges", "no edges near", "unreachable", "not reachable", "could not be matched",
         "cannot be matched", "no road", "not routable", "unable to snap", "could not snap"),
    ),
    (
        REASON_NO_ROUTE,
        ("no path could be found", "no path found", "no route", "route not found", "path distance exceeds",
         "exceeds the max distance", "could not find a path", "could not find a route"),
    ),
    (
        REASON_INVALID_COORDINATES,
        ("invalid coordinate", "invalid location", "invalid waypoint", "out of bounds", "out of range",
         "latitude", "longitude", "insufficient number of locations", "at least two"),
    ),
    (
        REASON_MALFORMED_REQUEST,
        ("is required", "is not allowed", "must be one of", "must be a", "failed to parse", "invalid mode",
         "validation", "unknown parameter"),
    ),
)


def classify_bad_request(message: object) -> str:
    """The fixed reason code for a provider's HTTP 400 message. Only the
    code leaves this function."""
    if not isinstance(message, str) or not message.strip():
        return REASON_PROVIDER_BAD_REQUEST
    lowered = message.lower()
    for reason, phrases in _BAD_REQUEST_PHRASES:
        if any(phrase in lowered for phrase in phrases):
            return reason
    return REASON_PROVIDER_BAD_REQUEST


class ProviderRequestError(Exception):
    """A provider request failed. `kind` is one of the fixed keys of
    `safe_messages`; `str(error)` is always the fixed message for that
    kind, so it is safe to log and to show."""

    safe_messages: dict[str, str] = _SAFE_MESSAGES

    def __init__(self, kind: str, reason: str | None = None) -> None:
        self.kind = kind if kind in self.safe_messages else "network"
        # Section 3C.2: an optional finer classification of a rejected
        # request -- one of `BAD_REQUEST_REASONS`, never provider text.
        self.reason = reason if reason in BAD_REQUEST_REASONS else None
        super().__init__(self.safe_messages[self.kind])


class CooldownBreaker:
    """Process-local circuit breaker for one provider endpoint.

    Once tripped (rate limit or rejected credentials), every further
    request is refused locally with the same error kind until the cooldown
    ends -- so one 429 stops the rest of a generation's lookups instead of
    each one hitting the endpoint again. Not shared across containers
    (PostgreSQL/Redis are never used as a lock), and not usage accounting.
    """

    default_cooldown_seconds = 60.0
    max_cooldown_seconds = 300.0

    def __init__(self, error_cls: type[ProviderRequestError] = ProviderRequestError) -> None:
        self._error_cls = error_cls
        self._lock = threading.Lock()
        self._blocked_until = 0.0
        self._blocked_kind = "rate_limited"

    def reset(self) -> None:
        with self._lock:
            self._blocked_until = 0.0

    def check(self) -> None:
        with self._lock:
            if time.monotonic() < self._blocked_until:
                raise self._error_cls(self._blocked_kind)

    def trip(self, kind: str, retry_after: str | None = None) -> None:
        cooldown = self.default_cooldown_seconds
        try:
            if retry_after is not None:
                cooldown = max(cooldown, float(retry_after))
        except (TypeError, ValueError):
            pass  # an HTTP-date Retry-After: keep the default cooldown
        with self._lock:
            self._blocked_until = time.monotonic() + min(cooldown, self.max_cooldown_seconds)
            self._blocked_kind = kind
