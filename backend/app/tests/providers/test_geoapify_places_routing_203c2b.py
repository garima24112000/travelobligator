from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from app.core.config import Settings
from app.core.provider_usage import (
    BudgetExhausted,
    GenerationProviderContext,
    ProviderUsageTracker,
    route_request_allowance,
)
from app.models.candidate_quality import CandidateQualityTier
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.planning_state import (
    DailyPlan,
    DestinationContext,
    ExperienceItem,
    ExperiencePlan,
    PlanningState,
    TravelGroupType,
    TripRequest,
)
from app.models.routing import RouteRequest
from app.providers import geoapify_client
from app.providers.errors import ProviderRequestError
from app.providers.gateway import ProviderGateway
from app.providers.geocoding import geoapify_adapter as geoapify_geocoder_module
from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
from app.providers.places import geoapify_places_adapter as places_module
from app.providers.places.geoapify_categories import (
    ACCOMMODATION_GROUP,
    ALL_GROUPS,
    ATTRACTION_GROUPS,
    FOOD_GROUP,
    VERIFIED_TAXONOMY,
    configured_categories,
    taxonomy_tags_from_categories,
)
from app.providers.places.factory import get_places_provider
from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter, places_request_credits
from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter
from app.providers.routing import geoapify_adapter as routing_module
from app.providers.routing.factory import get_routing_provider
from app.providers.routing.geoapify_adapter import GeoapifyRoutingAdapter
from app.services.candidate_quality_service import CandidateQualityService
from app.services.entity_collisions import SUSPECT_COLLISION_KEY, apply_suspect_collisions
from app.services.route_feasibility_service import RouteFeasibilityService
from app.storage.provider_cache_store import ProviderCacheStore

# Section 203C.2B: Geoapify Places + Routing + per-generation usage accounting.
# Every HTTP exchange is an in-process `httpx.MockTransport`; the key is a
# sentinel. Place names below are invented fixtures, not real destinations.

_KEY = "SENTINEL_203C2B_GEOAPIFY_KEY_9911"
_RealClient = httpx.Client

_DESTINATION = {
    "city": "Fixtureville",
    "country": "Fixtureland",
    "country_code": "fx",
    "lon": 10.0,
    "lat": 50.0,
    "result_type": "city",
    "formatted": "Fixtureville, Fixtureland",
    "rank": {"confidence": 1, "match_type": "full_match"},
    "place_id": "dest01",
    "bbox": {"lon1": 9.9, "lat1": 49.9, "lon2": 10.1, "lat2": 50.1},
}


def _feature(place_id: str, name: str, categories: list[str], lat: float = 50.0, lon: float = 10.0, **extra: Any) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {"place_id": place_id, "name": name, "categories": categories, "lat": lat, "lon": lon, **extra},
    }


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, GEOAPIFY_API_KEY=_KEY, GEOCODING_PROVIDER="geoapify", **overrides)


class _Network:
    """Routes by path. `places` maps a request to a list of features (or an
    `httpx.Response`); every request is recorded by API."""

    def __init__(
        self,
        places: Callable[[httpx.Request], Any] | None = None,
        named: Callable[[str], dict[str, Any] | None] | None = None,
        details: Callable[[str], dict[str, Any]] | None = None,
        routing: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> None:
        self._places = places or (lambda request: [])
        self._named = named or (lambda text: None)
        self._details = details or (lambda place_id: {})
        self._routing = routing
        self.requests: dict[str, list[httpx.Request]] = {"geocode": [], "places": [], "details": [], "routing": []}
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/geocode/search":
            with self.lock:
                self.requests["geocode"].append(request)
            text = request.url.params.get("text") or ""
            if text == "Fixtureville, Fixtureland":
                return httpx.Response(200, json={"results": [_DESTINATION]})
            hit = self._named(text)
            return httpx.Response(200, json={"results": [hit] if hit else []})
        if path == "/v2/places":
            with self.lock:
                self.requests["places"].append(request)
            result = self._places(request)
            return result if isinstance(result, httpx.Response) else httpx.Response(200, json={"features": result})
        if path == "/v2/place-details":
            with self.lock:
                self.requests["details"].append(request)
            properties = self._details(request.url.params["id"])
            return httpx.Response(200, json={"features": [{"properties": properties}]})
        if path == "/v1/routing":
            with self.lock:
                self.requests["routing"].append(request)
            assert self._routing is not None
            return self._routing(request)
        return httpx.Response(404)

    def client(self, **kwargs: Any) -> httpx.Client:
        return _RealClient(transport=httpx.MockTransport(self), **kwargs)


@pytest.fixture()
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in (places_module, routing_module, geoapify_geocoder_module):
        monkeypatch.setattr(module, "get_settings", lambda: _settings())


def _adapter(monkeypatch: pytest.MonkeyPatch, network: _Network, store: ProviderCacheStore) -> GeoapifyPlacesAdapter:
    monkeypatch.setattr(httpx, "Client", network.client)
    return GeoapifyPlacesAdapter(cache_store=store, geocoder=GeoapifyGeocoder())


def _context(budget: int = 100, trip_days: int = 3) -> GenerationProviderContext:
    context = GenerationProviderContext(
        usage_tracker=ProviderUsageTracker(budget), route_requests_left=route_request_allowance(trip_days)
    )
    return context


def _by_category(request: httpx.Request) -> list[dict[str, Any]]:
    categories = request.url.params["categories"]
    if "tourism.sights" in categories:
        return [
            _feature("s1", "Old Castle", ["tourism", "tourism.sights", "tourism.sights.castle"], 50.01, 10.01,
                     wiki_and_media={"wikipedia": "en:Old Castle", "wikidata": "Q1"}),
            _feature("s2", "Private Tower", ["tourism", "tourism.sights", "tourism.sights.tower",
                                              "access_limited", "access_limited.private", "heritage"], 50.02, 10.02),
        ]
    if "tourism.attraction" in categories:
        return [
            _feature("a1", "Bronze Figure", ["tourism", "tourism.attraction", "tourism.attraction.artwork",
                                              "tourism.attraction.artwork.statue"], 50.03, 10.03),
            _feature("a2", "Hill Lookout", ["tourism", "tourism.attraction", "tourism.attraction.viewpoint"], 50.04, 10.04),
        ]
    if "entertainment.museum" in categories:
        return [
            _feature("m1", "City Museum", ["entertainment", "entertainment.museum", "fee"], 50.05, 10.05,
                     datasource={"sourcename": "openstreetmap", "raw": {"tourism": "museum", "wikidata": "Q2", "phone": "1"}}),
        ]
    if "leisure.park" in categories:
        return [_feature("p1", "Central Garden", ["leisure", "leisure.park", "leisure.park.garden"], 50.06, 10.06)]
    if "catering" in categories:
        return [_feature("r1", "Corner Cafe", ["catering", "catering.cafe"], 50.01, 10.02)]
    if "accommodation" in categories:
        return [_feature("h1", "Station Hotel", ["accommodation", "accommodation.hotel"], 50.01, 10.03)]
    return []


# -- category contract ----------------------------------------------------------------------------


def test_every_configured_category_is_in_the_verified_taxonomy_snapshot() -> None:
    configured = configured_categories()
    assert configured, "no categories configured"
    assert configured <= VERIFIED_TAXONOMY, sorted(configured - VERIFIED_TAXONOMY)
    # each attraction family has a share, and the shares cover the pool
    assert sum(group.share for group in ATTRACTION_GROUPS) == pytest.approx(1.0)
    assert {group.key for group in ALL_GROUPS} >= {"sights", "attractions", "culture", "food", "accommodation"}


def test_the_category_rename_only_uses_verified_paths() -> None:
    from app.providers.places import geoapify_categories

    assert set(geoapify_categories._EXACT) <= VERIFIED_TAXONOMY


def test_factories_select_geoapify_only_when_configured(monkeypatch: pytest.MonkeyPatch, configured: None) -> None:
    assert Settings(_env_file=None).places_provider == "openstreetmap"
    assert isinstance(get_places_provider("openstreetmap"), OpenStreetMapPlacesAdapter)
    assert isinstance(get_places_provider("geoapify"), GeoapifyPlacesAdapter)
    assert isinstance(get_routing_provider("geoapify"), GeoapifyRoutingAdapter)
    with pytest.raises(ValueError):
        Settings(_env_file=None, PLACES_PROVIDER="google")


# -- Places: grouping, mapping, dedup ----------------------------------------------------------------


def test_attractions_use_one_request_per_group_inside_the_destination_boundary(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    network = _Network(places=_by_category)
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3"))
    response = adapter.search_attractions("Fixtureville, Fixtureland", {"pool_size": 60})

    assert response.status == ProviderStatus.SUCCESS
    assert len(network.requests["places"]) == len(ATTRACTION_GROUPS)
    # Section 1B: the group requests are one concurrent batch, so they may
    # ARRIVE in any order; one request per group is what is asserted (the
    # order the results are APPLIED in is covered by the concurrency tests).
    arrived = {request.url.params["categories"]: request.url.params for request in network.requests["places"]}
    sent = [arrived[",".join(group.categories)] for group in ATTRACTION_GROUPS]
    assert [params["categories"] for params in sent] == [",".join(group.categories) for group in ATTRACTION_GROUPS]
    for params in sent:
        assert params["filter"] == "place:dest01"  # the destination's real boundary
        assert params["conditions"] == "named"
        assert params["offset"] == "0"
    assert [int(params["limit"]) for params in sent] == [21, 9, 18, 12]  # shares of the 60-place pool
    assert len(network.requests["geocode"]) == 1

    food = adapter.search_restaurants("Fixtureville, Fixtureland", {"pool_size": 30})
    assert network.requests["places"][-1].url.params["categories"] == ",".join(FOOD_GROUP.categories)
    assert [place.name for place in food.data] == ["Corner Cafe"]
    stay = adapter.search_accommodation_pois("Fixtureville, Fixtureland")
    assert network.requests["places"][-1].url.params["categories"] == ",".join(ACCOMMODATION_GROUP.categories)
    assert stay.data[0].provider_tags["tourism"] == "hotel"


def test_places_are_mapped_from_what_the_response_states(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    adapter = _adapter(monkeypatch, _Network(places=_by_category), ProviderCacheStore(tmp_path / "c.sqlite3"))
    places = {place.name: place for place in adapter.search_attractions("Fixtureville, Fixtureland").data}

    castle = places["Old Castle"]
    assert (castle.source, castle.place_id) == ("geoapify_places", "geoapify/s1")
    assert (castle.coordinates.lat, castle.coordinates.lng) == (50.01, 10.01)
    assert castle.category == "castle"
    assert castle.provider_tags["historic"] == "castle"
    assert castle.provider_tags["wikipedia"] == "en:Old Castle"
    assert castle.provider_tags["category_path"] == "tourism.sights.castle"

    # underlying OSM tags, when Geoapify returns them, are kept (whitelisted only)
    museum = places["City Museum"]
    assert museum.provider_tags["tourism"] == "museum" and museum.provider_tags["wikidata"] == "Q2"
    assert "phone" not in museum.provider_tags

    assert places["Private Tower"].provider_tags["access"] == "private"
    assert places["Private Tower"].provider_tags["heritage"] == "yes"
    assert places["Bronze Figure"].provider_tags["tourism"] == "artwork"
    assert places["Central Garden"].provider_tags["leisure"] == "garden"
    for place in places.values():  # never a rating, price, hours or booking field
        assert set(place.model_dump()) == {
            "place_id", "name", "category", "coordinates", "address", "source", "data_status",
            "confidence", "provider_tags",
        }


def test_duplicates_are_removed_by_identity_and_by_name_plus_proximity(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    def _places(request: httpx.Request) -> list[dict[str, Any]]:
        if "tourism.sights" in request.url.params["categories"]:
            return [
                _feature("s1", "Old Castle", ["tourism.sights.castle"], 50.01, 10.01),
                _feature("s1", "Old Castle", ["tourism.sights.castle"], 50.01, 10.01),  # same id
                _feature("s9", "Old  Castle", ["tourism.sights"], 50.0101, 10.0101),  # same name, ~13 m away
                _feature("s8", "Old Castle", ["tourism.sights"], 50.05, 10.05),  # same name, far away: distinct
            ]
        return []

    adapter = _adapter(monkeypatch, _Network(places=_places), ProviderCacheStore(tmp_path / "c.sqlite3"))
    data = adapter.search_attractions("Fixtureville, Fixtureland").data
    assert [place.place_id for place in data] == ["geoapify/s1", "geoapify/s8"]


def test_expansion_requests_the_next_page(monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path) -> None:
    network = _Network(places=_by_category)
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3"))
    adapter.search_attractions("Fixtureville, Fixtureland", {"pool_size": 60, "page": 1})
    # one concurrent batch: arrival order is not fixed, the offset per group is
    arrived = {request.url.params["categories"]: request.url.params["offset"] for request in network.requests["places"]}
    assert [arrived[",".join(group.categories)] for group in ATTRACTION_GROUPS] == ["21", "9", "18", "12"]


# -- quality: provider order, private access, low-value objects -------------------------------------


def test_quality_ranking_does_not_depend_on_provider_order_and_penalises_private_and_minor_objects(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    quality = CandidateQualityService()

    def _ranked(reverse: bool) -> list[tuple[str, CandidateQualityTier]]:
        def _places(request: httpx.Request) -> list[dict[str, Any]]:
            features = _by_category(request)
            return list(reversed(features)) if reverse else features

        adapter = _adapter(
            monkeypatch, _Network(places=_places), ProviderCacheStore(tmp_path / f"c{int(reverse)}.sqlite3")
        )
        data = adapter.search_attractions("Fixtureville, Fixtureland").data
        scores = sorted((quality.score_attraction(place) for place in data), key=lambda s: (-s.total_score, s.candidate_name))
        return [(score.candidate_name, score.quality_tier) for score in scores]

    forward, backward = _ranked(False), _ranked(True)
    assert forward == backward  # provider return order is never a prominence signal

    by_name = dict(forward)
    names = [name for name, _ in forward]
    assert names[0] == "Old Castle"  # specific type + wikipedia evidence
    assert by_name["Private Tower"] == CandidateQualityTier.REJECTED  # not publicly accessible
    assert names.index("Bronze Figure") > names.index("City Museum")  # a statue never outranks a museum
    statue = next(
        quality.score_attraction(place)
        for place in _adapter(
            monkeypatch, _Network(places=_by_category), ProviderCacheStore(tmp_path / "s.sqlite3")
        ).search_attractions("Fixtureville, Fixtureland").data
        if place.name == "Bronze Figure"
    )
    assert statue.low_value_object is True


def test_a_private_place_the_user_explicitly_asked_for_is_not_rejected() -> None:
    place = {
        "place_id": "geoapify/x", "name": "Private Tower", "category": "tower",
        "coordinates": {"lat": 50.0, "lng": 10.0}, "source": "geoapify_places", "data_status": "live",
        "confidence": 0.6, "provider_tags": {"man_made": "tower", "access": "private"},
        "must_visit_term": "the old tower",
    }
    quality = CandidateQualityService()
    assert quality.score_attraction(place).quality_tier == CandidateQualityTier.REJECTED
    pinned = quality.score_attraction(place, must_visit_names=["the old tower"])
    assert pinned.quality_tier == CandidateQualityTier.PRIMARY_ANCHOR


# -- cache --------------------------------------------------------------------------------------------


def test_positive_results_are_cached_and_a_cache_hit_costs_nothing(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    store = ProviderCacheStore(tmp_path / "c.sqlite3")
    network = _Network(places=_by_category)
    first_context = _context()
    first = _adapter(monkeypatch, network, store).bound_to(first_context).search_attractions("Fixtureville, Fixtureland")
    assert len(network.requests["places"]) == 4
    # reserved 3 + 1 + 1 + 1, settled to what came back: (1 + 1) + 1 + 1 + 1
    assert first_context.usage_tracker.credits_used("places") == 5
    assert first_context.usage_tracker.credits_used("geocoding") == 1

    second_context = _context()
    second = _adapter(monkeypatch, network, store).bound_to(second_context).search_attractions("Fixtureville, Fixtureland")
    assert len(network.requests["places"]) == 4 and len(network.requests["geocode"]) == 1  # nothing re-requested
    assert second_context.usage_tracker.credits_used() == 0  # cache hits charge zero
    assert second_context.usage_tracker.calls_made() == 0
    assert {place.data_status for place in second.data} == {DataStatus.CACHED}
    assert [place.place_id for place in second.data] == [place.place_id for place in first.data]


def test_a_successful_empty_result_is_cached_briefly(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    store = ProviderCacheStore(tmp_path / "c.sqlite3")
    network = _Network(places=lambda request: [])
    response = _adapter(monkeypatch, network, store).search_restaurants("Fixtureville, Fixtureland")
    assert response.status == ProviderStatus.UNAVAILABLE and response.failure_reason is None
    assert len(network.requests["places"]) == 1

    again = _adapter(monkeypatch, network, store).search_restaurants("Fixtureville, Fixtureland")
    assert again.status == ProviderStatus.UNAVAILABLE
    assert len(network.requests["places"]) == 1  # the empty answer was remembered

    import sqlite3

    rows = sqlite3.connect(tmp_path / "c.sqlite3").execute(
        "select fetched_at, expires_at from provider_cache where source = 'geoapify_places'"
    ).fetchall()
    assert len(rows) == 1
    from datetime import datetime

    ttl = (datetime.fromisoformat(rows[0][1]) - datetime.fromisoformat(rows[0][0])).total_seconds()
    assert ttl == pytest.approx(_settings().geoapify_empty_result_cache_ttl_seconds, abs=5)
    assert ttl < _settings().osm_poi_cache_ttl_seconds  # short, negative-result TTL


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize(
    ("failure", "kind", "charged"),
    [
        (lambda request: httpx.Response(401), "auth", 0),
        (lambda request: httpx.Response(429), "rate_limited", 0),
        (lambda request: httpx.Response(503), "server", 0),
        (_timeout, "timeout", 0),
        (lambda request: httpx.Response(200, text="<html>"), "malformed", 0),
        # a well-formed 200 with an unusable body was answered by the provider, so it stays
        # charged at the reserved amount (conservative) -- but it is still never cached
        (lambda request: httpx.Response(200, json={"results": []}), "malformed", 3),
    ],
)
def test_a_failed_places_request_is_reported_as_a_places_failure_and_never_cached(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path, failure: Any, kind: str, charged: int
) -> None:
    store = ProviderCacheStore(tmp_path / "c.sqlite3")
    context = _context()
    failing = _Network(places=failure)
    response = _adapter(monkeypatch, failing, store).bound_to(context).search_restaurants("Fixtureville, Fixtureland")
    assert response.status == ProviderStatus.FAILED
    assert response.failure_reason == f"places_{kind}"
    assert response.message == "Place data provider (Geoapify Places) was unavailable."
    assert _KEY not in response.message and "Overpass" not in response.message
    assert context.usage_tracker.credits_used("places") == charged  # 0 = the reservation was released

    geoapify_client.request_breaker.reset()
    healthy = _Network(places=_by_category)
    recovered = _adapter(monkeypatch, healthy, store).search_restaurants("Fixtureville, Fixtureland")
    assert recovered.status in (ProviderStatus.SUCCESS, ProviderStatus.PARTIAL)
    assert len(healthy.requests["places"]) == 1  # nothing had been cached for the failure


# -- named places: pool identity, bounded details ------------------------------------------------------


def _named(text: str) -> dict[str, Any] | None:
    hits = {
        "Old Castle": {"name": "Old Castle", "lat": 50.0101, "lon": 10.0101, "place_id": "geo-castle"},
        "Hidden Chapel": {"name": "Hidden Chapel", "lat": 50.07, "lon": 10.07, "place_id": "geo-chapel"},
        "Second Chapel": {"name": "Second Chapel", "lat": 50.071, "lon": 10.071, "place_id": "geo-chapel2"},
    }
    for name, hit in hits.items():
        if text.startswith(name):
            return {**hit, "result_type": "amenity", "formatted": name, "rank": {"confidence": 1, "match_type": "full_match"}}
    return None


def test_a_grounded_place_already_in_the_pool_keeps_the_pool_identity_and_others_get_bounded_details(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    network = _Network(
        places=_by_category,
        named=_named,
        details=lambda place_id: {"categories": ["tourism.sights.place_of_worship.chapel"],
                                  "wiki_and_media": {"wikipedia": "en:Hidden Chapel"}},
    )
    context = _context()
    context.place_details_left = 1
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(context)
    adapter.search_attractions("Fixtureville, Fixtureland")

    in_pool = adapter.search_must_visit_place("Old Castle", "Fixtureville, Fixtureland").data[0]
    assert (in_pool.source, in_pool.place_id) == ("geoapify_places", "geoapify/s1")  # the POOL place
    assert network.requests["details"] == []  # no details needed for a pool match

    off_pool = adapter.search_must_visit_place("Hidden Chapel", "Fixtureville, Fixtureland").data[0]
    assert (off_pool.source, off_pool.place_id) == ("geoapify", "geoapify/geo-chapel")  # geocoder identity kept
    assert (off_pool.coordinates.lat, off_pool.coordinates.lng) == (50.07, 10.07)
    assert off_pool.provider_tags["building"] == "chapel" and off_pool.provider_tags["wikipedia"]
    assert len(network.requests["details"]) == 1

    # the per-generation details allowance is exhausted: still grounded, just without extra evidence
    bare = adapter.search_must_visit_place("Second Chapel", "Fixtureville, Fixtureland").data[0]
    assert bare.place_id == "geoapify/geo-chapel2" and bare.provider_tags is None
    assert len(network.requests["details"]) == 1

    assert adapter.search_must_visit_place("Invented Palace", "Fixtureville, Fixtureland").data is None
    assert context.usage_tracker.credits_used("place_details") == 1


# -- usage: reserve / settle / budget / isolation --------------------------------------------------------


def test_places_credit_rule_and_reserve_settle_release() -> None:
    assert places_request_credits(20) == 1
    assert places_request_credits(21) == 3  # conservative reservation: 1 + ceil(21 / 20)
    assert places_request_credits(21, returned=2) == 2  # reconciled to what was returned
    assert places_request_credits(60, returned=41) == 4

    tracker = ProviderUsageTracker(budget=5)
    reservation = tracker.reserve("places", 3)
    assert not tracker.can_afford(3)  # reserved credits count against the budget
    reservation.settle(2)
    assert tracker.credits_used() == 2 and tracker.can_afford(3)
    failed = tracker.reserve("routing", 3)
    failed.release()
    assert tracker.credits_used() == 2 and tracker.calls_made() == 1
    with pytest.raises(BudgetExhausted):
        tracker.reserve("places", 4)
    assert tracker.snapshot()["refused_calls"] == 1


def test_budget_exhaustion_prevents_the_call_locally(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    network = _Network(places=_by_category)
    context = _context(budget=1)  # the destination geocode uses the only credit
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(context)
    response = adapter.search_restaurants("Fixtureville, Fixtureland")

    assert response.status == ProviderStatus.FAILED
    assert response.failure_reason == "places_budget_exhausted"
    assert network.requests["places"] == []  # never sent
    assert context.usage_tracker.credits_used() == 1
    assert context.usage_tracker.snapshot()["refused_calls"] == 1


def test_concurrent_generations_have_isolated_budgets(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    network = _Network(places=_by_category)
    monkeypatch.setattr(httpx, "Client", network.client)
    store_a = ProviderCacheStore(tmp_path / "a.sqlite3")
    store_b = ProviderCacheStore(tmp_path / "b.sqlite3")
    shared_adapter_a = GeoapifyPlacesAdapter(cache_store=store_a, geocoder=GeoapifyGeocoder())
    shared_adapter_b = GeoapifyPlacesAdapter(cache_store=store_b, geocoder=GeoapifyGeocoder())
    context_a, context_b = _context(budget=2), _context(budget=100)
    results: dict[str, Any] = {}
    barrier = threading.Barrier(2)

    def _run(name: str, adapter: GeoapifyPlacesAdapter, context: GenerationProviderContext) -> None:
        barrier.wait()
        results[name] = adapter.bound_to(context).search_attractions("Fixtureville, Fixtureland")

    threads = [
        threading.Thread(target=_run, args=("a", shared_adapter_a, context_a)),
        threading.Thread(target=_run, args=("b", shared_adapter_b, context_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # A ran out of budget part-way; B was not affected and was not charged for A's calls.
    assert context_a.usage_tracker.credits_used() <= 2
    assert context_a.usage_tracker.snapshot()["refused_calls"] >= 1
    assert results["b"].status == ProviderStatus.SUCCESS
    assert context_b.usage_tracker.snapshot()["refused_calls"] == 0
    assert context_b.usage_tracker.credits_used("places") == 5
    assert context_b.usage_tracker.credits_used("geocoding") == 1
    # the process-level adapters themselves carry no usage state
    assert shared_adapter_a._usage is None and shared_adapter_b._usage is None


def test_production_refuses_a_credit_spending_call_outside_a_generation(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    monkeypatch.setattr(geoapify_client, "is_production", lambda: True)
    network = _Network()
    with pytest.raises(ProviderRequestError) as excinfo:
        geoapify_client.geoapify_get(
            network.client(), base_url="https://api.geoapify.com", path="/v2/places", params={},
            api_key=_KEY, timeout=5.0, api="places", usage=None,
        )
    assert excinfo.value.kind == "no_generation_context"
    assert network.requests["places"] == []


# -- routing ----------------------------------------------------------------------------------------------


def _route_response(request: httpx.Request) -> httpx.Response:
    waypoints = request.url.params["waypoints"].split("|")
    legs = [{"distance": 1000.0 * (index + 1), "time": 600.0 * (index + 1)} for index in range(len(waypoints) - 1)]
    lines = [[[10.0 + index, 50.0], [10.0 + index + 1, 50.0]] for index in range(len(legs))]
    return httpx.Response(
        200,
        json={"features": [{"properties": {"legs": legs, "distance": 0, "time": 0},
                            "geometry": {"type": "MultiLineString", "coordinates": lines}}]},
    )


def _routing(monkeypatch: pytest.MonkeyPatch, network: _Network, store: ProviderCacheStore) -> GeoapifyRoutingAdapter:
    monkeypatch.setattr(httpx, "Client", network.client)
    return GeoapifyRoutingAdapter(cache_store=store)


_DAY = [(50.0, 10.0), (50.0, 11.0), (50.0, 12.0)]


def test_one_multi_waypoint_request_routes_a_whole_day(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    network = _Network(routing=_route_response)
    context = _context()
    adapter = _routing(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(context)
    results = adapter.get_route_sequence(_DAY)

    assert len(network.requests["routing"]) == 1
    params = network.requests["routing"][0].url.params
    assert params["waypoints"] == "50.0,10.0|50.0,11.0|50.0,12.0" and params["mode"] == "walk"
    assert [(r.status, r.distance_meters, r.duration_seconds) for r in results] == [
        (ProviderStatus.SUCCESS, 1000.0, 600.0),
        (ProviderStatus.SUCCESS, 2000.0, 1200.0),
    ]
    assert [(point.lat, point.lon) for point in results[0].geometry] == [(50.0, 10.0), (50.0, 11.0)]
    assert context.usage_tracker.credits_used("routing") == 2  # one credit per waypoint pair

    # later stages read the same legs back: no further request, no further credit
    leg = adapter.get_route(RouteRequest(origin_lat=50.0, origin_lon=11.0, destination_lat=50.0, destination_lon=12.0))
    assert leg.duration_seconds == 1200.0
    assert adapter.get_route_sequence(_DAY)[1].distance_meters == 2000.0
    assert len(network.requests["routing"]) == 1 and context.usage_tracker.credits_used("routing") == 2

    # a NEW generation is served from the provider cache at zero cost
    other = _context()
    cached = _routing(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(other)
    assert cached.get_route_sequence(_DAY)[0].duration_seconds == 600.0
    assert len(network.requests["routing"]) == 1 and other.usage_tracker.credits_used() == 0


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (lambda request: httpx.Response(401), ProviderStatus.FAILED),
        (lambda request: httpx.Response(429), ProviderStatus.FAILED),
        (lambda request: httpx.Response(500), ProviderStatus.FAILED),
        (_timeout, ProviderStatus.FAILED),
        (lambda request: httpx.Response(200, text="oops"), ProviderStatus.FAILED),
        (lambda request: httpx.Response(200, json={"features": []}), ProviderStatus.UNAVAILABLE),
        (lambda request: httpx.Response(200, json={"features": [{"properties": {"legs": [{}]}}]}), ProviderStatus.UNAVAILABLE),
    ],
)
def test_routing_failures_are_honest_and_never_estimated_or_cached(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path, failure: Any, status: ProviderStatus
) -> None:
    store = ProviderCacheStore(tmp_path / "c.sqlite3")
    context = _context()
    results = _routing(monkeypatch, _Network(routing=failure), store).bound_to(context).get_route_sequence(_DAY)
    assert [result.status for result in results] == [status, status]
    for result in results:
        assert result.distance_meters is None and result.duration_seconds is None and result.geometry is None
        assert _KEY not in (result.message or "") and "http" not in (result.message or "")
    if status == ProviderStatus.FAILED:
        assert context.usage_tracker.credits_used("routing") == 0

    geoapify_client.request_breaker.reset()
    healthy = _Network(routing=_route_response)
    assert _routing(monkeypatch, healthy, store).get_route_sequence(_DAY)[0].status == ProviderStatus.SUCCESS
    assert len(healthy.requests["routing"]) == 1  # the failure had not been cached


def test_routing_requests_are_bounded_per_generation(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    assert [route_request_allowance(days) for days in (1, 3, 5)] == [6, 10, 14]
    network = _Network(routing=_route_response)
    context = _context(trip_days=1)
    context.route_requests_left = 2
    adapter = _routing(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(context)

    assert adapter.get_route_sequence([(50.0, 10.0), (50.0, 11.0)])[0].status == ProviderStatus.SUCCESS
    assert adapter.get_route_sequence([(50.0, 20.0), (50.0, 21.0)])[0].status == ProviderStatus.SUCCESS
    refused = adapter.get_route_sequence([(50.0, 30.0), (50.0, 31.0)])[0]
    assert refused.status == ProviderStatus.UNAVAILABLE
    assert refused.message == "The route request budget for this generation was reached."
    assert len(network.requests["routing"]) == 2


def test_routing_without_a_key_is_not_connected_and_the_gateway_binds_per_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(routing_module, "get_settings", lambda: Settings(_env_file=None))
    network = _Network(routing=_route_response)
    adapter = _routing(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3"))
    assert adapter.get_route_sequence(_DAY)[0].status == ProviderStatus.NOT_CONNECTED
    assert network.requests["routing"] == []

    gateway = ProviderGateway(routing=adapter)
    assert gateway.routing_for(None) is adapter
    bound = gateway.routing_for(_context())
    assert bound is not adapter and bound._context is not None and adapter._context is None


def test_category_rename_examples() -> None:
    assert taxonomy_tags_from_categories(["tourism", "tourism.sights", "tourism.sights.memorial"]) == {
        "historic": "memorial", "category_path": "tourism.sights.memorial",
    }
    assert taxonomy_tags_from_categories(["commercial.marketplace", "no_access"])["access"] == "no"
    assert taxonomy_tags_from_categories(["fee", "wheelchair.yes"]) == {}


def test_the_live_places_response_shape_maps_cleanly_without_wiki_fields_or_extra_details_calls(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    """Regression fixture for the shape observed in the live check (2026-10-02):
    `datasource.raw` present with only a few keys (building, lat, lon, name,
    osm_id, osm_type, tourism), `categories` present, `wiki_and_media` absent.
    A second place has no `datasource.raw` at all. Both must map from whatever
    factual fields exist, and neither may trigger a Place Details call."""

    def _places(request: httpx.Request) -> list[dict[str, Any]]:
        if "tourism.sights" not in request.url.params["categories"]:
            return []
        return [
            _feature(
                "live1", "Harbour Fort", ["building", "building.tourism", "tourism", "tourism.sights", "tourism.sights.fort"],
                50.01, 10.01,
                datasource={
                    "sourcename": "openstreetmap",
                    "raw": {"building": "yes", "lat": 50.01, "lon": 10.01, "name": "Harbour Fort",
                            "osm_id": 123456, "osm_type": "w", "tourism": "attraction"},
                },
            ),
            _feature("live2", "Quiet Square", ["tourism", "tourism.sights"], 50.02, 10.02, datasource={"sourcename": "openstreetmap"}),
        ]

    network = _Network(places=_places)
    context = _context()
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(context)
    places = {place.name: place for place in adapter.search_attractions("Fixtureville, Fixtureland").data}

    fort = places["Harbour Fort"]
    # raw OSM tags are used where present (whitelisted keys only) on top of the category rename
    assert fort.provider_tags == {
        "historic": "fort", "tourism": "attraction", "building": "yes", "category_path": "tourism.sights.fort",
    }
    assert (fort.place_id, fort.source) == ("geoapify/live1", "geoapify_places")  # Geoapify identity, not the OSM id
    assert "wikipedia" not in fort.provider_tags and "wikidata" not in fort.provider_tags  # nothing invented

    # no raw tags and no wiki fields: degrades to the categories alone
    square = places["Quiet Square"]
    assert square.provider_tags == {"category_path": "tourism.sights"}
    assert square.category == "sights"

    quality = CandidateQualityService()
    assert quality.score_attraction(fort).quality_tier != CandidateQualityTier.REJECTED
    assert quality.score_attraction(square).quality_tier != CandidateQualityTier.REJECTED
    assert quality.score_attraction(fort).total_score > quality.score_attraction(square).total_score

    # a broad Places result never triggers Place Details, wiki fields or not
    assert network.requests["details"] == []
    assert context.usage_tracker.credits_used("place_details") == 0


# -- generalization correction: mixed-mode routing ------------------------------------------------------


def _mode_route_response(request: httpx.Request) -> httpx.Response:
    """A walking route of 7500 s per leg, a driving route of 900 s per leg."""
    waypoints = request.url.params["waypoints"].split("|")
    drive = request.url.params["mode"] == "drive"
    legs = [
        {"distance": 9000.0 if drive else 7500.0, "time": 900.0 if drive else 7500.0}
        for _ in range(len(waypoints) - 1)
    ]
    return httpx.Response(200, json={"features": [{"properties": {"legs": legs}, "geometry": None}]})


def test_a_driving_route_is_a_separate_request_cache_entry_usage_label_and_cap(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    network = _Network(routing=_mode_route_response)
    store = ProviderCacheStore(tmp_path / "c.sqlite3")
    context = _context()
    context.alternate_mode_requests_left = 1
    gateway = ProviderGateway(routing=_routing(monkeypatch, network, store))
    leg = [(50.0, 10.0), (50.0, 11.0)]

    walk = gateway.get_route_sequence(leg, context)[0]
    assert (walk.mode, walk.duration_seconds) == ("walk", 7500.0)
    day_routes_left = context.route_requests_left

    drive = gateway.get_alternate_mode_route(leg[0], leg[1], context)
    assert (drive.mode, drive.status, drive.distance_meters, drive.duration_seconds) == (
        "drive", ProviderStatus.SUCCESS, 9000.0, 900.0,
    )
    assert [request.url.params["mode"] for request in network.requests["routing"]] == ["walk", "drive"]
    # its own cap and its own usage label; the day-route allowance is untouched
    assert context.alternate_mode_requests_left == 0 and context.route_requests_left == day_routes_left
    assert context.usage_tracker.credits_used("routing") == 1
    assert context.usage_tracker.credits_used("routing_drive") == 1

    # coordinates + mode is the cache key: each mode is read back as itself, with no further request
    assert gateway.get_alternate_mode_route(leg[0], leg[1], context).duration_seconds == 900.0
    assert gateway.get_route_sequence(leg, context)[0].duration_seconds == 7500.0
    assert len(network.requests["routing"]) == 2

    # the cap is used up: a driving route for ANOTHER leg is refused locally, never requested
    refused = gateway.get_alternate_mode_route((50.0, 20.0), (50.0, 21.0), context)
    assert refused.status == ProviderStatus.UNAVAILABLE and refused.distance_meters is None
    assert len(network.requests["routing"]) == 2 and context.usage_tracker.credits_used() == 2

    # a NEW generation reads both modes from the provider cache at zero cost
    other = _context()
    cached = ProviderGateway(routing=_routing(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")))
    assert cached.get_alternate_mode_route(leg[0], leg[1], other).mode == "drive"
    assert len(network.requests["routing"]) == 2 and other.usage_tracker.credits_used() == 0


def test_a_rebuilt_route_report_never_pays_for_the_same_leg_twice_in_either_mode(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    network = _Network(routing=_mode_route_response)
    context = _context(trip_days=1)
    gateway = ProviderGateway(routing=_routing(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")))
    stops = [
        ExperienceItem(
            experience_id=f"s{index}", name=f"Stop {index}", category="museum",
            coordinates=GeoPoint(lat=50.0, lng=10.0 + index), provider_place_id=f"geoapify/s{index}",
            provider_source="geoapify_places",
        )
        for index in range(2)
    ]
    state = PlanningState(
        trip_request=TripRequest(
            primary_destination="Fixtureville, Fixtureland", start_date="2026-11-10", end_date="2026-11-10",
            travelers_count=2, travel_group_type=TravelGroupType.COUPLE,
        )
    )
    state.experience_plan = ExperiencePlan(daily_plans=[DailyPlan(day_number=1, date="2026-11-10", experiences=stops)])
    service = RouteFeasibilityService(gateway=gateway)

    first = service.build_report(state, context)
    second = service.build_report(state, context)  # e.g. after sequencing or a repair

    for report in (first, second):
        leg = report.legs[0]
        assert (leg.mode, leg.duration_seconds, leg.walking_duration_seconds) == ("drive", 900.0, 7500.0)
        assert leg.provider == "geoapify_routing" and leg.status == ProviderStatus.SUCCESS
    # one walking day-route request and ONE driving request for the long leg, in total
    assert [request.url.params["mode"] for request in network.requests["routing"]] == ["walk", "drive"]
    assert context.usage_tracker.credits_used("routing") == 1
    assert context.usage_tracker.credits_used("routing_drive") == 1
    assert context.alternate_mode_requests_left == 5


# -- generalization correction: one candidate per real entity -------------------------------------------


def _osm(osm_type: str, osm_id: int, **raw: Any) -> dict[str, Any]:
    return {"sourcename": "openstreetmap", "raw": {"osm_type": osm_type, "osm_id": osm_id, **raw}}


def test_one_real_entity_under_an_english_and_a_local_script_name_is_one_candidate(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    """The same source object reaches the pool twice -- once per category
    query, once under an English name and once under a local-script name,
    with different Geoapify place ids -- and is grounded again by name."""
    local_name = "खगोल वेधशाला"

    def _places(request: httpx.Request) -> list[dict[str, Any]]:
        categories = request.url.params["categories"]
        if "tourism.sights" in categories:
            return [
                _feature("obs-en", "Royal Observatory", ["tourism", "tourism.sights"], 50.0100, 10.0100,
                         datasource=_osm("w", 4711, tourism="attraction", name=local_name)),
                # a DIFFERENT source object 30 m away with a different name: never merged
                _feature("gate", "Observatory Gate", ["tourism", "tourism.sights"], 50.0102, 10.0102,
                         datasource=_osm("n", 9001, historic="city_gate")),
            ]
        if "entertainment.museum" in categories:
            return [
                _feature("obs-local", local_name, ["entertainment", "entertainment.museum"], 50.0101, 10.0101,
                         datasource=_osm("way", "4711", tourism="museum")),
            ]
        return []

    def _named(text: str) -> dict[str, Any] | None:
        if not text.startswith("Royal Astronomical Observatory"):
            return None
        return {"name": "Royal Astronomical Observatory", "lat": 50.01005, "lon": 10.01005, "place_id": "geo-obs",
                "result_type": "amenity", "formatted": "Royal Astronomical Observatory",
                "rank": {"confidence": 1, "match_type": "full_match"}}

    network = _Network(
        places=_places,
        named=_named,
        # Place Details reveals the grounded place's source object
        details=lambda place_id: {"categories": ["tourism.sights"], "datasource": _osm("W", 4711, tourism="attraction")},
    )
    context = _context()
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(context)
    pool = adapter.search_attractions("Fixtureville, Fixtureland").data

    assert sorted(place.name for place in pool) == ["Observatory Gate", "Royal Observatory"]
    observatory = next(place for place in pool if place.name == "Royal Observatory")
    # the public identity is still the Geoapify place id; the source identity is internal only
    assert (observatory.place_id, observatory.source) == ("geoapify/obs-en", "geoapify_places")
    assert observatory.source_entity_id == "osm/way/4711"
    dumped = observatory.model_dump(mode="json")
    assert "source_entity_id" not in dumped and "alt_names" not in dumped
    assert "osm" not in str(dumped) and "raw" not in dumped

    # a named lookup under a THIRD name is recognised as the same entity through its source identity
    grounded = adapter.search_must_visit_place("Royal Astronomical Observatory", "Fixtureville, Fixtureland").data[0]
    assert grounded.place_id == "geoapify/obs-en"
    assert context.entity_merges == {"source_identity": 2}

    # the identity survives the provider cache, so a later generation de-duplicates the same way
    other = _context()
    cached = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(other)
    assert sorted(p.name for p in cached.search_attractions("Fixtureville, Fixtureland").data) == [
        "Observatory Gate", "Royal Observatory",
    ]
    assert other.entity_merges == {"source_identity": 1} and other.usage_tracker.credits_used("places") == 0


# -- live cleanup: suspected duplicate candidates with no identity evidence -----------------------------

_HALL_LOCAL_NAME = "बड़ा हॉल"
_COLLISION_KEYS = {
    "place_ids", "name_variants", "coarse_classes", "separation_meters", "source_identity_present",
    "wikidata_identity_present", "enrichment_attempted", "resolution", "merged_by",
}


def _co_located_museums(request: httpx.Request) -> list[dict[str, Any]]:
    """Two museum records at the same coordinates under an English and a
    local-script name: different place ids, no source identity, no Wikidata,
    no name in common -- nothing any identity rule can use."""
    if "entertainment.museum" not in request.url.params["categories"]:
        return []
    museum = ["entertainment", "entertainment.museum"]
    return [
        _feature("hall-en", "Grand Hall Museum", museum, 50.02, 10.02),
        _feature("hall-local", _HALL_LOCAL_NAME, museum, 50.02, 10.02),
        _feature("other", "Tram Museum", museum, 50.05, 10.05),  # a different museum 4 km away
    ]


def _collision_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, details: Callable[[str], dict[str, Any]]):
    network = _Network(places=_co_located_museums, details=details)
    context = _context()
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "c.sqlite3")).bound_to(context)
    return adapter.search_attractions("Fixtureville, Fixtureland").data, context, network


def test_a_co_located_pair_is_merged_when_details_corroborate_the_same_entity(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    museum = ["entertainment", "entertainment.museum"]
    pool, context, network = _collision_run(
        monkeypatch, tmp_path,
        # Place Details shows both records are the same source object
        lambda place_id: {"categories": museum, "datasource": _osm("w", 77, tourism="museum")},
    )

    assert sorted(place.name for place in pool) == ["Grand Hall Museum", "Tram Museum"]
    assert context.entity_merges == {"source_identity": 1}
    record = context.suspect_collisions[0]
    assert (record["resolution"], record["merged_by"], record["enrichment_attempted"]) == (
        "merged", "source_identity", True,
    )
    assert record["place_ids"] == ["geoapify/hall-en", "geoapify/hall-local"]
    assert record["name_variants"] == [["Grand Hall Museum"], [_HALL_LOCAL_NAME]]
    assert record["separation_meters"] == 0.0 and record["coarse_classes"] == ["museum_culture", "museum_culture"]
    # bounded: one lookup per member, inside both allowances
    assert len(network.requests["details"]) == 2 and context.identity_lookups_left == 2
    assert context.place_details_left == 10 and context.usage_tracker.credits_used("place_details") == 2
    # diagnostics are sanitised: fixed keys, yes/no identity flags, no provider payload
    assert set(record) == _COLLISION_KEYS and record["source_identity_present"] == [True, True]
    assert "osm" not in str(record) and "77" not in str(record) and "raw" not in str(record)


def test_two_genuinely_different_co_located_places_stay_separate(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    museum = ["entertainment", "entertainment.museum"]
    wikidata = {"hall-en": "Q100", "hall-local": "Q200"}
    pool, context, _ = _collision_run(
        monkeypatch, tmp_path,
        lambda place_id: {"categories": museum, "wiki_and_media": {"wikidata": wikidata[place_id]},
                          "datasource": _osm("n", 1 if place_id == "hall-en" else 2)},
    )

    assert sorted(place.name for place in pool) == sorted(["Grand Hall Museum", _HALL_LOCAL_NAME, "Tram Museum"])
    assert context.entity_merges == {}  # being in the same spot is never a reason to merge
    record = context.suspect_collisions[0]
    assert record["resolution"] == "distinct" and record["wikidata_identity_present"] == [True, True]

    # marked on nothing: two distinct places may both be scheduled
    destination = DestinationContext(
        destination_name="Fixtureville, Fixtureland", candidate_pois=[p.model_dump(mode="json") for p in pool]
    )
    apply_suspect_collisions(destination, context)
    assert not any(SUSPECT_COLLISION_KEY in poi for poi in destination.candidate_pois)


def test_an_unresolvable_co_located_pair_is_never_merged_but_is_marked_for_the_scheduler(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    # Place Details adds nothing usable; then the lookup allowance runs out entirely
    for lookups in (4, 0):
        network = _Network(places=_co_located_museums, details=lambda place_id: {})
        context = _context()
        context.identity_lookups_left = lookups
        adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / f"c{lookups}.sqlite3")).bound_to(context)
        pool = adapter.search_attractions("Fixtureville, Fixtureland").data

        assert len(pool) == 3 and context.entity_merges == {}  # not silently merged
        record = context.suspect_collisions[0]
        assert record["resolution"] == "unresolved" and record["enrichment_attempted"] == (lookups > 0)
        assert record["source_identity_present"] == [False, False]
        assert len(network.requests["details"]) == (2 if lookups else 0)

        destination = DestinationContext(
            destination_name="Fixtureville, Fixtureland", candidate_pois=[p.model_dump(mode="json") for p in pool]
        )
        apply_suspect_collisions(destination, context)
        groups = {poi["name"]: poi.get(SUSPECT_COLLISION_KEY) for poi in destination.candidate_pois}
        assert groups["Grand Hall Museum"] == groups[_HALL_LOCAL_NAME] is not None
        assert groups["Tram Museum"] is None
        assert destination.suspect_entity_collisions == context.suspect_collisions
        assert "raw" not in destination.model_dump_json() and "datasource" not in destination.model_dump_json()
