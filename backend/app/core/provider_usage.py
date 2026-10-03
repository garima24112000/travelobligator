"""Per-generation provider usage accounting (Section 203C.2B).

One `GenerationProviderContext` is created at each generation entry point
and passed EXPLICITLY down to the services and adapters that spend
Geoapify credits. There is no process-global counter: two simultaneous
generations (two users, two jobs, a targeted regeneration, a repair
inside its own generation) each charge only the tracker they were handed.

Costs that depend on the response are handled as reserve -> reconcile:
the adapter reserves a conservative amount BEFORE the request (refused
locally when it would exceed the budget), then settles to the actual cost
on success or releases the reservation on failure. Cache hits never
reserve anything.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from app.core import performance
from app.core.config import get_settings


class BudgetExhausted(Exception):
    """The generation's provider-credit budget does not allow this call.
    Raised locally, before any request is made."""

    def __init__(self) -> None:
        super().__init__("The provider call budget for this generation was reached.")


class UsageReservation:
    def __init__(self, tracker: "ProviderUsageTracker", api: str, credits: int) -> None:
        self._tracker = tracker
        self._api = api
        self._reserved = credits
        self._closed = False

    def settle(self, actual_credits: int | None = None) -> None:
        """Reconciles the reservation to what the call actually cost
        (never more than was reserved) and releases the remainder."""
        if self._closed:
            return
        self._closed = True
        actual = self._reserved if actual_credits is None else max(0, min(actual_credits, self._reserved))
        self._tracker._close(self._api, self._reserved, actual, counted_call=True)

    def release(self) -> None:
        """The request failed or was never issued: nothing is charged."""
        if self._closed:
            return
        self._closed = True
        self._tracker._close(self._api, self._reserved, 0, counted_call=False)


class _Slots:
    """A counting limiter that also knows how many slots are in use."""

    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._slots = threading.BoundedSemaphore(self.limit)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._peak = 0

    @property
    def peak(self) -> int:
        with self._lock:
            return self._peak

    def acquire(self) -> int:
        """Blocks until a slot is free; returns how many are then in use."""
        self._slots.acquire()
        with self._lock:
            self._in_flight += 1
            self._peak = max(self._peak, self._in_flight)
            return self._in_flight

    def release(self) -> None:
        with self._lock:
            self._in_flight -= 1
        self._slots.release()


# Section 1C: ONE limiter for every Geoapify request this PROCESS makes,
# whichever generation makes it. In-process only: the backend runs exactly
# one Uvicorn worker per container, so this bounds one container, NOT a
# deployment of several (each container has its own). Never Redis or
# PostgreSQL -- request concurrency is not correctness state.
_process_slots_lock = threading.Lock()
_process_slots: _Slots | None = None


def process_request_slots() -> _Slots:
    """The process-wide limiter (`GEOAPIFY_PROCESS_MAX_CONCURRENT_REQUESTS`).
    Built on first use; rebuilt only if the configured limit changes."""
    global _process_slots
    limit = get_settings().geoapify_process_max_concurrent_requests
    with _process_slots_lock:
        if _process_slots is None or _process_slots.limit != limit:
            _process_slots = _Slots(limit)
        return _process_slots


class RequestLimiter:
    """How many requests ONE generation may have in flight at once (Section
    1B). Every Geoapify API of the generation shares the one limiter, so
    overlapping batches (geocoding, Places, Place Details, routes) together
    never exceed the limit. In-process and generation-scoped: never a lock
    in Redis or PostgreSQL.

    Section 1C: a request also needs a slot of the PROCESS-wide limiter, so
    several simultaneous generations together stay under
    `GEOAPIFY_PROCESS_MAX_CONCURRENT_REQUESTS`. The two are always taken in
    the same order -- generation first, then process -- and released in the
    reverse order in `finally`, so overlapping generations cannot deadlock
    and a request that fails or times out always gives both back."""

    def __init__(self, limit: int) -> None:
        self._generation = _Slots(limit)
        self.limit = self._generation.limit

    @property
    def peak(self) -> int:
        return self._generation.peak

    @contextmanager
    def slot(self) -> Iterator[None]:
        in_flight = self._generation.acquire()
        try:
            process = process_request_slots()
            process_in_flight = process.acquire()
            try:
                performance.note_in_flight("geoapify", in_flight)
                performance.note_in_flight("geoapify_process", process_in_flight)
                yield
            finally:
                process.release()
        finally:
            self._generation.release()


class ProviderUsageTracker:
    """Credits and calls for ONE generation. Thread-safe; holds counts
    only -- never a query, an id, a URL or a key."""

    def __init__(self, budget: int | None, max_concurrent_requests: int | None = None) -> None:
        self.budget = budget
        # Section 1B: the generation's one limiter for requests in flight.
        self.request_limiter = RequestLimiter(
            max_concurrent_requests
            if max_concurrent_requests is not None
            else get_settings().geoapify_max_concurrent_requests
        )
        self._lock = threading.Lock()
        self._credits: dict[str, int] = {}
        self._calls: dict[str, int] = {}
        self._reserved = 0
        self._refused = 0

    def _spent(self) -> int:
        return sum(self._credits.values())

    def reserve(self, api: str, credits: int = 1) -> UsageReservation:
        credits = max(0, int(credits))
        with self._lock:
            if self.budget is not None and self._spent() + self._reserved + credits > self.budget:
                self._refused += 1
                raise BudgetExhausted()
            self._reserved += credits
        return UsageReservation(self, api, credits)

    def can_afford(self, credits: int) -> bool:
        with self._lock:
            return self.budget is None or self._spent() + self._reserved + credits <= self.budget

    def _close(self, api: str, reserved: int, actual: int, *, counted_call: bool) -> None:
        with self._lock:
            self._reserved -= reserved
            if counted_call:
                self._credits[api] = self._credits.get(api, 0) + actual
                self._calls[api] = self._calls.get(api, 0) + 1

    def credits_used(self, api: str | None = None) -> int:
        with self._lock:
            return self._spent() if api is None else self._credits.get(api, 0)

    def calls_made(self, api: str | None = None) -> int:
        with self._lock:
            return sum(self._calls.values()) if api is None else self._calls.get(api, 0)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "budget": self.budget,
                "credits_used": self._spent(),
                "credits_by_api": dict(self._credits),
                "calls_by_api": dict(self._calls),
                "refused_calls": self._refused,
            }


def route_request_allowance(trip_days: int) -> int:
    """Routing requests one generation may make: initial + one alternate per
    day, plus four (one bounded swap evaluation = two day-routes, and two
    post-repair re-routes)."""
    return 2 * max(1, trip_days) + 4


@dataclass
class GenerationProviderContext:
    """What one generation hands to every credit-spending provider call."""

    usage_tracker: ProviderUsageTracker
    generation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # Routing REQUESTS still allowed (Section 203C.2B routing contract): one
    # per day for the proposed order, at most one alternate per day, and a
    # fixed allowance of four for a bounded swap evaluation and post-repair
    # re-routes -- `2 * trip_days + 4`. Never an all-pairs matrix.
    route_requests_left: int = 6
    # Leg results already obtained in this generation, keyed by the routing
    # adapter, so later stages read them without another request.
    route_memo: dict = field(default_factory=dict)
    # Place Details lookups still allowed for off-pool grounded anchors.
    place_details_left: int = 12
    # Broad factual pool fetched during this generation, so a grounded
    # named place that is already in it reuses the pool identity.
    place_pool: list = field(default_factory=list)
    # Alternate-mode (driving) route requests still allowed: one per long
    # walking leg, capped per generation. Separate from `route_requests_left`
    # so adapting a leg never uses up a day's own route request.
    alternate_mode_requests_left: int = 6
    # Legs whose alternate-mode route was already asked for and did not
    # succeed, so a rebuilt route report never asks for the same leg twice.
    alternate_mode_failed_legs: set = field(default_factory=set)
    # How many duplicate candidates were merged, by the rule that matched
    # (`place_id` / `source_identity` / `name_proximity`). Counts only.
    entity_merges: dict = field(default_factory=dict)
    # Suspected duplicate pairs found in this generation's pool and what the
    # provider's evidence resolved them to (sanitised records only; see
    # `providers/places/entity_identity.collision_record`).
    suspect_collisions: list = field(default_factory=list)
    # Identity-enrichment lookups still allowed for suspected pairs. Each is
    # a Place Details lookup and also draws on `place_details_left`.
    identity_lookups_left: int = 4
    # Place ids already looked up for identity, so none is asked for twice.
    identity_checked: set = field(default_factory=set)

    @classmethod
    def new(
        cls,
        generation_id: str | None = None,
        budget: int | None = None,
        trip_days: int = 1,
    ) -> "GenerationProviderContext":
        settings = get_settings()
        tracker = ProviderUsageTracker(budget if budget is not None else settings.geoapify_max_credits_per_generation)
        context = cls(
            usage_tracker=tracker,
            route_requests_left=route_request_allowance(trip_days),
            place_details_left=settings.geoapify_max_place_details_per_generation,
            alternate_mode_requests_left=settings.route_alternate_mode_max_requests_per_generation,
            identity_lookups_left=settings.geoapify_max_identity_lookups_per_generation,
        )
        if generation_id is not None:
            context.generation_id = generation_id
        return context

    def usage_report(self) -> dict[str, object]:
        return {
            "generation_id": self.generation_id,
            **self.usage_tracker.snapshot(),
            "entity_merges": dict(self.entity_merges),
        }


def context_kwargs(provider_context: GenerationProviderContext | None) -> dict[str, GenerationProviderContext]:
    """Keyword arguments for passing the context on -- empty when there is
    none, so collaborators that predate it (and test doubles) are called
    exactly as before."""
    return {} if provider_context is None else {"provider_context": provider_context}
