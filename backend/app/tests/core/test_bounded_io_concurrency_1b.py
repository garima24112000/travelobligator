from __future__ import annotations

import argparse
import logging
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest

from app.core import bounded_concurrency, performance
from app.core.bounded_concurrency import Outcome, run_bounded
from app.core.config import get_settings
from app.core.provider_usage import GenerationProviderContext, ProviderUsageTracker
from app.models.accommodation import AccommodationSearchResult, AccommodationSearchStatus
from app.models.common import ProviderStatus
from app.models.routing import RouteRequest
from app.providers import geoapify_client
from app.providers.errors import ProviderRequestError
from app.providers.gateway import ProviderGateway
from app.providers.holidays import nager_date_adapter
from app.providers.holidays.nager_date_adapter import HOLIDAY_DATA_NOT_PROVIDED, NagerDateHolidaysAdapter
from app.providers.places.geoapify_categories import ATTRACTION_GROUPS
from app.services.ai_directed_provider_discovery_service import AIDirectedProviderDiscoveryService
from app.services.destination_context_service import DestinationContextService
from app.storage.provider_cache_store import ProviderCacheStore
from app.tests.core.test_generation_performance_1a import (  # noqa: F401 - `production_like_pipeline` is a fixture
    _canary_args,
    _load_canary,
    production_like_pipeline,
)
from app.tests.providers.test_geoapify_places_routing_203c2b import (  # noqa: F401 - `configured` is a fixture
    _adapter,
    _by_category,
    _co_located_museums,
    _context,
    _feature,
    _Network,
    _routing,
    configured,
)
from app.tests.services.test_ai_directed_provider_discovery_service import _named_place, _planning_state

# Section 1B: bounded I/O concurrency. Every test here makes the concurrent
# requests finish in a DIFFERENT order than they were issued in (a slow
# first request, a fast last one) and proves the result is the one the
# serial, one-at-a-time path produces.

_CITY = "Fixtureville, Fixtureland"
_KEY = "SENTINEL_1B_CONCURRENCY_KEY_0001"


class _SlowNetwork(_Network):
    """`_Network` whose requests take `delay(request)` seconds, and which
    records how many requests were in flight at once."""

    def __init__(self, delay: Callable[[httpx.Request], float], **handlers: Any) -> None:
        super().__init__(**handlers)
        self._delay = delay
        self._in_flight = 0
        self.peak_in_flight = 0
        self.finished: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self._in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
        try:
            time.sleep(self._delay(request))
            return super().__call__(request)
        finally:
            with self.lock:
                self._in_flight -= 1
                self.finished.append(str(request.url.params.get("text") or request.url.params.get("categories") or request.url.params.get("waypoints") or request.url.params.get("id")))


def _serial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every batch runs one task at a time, in order, on the calling thread."""
    monkeypatch.setattr(
        bounded_concurrency, "get_settings", lambda: SimpleNamespace(provider_io_concurrency_enabled=False)
    )


def _reversed_group_delay(request: httpx.Request) -> float:
    """The FIRST attraction group answers last."""
    categories = request.url.params.get("categories")
    for index, group in enumerate(ATTRACTION_GROUPS):
        if categories == ",".join(group.categories):
            return 0.02 * (len(ATTRACTION_GROUPS) - index)
    return 0.0


# -- the helper ---------------------------------------------------------------------------------------------------


def test_outcomes_come_back_in_task_order_whatever_order_the_tasks_finish_in() -> None:
    finished: list[int] = []

    def task(index: int) -> int:
        time.sleep(0.01 * (5 - index))  # the first task is the slowest
        finished.append(index)
        return index * 10

    outcomes = run_bounded("places", [lambda i=i: task(i) for i in range(5)], 5)
    assert [outcome.unwrap() for outcome in outcomes] == [0, 10, 20, 30, 40]
    assert finished != sorted(finished)  # they really did finish out of order


def test_a_batch_never_runs_more_tasks_at_once_than_its_limit() -> None:
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def task() -> None:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.01)
        with lock:
            state["now"] -= 1

    run_bounded("places", [task] * 12, 3)
    assert state["peak"] == 3


def test_one_failed_or_timed_out_task_does_not_cancel_or_change_the_others() -> None:
    def task(index: int) -> int:
        if index == 1:
            raise ProviderRequestError("timeout")
        time.sleep(0.01)
        return index

    outcomes = run_bounded("places", [lambda i=i: task(i) for i in range(4)], 4)
    assert [outcome.value for outcome in outcomes] == [0, None, 2, 3]
    assert isinstance(outcomes[1].error, ProviderRequestError) and outcomes[1].error.kind == "timeout"
    with pytest.raises(ProviderRequestError):
        outcomes[1].unwrap()


def test_disabled_concurrency_runs_the_same_tasks_serially_on_the_calling_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _serial(monkeypatch)
    threads: list[int] = []
    outcomes = run_bounded("places", [lambda: threads.append(threading.get_ident()) or 1] * 4, 4)
    assert [outcome.unwrap() for outcome in outcomes] == [1, 1, 1, 1]
    assert set(threads) == {threading.get_ident()}
    assert run_bounded("places", [], 4) == [] and run_bounded("places", [lambda: 7], 4) == [Outcome(value=7)]


# -- the generation-scoped Geoapify limiter --------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [1, 2, 4])
def test_geoapify_requests_in_flight_never_exceed_the_configured_bound_across_apis(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path, limit: int
) -> None:
    network = _SlowNetwork(lambda request: 0.02, places=_by_category, routing=_distinct_route_response)
    store = ProviderCacheStore(tmp_path / "c.sqlite3")
    context = GenerationProviderContext(
        usage_tracker=ProviderUsageTracker(100, max_concurrent_requests=limit), route_requests_left=10
    )
    places = _adapter(monkeypatch, network, store).bound_to(context)
    routing = _routing(monkeypatch, network, store).bound_to(context)

    # Two overlapping batches of different Geoapify APIs: 6 Places requests
    # and 4 routing requests, started together.
    routes = threading.Thread(target=lambda: routing.get_route_sequences(_DAYS))
    routes.start()
    places.search_broad_inventory(_CITY, {"pool_size": 60}, {"pool_size": 30})
    routes.join()

    # 4 broad + 4 local attraction requests, 2 food requests, 1 accommodation request
    assert len(network.requests["places"]) == 11 and len(network.requests["routing"]) == len(_DAYS)
    assert network.peak_in_flight <= limit
    assert context.usage_tracker.request_limiter.peak == network.peak_in_flight == min(limit, 4 + 3)


def test_the_limit_is_a_setting_and_each_generation_has_its_own_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_settings().geoapify_max_concurrent_requests == 4
    assert get_settings().provider_io_concurrency_enabled is True
    monkeypatch.setenv("GEOAPIFY_MAX_CONCURRENT_REQUESTS", "2")
    get_settings.cache_clear()
    first, second = GenerationProviderContext.new(), GenerationProviderContext.new()
    assert first.usage_tracker.request_limiter.limit == 2
    assert first.usage_tracker.request_limiter is not second.usage_tracker.request_limiter


def test_the_provider_budget_is_never_oversubscribed_by_concurrent_requests() -> None:
    tracker = ProviderUsageTracker(budget=3, max_concurrent_requests=4)
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        time.sleep(0.01)
        return httpx.Response(200, json={"results": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        outcomes = run_bounded(
            "named_place_geocoding",
            [
                lambda i=i: geoapify_client.geoapify_get(
                    client, base_url="https://geo.test", path="/v1/geocode/search", params={"text": str(i)},
                    api_key=_KEY, timeout=5.0, api="geocoding", usage=tracker,
                )
                for i in range(8)
            ],
            4,
        )
    refused = [outcome.error.kind for outcome in outcomes if outcome.error is not None]
    assert len(sent) == 3 and tracker.credits_used() == 3 and tracker.calls_made("geocoding") == 3
    assert refused == ["budget_exhausted"] * 5 and tracker.snapshot()["refused_calls"] == 5


# -- A. broad Places ---------------------------------------------------------------------------------------------------


def _broad(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> tuple[Any, GenerationProviderContext, _SlowNetwork]:
    network = _SlowNetwork(_reversed_group_delay, places=_by_category)
    context = _context()
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / f"{name}.sqlite3")).bound_to(context)
    return adapter.search_broad_inventory(_CITY, {"pool_size": 60}, {"pool_size": 30}), context, network


def test_concurrent_places_results_are_applied_in_the_original_category_order(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    concurrent, context, network = _broad(monkeypatch, tmp_path, "concurrent")
    # the requests really finished in another order than the category order
    finished = [entry for entry in network.finished if "," in entry and "." in entry]
    assert finished[: len(ATTRACTION_GROUPS)] != [",".join(group.categories) for group in ATTRACTION_GROUPS]
    assert network.peak_in_flight > 1

    _serial(monkeypatch)
    serial, serial_context, serial_network = _broad(monkeypatch, tmp_path, "serial")
    assert serial_network.peak_in_flight == 1

    for fast, slow in zip(concurrent, serial):
        assert fast.status == slow.status and fast.message == slow.message
        assert [place.model_dump() for place in fast.data] == [place.model_dump() for place in slow.data]
    attractions = concurrent[0]
    assert [place.place_id for place in attractions.data] == [
        "geoapify/s1", "geoapify/s2", "geoapify/a1", "geoapify/a2", "geoapify/m1", "geoapify/p1",
    ]
    assert [place.place_id for place in context.place_pool] == [place.place_id for place in serial_context.place_pool]
    assert context.entity_merges == serial_context.entity_merges
    assert context.usage_tracker.snapshot() == serial_context.usage_tracker.snapshot()
    assert len(network.requests["places"]) == len(serial_network.requests["places"]) == 11
    assert len(network.requests["geocode"]) == len(serial_network.requests["geocode"]) == 1


def test_a_timed_out_places_request_does_not_cancel_the_other_groups(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    def places(request: httpx.Request) -> Any:
        if "tourism.attraction" in request.url.params["categories"]:
            raise httpx.ReadTimeout("simulated timeout")
        return _by_category(request)

    network = _SlowNetwork(_reversed_group_delay, places=places)
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(_context())
    attractions, restaurants, accommodation = adapter.search_broad_inventory(_CITY, {"pool_size": 60}, None)

    # the failed group is reported (partial), every other group's places are there
    assert attractions.status == ProviderStatus.PARTIAL
    assert [place.place_id for place in attractions.data] == [
        "geoapify/s1", "geoapify/s2", "geoapify/m1", "geoapify/p1",
    ]
    assert [place.name for place in restaurants.data] == ["Corner Cafe"]
    assert [place.name for place in accommodation.data] == ["Station Hotel"]


def test_the_identity_enrichment_cap_holds_when_a_pair_is_looked_up_concurrently(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    for lookups, details_left, expected in ((4, 12, 2), (1, 12, 1), (4, 1, 1), (0, 12, 0)):
        network = _SlowNetwork(
            lambda request: 0.02 if request.url.params.get("id") == "hall-en" else 0.0,
            places=_co_located_museums,
            details=lambda place_id: {},
        )
        context = _context()
        context.identity_lookups_left, context.place_details_left = lookups, details_left
        adapter = _adapter(
            monkeypatch, network, ProviderCacheStore(tmp_path / f"c{lookups}-{details_left}.sqlite3")
        ).bound_to(context)
        pool = adapter.search_attractions(_CITY).data

        assert len(pool) == 3 and len(network.requests["details"]) == expected
        assert context.identity_lookups_left >= 0 and context.place_details_left >= 0
        assert context.usage_tracker.credits_used("place_details") == expected
        # both allowances are taken in pair order, before anything is fetched
        if expected == 1:
            assert network.requests["details"][0].url.params["id"] == "hall-en"


# -- B / C / D. named places: anchors, must-visits, details -------------------------------------------------------------

_PLACES = {f"Place {number}": number for number in range(1, 7)}


def _named_places(text: str) -> dict[str, Any] | None:
    """Six named places. "Place 3" does not exist; "Place 4" is the same
    real place as "Place 2" (same provider identity)."""
    for name, number in _PLACES.items():
        if text.startswith(name):
            if number == 3:
                return None
            number = 2 if number == 4 else number
            return {
                "name": f"Place {number}", "lat": 50.05 + number * 0.005, "lon": 10.05, "place_id": f"geo-{number}",
                "result_type": "amenity", "formatted": f"Place {number}",
                "rank": {"confidence": 1, "match_type": "full_match"},
            }
    return None


def _reversed_named_delay(request: httpx.Request) -> float:
    """The FIRST named lookup (and its Details lookup) answers last."""
    text = request.url.params.get("text") or ""
    for name, number in _PLACES.items():
        if text.startswith(name):
            return 0.015 * (7 - number)
    place_id = request.url.params.get("id") or ""
    return 0.015 * (7 - int(place_id.rsplit("-", 1)[1])) if place_id.startswith("geo-") else 0.0


_CHAPEL = {"categories": ["tourism.sights.place_of_worship.chapel"], "wiki_and_media": {"wikipedia": "en:Somewhere"}}


def _named_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, details_left: int = 12
) -> tuple[Any, GenerationProviderContext, _SlowNetwork]:
    network = _SlowNetwork(_reversed_named_delay, named=_named_places, details=lambda place_id: dict(_CHAPEL))
    context = _context()
    context.place_details_left = details_left
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / f"{name}.sqlite3"))
    return adapter, context, network


def _discover(adapter: Any, context: GenerationProviderContext) -> Any:
    proposals = [_named_place(f"p{number}", name, confidence=0.9) for name, number in _PLACES.items()]
    service = AIDirectedProviderDiscoveryService(gateway=ProviderGateway(places=adapter))
    return service.discover(
        _planning_state(primary_destination=_CITY), proposals, [],
        max_searches=4, max_extra_searches=2, provider_context=context,
    )


def test_anchor_grounding_that_completes_out_of_order_promotes_the_same_anchors(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    adapter, context, network = _named_adapter(monkeypatch, tmp_path, "concurrent")
    concurrent = _discover(adapter, context)
    geocoded = [entry.split(",")[0] for entry in network.finished if entry.startswith("Place")]
    assert geocoded[:4] != ["Place 1", "Place 2", "Place 3", "Place 4"] and network.peak_in_flight > 1

    _serial(monkeypatch)
    serial_adapter, serial_context, serial_network = _named_adapter(monkeypatch, tmp_path, "serial")
    serial = _discover(serial_adapter, serial_context)
    assert serial_network.peak_in_flight == 1

    assert concurrent.model_dump() == serial.model_dump()
    statuses = {attempt.proposal_id: attempt.status.value for attempt in concurrent.attempts}
    # base batch: 1 and 2 ground; 3 is not found; 4 is the same place as 2 and
    # LOSES to it although its lookup finished first. Two base lookups did
    # not match, so the reserve (5 and 6) runs as a second batch.
    assert statuses == {
        "p1": "matched", "p2": "matched", "p3": "not_found", "p4": "duplicate_grounded_anchor",
        "p5": "matched", "p6": "matched",
    }
    assert [attempt.match.provider_place_id for attempt in concurrent.attempts if attempt.match] == [
        "geoapify/geo-1", "geoapify/geo-2", "geoapify/geo-5", "geoapify/geo-6",
    ]
    assert concurrent.searched_count == 6 and concurrent.matched_count == 4
    # the same requests, the same credits, the same allowances as the serial run
    for api in ("geocode", "details"):
        assert sorted(str(r.url.params) for r in network.requests[api]) == sorted(
            str(r.url.params) for r in serial_network.requests[api]
        )
    assert context.usage_tracker.snapshot() == serial_context.usage_tracker.snapshot()
    assert context.place_details_left == serial_context.place_details_left
    assert context.entity_merges == serial_context.entity_merges


def test_the_reserve_phase_runs_exactly_the_lookups_a_one_at_a_time_loop_runs(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    def run(name: str) -> tuple[Any, _SlowNetwork]:
        network = _SlowNetwork(
            _reversed_named_delay,
            # every base lookup matches, so the reserve must not be touched
            named=lambda text: None if text.startswith("Place 3") else _named_places(text.replace("Place 4", "Place 5")),
            details=lambda place_id: dict(_CHAPEL),
        )
        adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / f"{name}.sqlite3"))
        proposals = [_named_place(f"p{number}", f"Place {number}", confidence=0.9) for number in (1, 2, 4, 5, 6)]
        service = AIDirectedProviderDiscoveryService(gateway=ProviderGateway(places=adapter))
        result = service.discover(
            _planning_state(primary_destination=_CITY), proposals, [],
            max_searches=2, max_extra_searches=3, provider_context=_context(),
        )
        return result, network

    concurrent, network = run("concurrent")
    _serial(monkeypatch)
    serial, serial_network = run("serial")
    assert concurrent.model_dump() == serial.model_dump()
    # both base lookups matched: no reserve lookup was dispatched at all
    assert [a.status.value for a in concurrent.attempts] == [
        "matched", "matched", "not_searched", "not_searched", "not_searched",
    ]
    assert len(network.requests["geocode"]) == len(serial_network.requests["geocode"]) == 3  # destination + 2


def test_must_visit_lookups_keep_the_users_order_and_their_own_results(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    terms = ["Place 6", "Place 3", "Place 1", "Place 5"]

    def run(name: str) -> tuple[list[dict[str, Any]], list[str], _SlowNetwork]:
        adapter, context, network = _named_adapter(monkeypatch, tmp_path, name)
        state = _planning_state(primary_destination=_CITY, must_visit=terms)
        pois, ungrounded = DestinationContextService(gateway=ProviderGateway(places=adapter))._append_must_visit_candidates(
            state, _CITY, [], places=adapter.bound_to(context)
        )
        return pois, ungrounded, network

    pois, ungrounded, network = run("concurrent")
    geocoded = [entry.split(",")[0] for entry in network.finished if entry.startswith("Place")]
    assert geocoded != terms and network.peak_in_flight > 1  # e.g. "Place 6" (first) answers first here, "Place 1" last

    _serial(monkeypatch)
    serial_pois, serial_ungrounded, _ = run("serial")
    assert pois == serial_pois and ungrounded == serial_ungrounded == ["Place 3"]
    # the user's own order, each term carried by ITS grounded place
    assert [(poi["must_visit_term"], poi["place_id"]) for poi in pois] == [
        ("Place 6", "geoapify/geo-6"), ("Place 1", "geoapify/geo-1"), ("Place 5", "geoapify/geo-5"),
    ]


def test_the_details_allowance_is_reserved_in_order_and_never_exceeded_under_concurrency(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    adapter, context, network = _named_adapter(monkeypatch, tmp_path, "c", details_left=2)
    responses = adapter.bound_to(context).search_must_visit_places(["Place 1", "Place 2", "Place 5", "Place 6"], _CITY)

    # "Place 6" was geocoded first, but the two allowances go to the first two TERMS
    assert sorted(request.url.params["id"] for request in network.requests["details"]) == ["geo-1", "geo-2"]
    assert context.place_details_left == 0 and context.usage_tracker.credits_used("place_details") == 2
    assert [response.data[0].place_id for response in responses] == [
        "geoapify/geo-1", "geoapify/geo-2", "geoapify/geo-5", "geoapify/geo-6",
    ]
    assert [bool(response.data[0].provider_tags) for response in responses] == [True, True, False, False]


def test_one_named_lookup_timing_out_does_not_cancel_or_change_the_others(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    def named(text: str) -> dict[str, Any] | None:
        if text.startswith("Place 2"):
            raise httpx.ReadTimeout("simulated timeout")
        return _named_places(text)

    network = _SlowNetwork(_reversed_named_delay, named=named, details=lambda place_id: dict(_CHAPEL))
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(_context())
    responses = adapter.search_must_visit_places(["Place 1", "Place 2", "Place 5"], _CITY)

    assert [response.status for response in responses] == [
        ProviderStatus.SUCCESS, ProviderStatus.FAILED, ProviderStatus.SUCCESS,
    ]
    assert responses[1].data is None and responses[1].failure_reason == "geocoder_timeout"
    assert [response.data[0].place_id for response in (responses[0], responses[2])] == [
        "geoapify/geo-1", "geoapify/geo-5",
    ]


# -- E / F. routing ---------------------------------------------------------------------------------------------------

_DAYS = [
    [(50.1, 10.0), (50.1, 10.1), (50.1, 10.2), (50.1, 10.3)],
    [(50.2, 10.0), (50.2, 10.1)],
    [(50.3, 10.0), (50.3, 10.1), (50.3, 10.2)],
    [(50.4, 10.0), (50.4, 10.1)],
]


def _distinct_route_response(request: httpx.Request) -> httpx.Response:
    """Every leg's distance encodes ITS OWN origin, so a result that ended
    up on the wrong day or leg is detectable."""
    waypoints = [point.split(",") for point in request.url.params["waypoints"].split("|")]
    drive = request.url.params["mode"] == "drive"
    legs = [
        {"distance": round(float(lat) * 1000 + float(lon) * 10, 1), "time": 900.0 if drive else 7500.0}
        for lat, lon in waypoints[:-1]
    ]
    return httpx.Response(200, json={"features": [{"properties": {"legs": legs}, "geometry": None}]})


def _reversed_day_delay(request: httpx.Request) -> float:
    """The FIRST day's (and first leg's) route answers last."""
    first = (request.url.params.get("waypoints") or "0,0").split("|")[0]
    return max(0.0, 0.1 - (float(first.split(",")[0]) - 50.0) * 0.2)


def _expected_distances(day: list[tuple[float, float]]) -> list[float]:
    return [round(lat * 1000 + lon * 10, 1) for lat, lon in day[:-1]]


def test_day_routes_stay_with_their_own_day_when_they_complete_out_of_order(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    network = _SlowNetwork(_reversed_day_delay, routing=_distinct_route_response)
    context = _context(trip_days=4)
    gateway = ProviderGateway(routing=_routing(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")))
    results = gateway.get_route_sequences(_DAYS, context)

    finished = [entry.split("|")[0] for entry in network.finished]
    assert finished != [f"{day[0][0]},{day[0][1]}" for day in _DAYS]  # out of order on the wire
    assert network.peak_in_flight == 3  # min(number of days, 3)
    for day, legs in zip(_DAYS, results):
        assert [leg.status for leg in legs] == [ProviderStatus.SUCCESS] * (len(day) - 1)
        assert [leg.distance_meters for leg in legs] == _expected_distances(day)
    assert context.usage_tracker.credits_used("routing") == sum(len(day) - 1 for day in _DAYS)
    # every leg was memoised under its own key: asking again costs nothing
    assert gateway.get_route_sequences(_DAYS, context) == results and len(network.requests["routing"]) == len(_DAYS)


def test_route_allowances_are_reserved_in_day_order_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    network = _SlowNetwork(_reversed_day_delay, routing=_distinct_route_response)
    context = _context()
    context.route_requests_left = 2
    adapter = _routing(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(context)
    results = adapter.get_route_sequences(_DAYS)

    # the LAST days are the fastest to answer, but the allowance went to the first two
    assert [legs[0].status for legs in results] == [
        ProviderStatus.SUCCESS, ProviderStatus.SUCCESS, ProviderStatus.UNAVAILABLE, ProviderStatus.UNAVAILABLE,
    ]
    assert len(network.requests["routing"]) == 2 and context.route_requests_left == 0


def test_a_failed_day_route_does_not_cancel_the_other_days(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    def routing(request: httpx.Request) -> httpx.Response:
        if request.url.params["waypoints"].startswith("50.2"):
            raise httpx.ReadTimeout("simulated timeout")
        return _distinct_route_response(request)

    network = _SlowNetwork(_reversed_day_delay, routing=routing)
    adapter = _routing(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(_context(trip_days=4))
    results = adapter.get_route_sequences(_DAYS)
    assert [legs[0].status for legs in results] == [
        ProviderStatus.SUCCESS, ProviderStatus.FAILED, ProviderStatus.SUCCESS, ProviderStatus.SUCCESS,
    ]
    assert [leg.distance_meters for leg in results[2]] == _expected_distances(_DAYS[2])


def test_drive_legs_stay_with_their_own_leg_and_respect_the_alternate_mode_cap(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    network = _SlowNetwork(_reversed_day_delay, routing=_distinct_route_response)
    context = _context()
    context.alternate_mode_requests_left = 3
    gateway = ProviderGateway(routing=_routing(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")))
    legs = [(day[0], day[1]) for day in _DAYS]
    day_routes_left = context.route_requests_left

    drives = gateway.get_alternate_mode_routes(legs, context)

    assert network.peak_in_flight == 3
    assert [request.url.params["mode"] for request in network.requests["routing"]] == ["drive"] * 3
    for (origin, _destination), drive in zip(legs[:3], drives[:3]):
        assert (drive.status, drive.mode, drive.duration_seconds) == (ProviderStatus.SUCCESS, "drive", 900.0)
        assert drive.distance_meters == round(origin[0] * 1000 + origin[1] * 10, 1)
    # the cap was reserved in leg order: the fourth leg is refused, not a faster earlier one
    assert drives[3].status == ProviderStatus.UNAVAILABLE and context.alternate_mode_requests_left == 0
    assert context.route_requests_left == day_routes_left  # never drawn from the day-route allowance
    assert context.usage_tracker.credits_used("routing_drive") == 3


# -- before / after: the whole pipeline ---------------------------------------------------------------------------------


def _projection(state: Any) -> dict[str, Any]:
    """Everything a traveller or a later stage can observe -- no ids minted
    per trip, no timestamps, no timings."""
    context, plan, validation = state.destination_context, state.experience_plan, state.validation_report
    quality = state.candidate_quality_report
    return {
        "candidates": [poi["place_id"] for poi in context.candidate_pois],
        "restaurants": [poi["place_id"] for poi in context.candidate_restaurants],
        "accommodation": [poi["place_id"] for poi in context.candidate_accommodation_pois],
        "collisions": context.suspect_entity_collisions,
        "scores": [(score.candidate_id, score.total_score, score.quality_tier) for score in quality.attraction_scores],
        "inventory": state.inventory_sufficiency_report.model_dump(exclude={"generated_at"}),
        "days": [
            (
                day.day_number,
                [(stop.provider_place_id, stop.name) for stop in day.experiences],
                [suggestion.name for suggestion in day.restaurant_suggestions],
                list(day.warnings),
            )
            for day in plan.daily_plans
        ],
        "diversity": [entry.model_dump() for entry in plan.schedule_diversity],
        "legs": [
            (leg.from_experience_name, leg.to_experience_name, leg.status, leg.mode, leg.distance_meters,
             leg.duration_seconds, leg.feasibility_status, leg.mode_adaptation_attempted)
            for leg in state.route_feasibility_report.legs
        ],
        "buffers": state.travel_time_buffer_report.status,
        "repair": state.route_burden_repair_report.model_dump() if state.route_burden_repair_report else None,
        "readiness": (validation.readiness_status, validation.blocking_codes, validation.review_codes),
        "coverage": state.provider_coverage.model_dump(),
        "usage": state.provider_usage_report.model_dump(exclude={"generation_id"}),
    }


@pytest.mark.parametrize("engine", ["langgraph", "legacy"])
def test_the_final_state_is_identical_with_and_without_concurrency(
    production_like_pipeline: dict[str, list[httpx.Request]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    engine: str,
) -> None:
    from app.models.planning_state import TravelGroupType, TripPace, TripRequest
    from app.providers.gateway import provider_gateway
    from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
    from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter
    from app.providers.routing.geoapify_adapter import GeoapifyRoutingAdapter
    from app.services.planning_orchestrator import planning_orchestrator

    canary = _load_canary()

    # Every request is slowed so that later requests of a batch overtake earlier ones.
    real_client = httpx.Client
    counter = {"n": 0}
    lock = threading.Lock()

    def slowed(**kwargs: Any) -> httpx.Client:
        client = real_client(**kwargs)
        send = client.send

        def out_of_order_send(request: httpx.Request, **send_kwargs: Any) -> httpx.Response:
            with lock:
                counter["n"] += 1
                turn = counter["n"]
            time.sleep(0.012 if turn % 2 else 0.0)
            return send(request, **send_kwargs)

        client.send = out_of_order_send  # type: ignore[method-assign]
        return client

    monkeypatch.setattr(httpx, "Client", slowed)

    def generate() -> Any:
        created = planning_orchestrator.create_trip(
            TripRequest(
                primary_destination="Testville, Testland",
                start_date=canary._START_DATE,
                end_date=canary._START_DATE + timedelta(days=2),
                travelers_count=2,
                travel_group_type=TravelGroupType.COUPLE,
                pace=TripPace.BALANCED,
                interests=["history", "museum", "food"],
                must_visit=["Castle 3"],
            )
        )
        if engine == "langgraph":
            return planning_orchestrator.generate_full_plan_via_langgraph(created.trip_id)
        return planning_orchestrator.generate_full_plan(created.trip_id)

    concurrent = generate()
    report = concurrent.generation_performance_report
    assert report.concurrent_batches >= 2 and report.peak_geoapify_concurrency > 1
    # the first Places batch (4 broad + 4 local + 2 food + 1 accommodation), then the must-visit
    # follow-up batch (the held culture, parks and food shares)
    assert report.batch_sizes["places"] == [11, 3] and report.batch_sizes["walk_routes"] == [3]

    # the same generation again, equally cold (fresh adapters, fresh cache),
    # with every batch forced serial
    store = ProviderCacheStore(tmp_path / "serial.sqlite3")
    monkeypatch.setattr(provider_gateway, "places", GeoapifyPlacesAdapter(cache_store=store, geocoder=GeoapifyGeocoder()))
    monkeypatch.setattr(provider_gateway, "routing", GeoapifyRoutingAdapter(cache_store=store))
    _serial(monkeypatch)
    serial = generate()
    assert serial.generation_performance_report.concurrent_batches == 0
    assert serial.generation_performance_report.peak_geoapify_concurrency == 1

    assert _projection(concurrent) == _projection(serial)
    assert len(concurrent.experience_plan.daily_plans) == 3
    assert concurrent.validation_report.readiness_status.value == "ready"


# -- the performance recorder under concurrency --------------------------------------------------------------------------


def test_the_performance_recorder_works_under_concurrent_tasks() -> None:
    recorder = performance.PerformanceRecorder()
    tracker = ProviderUsageTracker(100, max_concurrent_requests=3)

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(0.02)
        return httpx.Response(200, json={"results": []})

    def lookup(index: int) -> None:
        with performance.stage("destination_resolution"):
            geoapify_client.geoapify_get(
                client, base_url="https://geo.test", path="/v1/geocode/search", params={"text": str(index)},
                api_key=_KEY, timeout=5.0, api="geocoding", usage=tracker,
            )

    with performance.activate(recorder), httpx.Client(transport=httpx.MockTransport(handler)) as client:
        recorder.start()
        with performance.stage("anchor_grounding"):
            run_bounded("named_place_geocoding", [lambda i=i: lookup(i) for i in range(6)], 4)
            run_bounded("place_details", [lambda i=i: lookup(i + 10) for i in range(2)], 4)
    report = recorder.snapshot(tracker.snapshot())

    # every thread's request was recorded, on the one recorder
    assert report["provider_attempts"] == {"geoapify_geocoding": 8} and report["counts"]["geocode_requests"] == 8
    assert report["request_totals"] == {"geocoding": 8} and report["redundant_requests"] == {"geocoding": 0}
    # concurrency diagnostics
    assert report["concurrent_batches"] == 2
    assert report["batch_sizes"] == {"named_place_geocoding": [6], "place_details": [2]}
    assert report["peak_geoapify_concurrency"] == tracker.request_limiter.peak == 3
    # wall-clock stays with the stage that waited; the tasks' own time is kept apart and overlaps it
    assert set(report["stage_ms"]) == {"anchor_grounding"}
    assert report["stage_task_ms"]["destination_resolution"] > report["stage_ms"]["anchor_grounding"]
    assert sum(report["stage_ms"].values()) + report["other_ms"] == pytest.approx(report["total_ms"], abs=0.5)
    # the provider wall time is the sum over the tasks, so it exceeds the batch's wall-clock
    assert report["provider_ms"]["geoapify_geocoding"] > report["stage_ms"]["anchor_grounding"]


def test_the_canary_reports_concurrency_diagnostics_without_judging_them(
    production_like_pipeline: dict[str, list[httpx.Request]]
) -> None:
    canary = _load_canary()
    report = canary._run(_canary_args(), {})
    section = report["performance"]
    assert section["concurrency"]["peak_geoapify_concurrency"] >= 1
    assert section["concurrency"]["concurrent_batches"] >= 2
    assert section["concurrency"]["batch_sizes"]["places"] == [11, 3]  # first batch, then the must-visit follow-up
    text = canary._render(report)
    for label in (
        "CONCURRENCY", "- peak Geoapify concurrency (this generation):", "- peak Geoapify concurrency (whole process):",
        "- concurrent batches:", "- batch sizes by operation:",
    ):
        assert label in text, label
    assert report["acceptance"]["outcome"] == "PASS"
    assert not any("concurren" in check["check"].lower() for check in report["acceptance"]["checks"])


# -- Nager.Date: a successful EMPTY answer -------------------------------------------------------------------------------

_HOLIDAY = [{"date": "2027-01-01", "localName": "Ano Novo", "name": "New Year's Day", "countryCode": "PT"}]
_DATES = {"start_date": "2026-12-30", "end_date": "2027-01-02"}


def _nager(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response]
) -> tuple[NagerDateHolidaysAdapter, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    real_client = httpx.Client
    monkeypatch.setattr(
        nager_date_adapter.httpx, "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(recording), **kwargs),
    )
    return NagerDateHolidaysAdapter(cache_store=ProviderCacheStore(tmp_path / "nager.sqlite3")), sent


def test_nager_200_with_valid_json_is_a_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    adapter, sent = _nager(monkeypatch, tmp_path, lambda request: httpx.Response(200, json=_HOLIDAY))
    response = adapter.get_public_holidays("Lisbon, Portugal", _DATES)
    assert response.status == ProviderStatus.SUCCESS and response.data
    assert {holiday.name for holiday in response.data} == {"New Year's Day"}
    assert len(sent) == 2 and response.failure_reason is None  # one request per calendar year


@pytest.mark.parametrize(
    "empty", [httpx.Response(204), httpx.Response(200, content=b""), httpx.Response(200, content=b"  \n")],
    ids=["204", "200-empty-body", "200-whitespace-body"],
)
def test_nager_successful_empty_answer_is_unavailable_not_a_failure_and_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, empty: httpx.Response
) -> None:
    adapter, sent = _nager(
        monkeypatch, tmp_path, lambda request: httpx.Response(empty.status_code, content=empty.content)
    )
    with caplog.at_level(logging.INFO, logger=nager_date_adapter.__name__):
        response = adapter.get_public_holidays("Jaipur, India", _DATES)

    # a neutral availability state: not a failure, and not "there are no holidays"
    assert response.status == ProviderStatus.UNAVAILABLE and response.data is None
    assert response.failure_reason == HOLIDAY_DATA_NOT_PROVIDED
    assert "no public holiday data" in response.message and "IN" in response.message
    assert len(sent) == 2  # one request per calendar year: nothing was retried
    # no exception-style warning, no parse error, no destination text
    assert [record.levelname for record in caplog.records] == ["INFO"]
    logged = caplog.records[0].getMessage()
    assert logged == "Nager.Date returned no holiday data (country=IN, status=holiday_data_not_provided)."
    assert "Jaipur" not in logged and "Expecting value" not in caplog.text

    # remembered briefly: the next generation does not ask again
    again = adapter.get_public_holidays("Jaipur, India", _DATES)
    assert again.status == ProviderStatus.UNAVAILABLE and again.failure_reason == HOLIDAY_DATA_NOT_PROVIDED
    assert len(sent) == 2


def test_nager_no_data_is_not_remembered_when_the_negative_cache_is_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NAGER_DATE_NO_DATA_CACHE_TTL_SECONDS", "0")
    get_settings.cache_clear()
    adapter, sent = _nager(monkeypatch, tmp_path, lambda request: httpx.Response(204))
    adapter.get_public_holidays("Jaipur, India", _DATES)
    adapter.get_public_holidays("Jaipur, India", _DATES)
    assert len(sent) == 4


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(200, content=b"<html>not json</html>"),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("simulated network failure")),
        lambda request: httpx.Response(503),
    ],
    ids=["malformed-200", "network-failure", "http-503"],
)
def test_nager_real_failures_are_still_failures_and_are_never_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, handler: Any
) -> None:
    adapter, sent = _nager(monkeypatch, tmp_path, handler)
    with caplog.at_level(logging.INFO, logger=nager_date_adapter.__name__):
        response = adapter.get_public_holidays("Jaipur, India", _DATES)
    assert response.status == ProviderStatus.FAILED and response.failure_reason is None
    assert [record.levelname for record in caplog.records] == ["WARNING"]
    # fixed identifiers only: never the destination, the URL or the exception text
    assert "Jaipur" not in caplog.text and "http" not in caplog.text.lower().replace("http 503", "")
    assert "simulated" not in caplog.text and "Expecting value" not in caplog.text

    adapter.get_public_holidays("Jaipur, India", _DATES)
    assert len(sent) == 2  # the failure was not cached: the second call asked again (and stopped at the first year)


# -- provider logging ------------------------------------------------------------------------------------------------------


class _Inventory:
    provider_name = "scraped_accommodation_provider"

    def __init__(self, status: AccommodationSearchStatus) -> None:
        self._status = status

    def search_accommodations(self, request: Any) -> AccommodationSearchResult:
        return AccommodationSearchResult(
            provider=self.provider_name, status=self._status, offers=[],
            message="https://secret.example/?apiKey=SENTINEL raw provider text",
        )


@pytest.mark.parametrize(
    ("status", "level", "event", "message"),
    [
        (AccommodationSearchStatus.UNAVAILABLE, "INFO", "provider.no_data", "Optional provider returned no data"),
        (AccommodationSearchStatus.NOT_CONNECTED, "INFO", "provider.no_data", "Optional provider returned no data"),
        (AccommodationSearchStatus.FAILED, "WARNING", "provider.failure", "Provider call failed"),
        (AccommodationSearchStatus.SUCCESS, "INFO", "provider.success", "Provider call completed"),
    ],
)
def test_gateway_logs_name_provider_operation_and_status_and_only_real_failures_warn(
    caplog: pytest.LogCaptureFixture, status: AccommodationSearchStatus, level: str, event: str, message: str
) -> None:
    gateway = ProviderGateway(accommodation_inventory=_Inventory(status))
    with caplog.at_level(logging.INFO, logger="app.providers.gateway"):
        result = gateway.search_accommodations(SimpleNamespace(destination="Jaipur, India"))  # type: ignore[arg-type]

    assert result.status == status  # logging never changes the result
    (record,) = caplog.records
    assert record.levelname == level and record.event == event
    assert record.getMessage() == (
        f"{message} (provider=scraped_accommodation_provider, operation=accommodations, status={status.value})."
    )
    assert (record.provider, record.operation, record.status) == (
        "scraped_accommodation_provider", "accommodations", status.value,
    )
    for forbidden in ("SENTINEL", "http", "apiKey", "Jaipur", "raw provider text", "did not return success"):
        assert forbidden not in record.getMessage() and forbidden not in str(record.__dict__), forbidden


def test_a_routing_provider_that_is_unavailable_or_not_connected_still_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.providers.gateway"):
        ProviderGateway().get_route(
            RouteRequest(origin_lat=50.0, origin_lon=10.0, destination_lat=50.1, destination_lon=10.1)
        )
    (record,) = caplog.records
    assert record.levelname == "WARNING" and record.event == "provider.failure"
    assert record.getMessage().endswith("operation=routing, status=not_connected).")


# -- Groq: explicit, finite request bounds ---------------------------------------------------------------------------------


def test_groq_anchor_reasoning_repair_and_narrator_clients_have_explicit_finite_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import langchain_groq

    from app.providers.ai_candidate_proposal.groq_adapter import GroqAICandidateProposalProvider
    from app.providers.ai_itinerary_reasoning.groq_adapter import GroqAIItineraryReasoningProvider
    from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider

    built: list[dict[str, Any]] = []

    class _ChatGroq:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

        def with_structured_output(self, *args: Any, **kwargs: Any) -> Any:
            return object()

    monkeypatch.setattr(langchain_groq, "ChatGroq", _ChatGroq)
    settings = get_settings()
    assert (settings.groq_request_timeout_seconds, settings.groq_max_retries) == (30.0, 1)

    GroqAICandidateProposalProvider(api_key="k")._build_client()
    reasoning = GroqAIItineraryReasoningProvider(api_key="k")
    reasoning._build_client()
    reasoning._build_repair_client()
    GroqItineraryNarratorProvider(api_key="k")._build_client()
    # an attempt that only has part of the stage budget left gets exactly that
    GroqItineraryNarratorProvider(api_key="k")._build_client(timeout=4.5)

    # Section 1C: the SDK's own hidden retries are OFF for every stage -- the
    # single recovery attempt is the application's, under the stage budget.
    assert [(kwargs["timeout"], kwargs["max_retries"]) for kwargs in built] == [
        (30.0, 0), (30.0, 0), (30.0, 0), (settings.itinerary_narrator_timeout_seconds, 0), (4.5, 0),
    ]
    assert all(kwargs["timeout"] > 0 for kwargs in built)
