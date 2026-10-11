from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.models.common import GeoPoint, ProviderStatus
from app.providers.places import geoapify_categories as categories
from app.providers.places import geoapify_places_adapter as places_module
from app.providers.places.geoapify_places_adapter import (
    SOURCE_BROAD,
    SOURCE_DESTINATION_LOCAL,
    SOURCE_INTEREST,
    SOURCE_MUST_VISIT_LOCAL,
    GeoapifyPlacesAdapter,
    places_request_credits,
)
from app.services import place_taxonomy as taxonomy
from app.services.destination_context_service import DestinationContextService
from app.services.must_visit_matching import grounded_terms, record_grounded_term
from app.storage.provider_cache_store import ProviderCacheStore
from app.tests.providers.test_geoapify_places_routing_203c2b import (
    _adapter,
    _context,
    _feature,
    _Network,
    configured,  # noqa: F401  (fixture)
)

# Mixed local / broad discovery (quality tuning corrections; docs/24_itinerary_quality_contract.md):
# each attraction group's limit is split into a BROAD request (the destination filter, no bias) and
# LOCAL requests (the same filter with a proximity bias towards a locality anchor). Synthetic
# provider only -- no city, no real place, no live call.

_CITY = "Fixtureville, Fixtureland"
_CENTRE = (50.0, 10.0)
_SIGHTS, _ATTRACTIONS = "tourism.sights", "tourism.attraction"
_CULTURE, _PARKS, _FOOD = "entertainment.museum", "leisure.park", "catering"


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    d_lat, d_lon = lat2 - lat1, math.radians(b[1] - a[1])
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class _Provider:
    """A stand-in for the provider's Places search over a fixed catalogue: the requested
    categories select records, a proximity bias orders them nearest first (without one they
    come in catalogue order, which is unrelated to distance), then offset and limit apply."""

    def __init__(self, catalogue: dict[str, list[dict[str, Any]]]) -> None:
        self.catalogue = catalogue

    def __call__(self, request: httpx.Request) -> list[dict[str, Any]]:
        params = request.url.params
        records = next((rows for key, rows in self.catalogue.items() if key in params["categories"]), [])
        if "bias" in params:
            lon, lat = (float(value) for value in params["bias"].removeprefix("proximity:").split(","))
            records = sorted(
                records, key=lambda r: _km((lat, lon), (r["properties"]["lat"], r["properties"]["lon"]))
            )
        offset, limit = int(params["offset"]), int(params["limit"])
        return records[offset : offset + limit]


def _rows(prefix: str, leaf: list[str], count: int, lat: float, lon: float, **extra: Any) -> list[dict[str, Any]]:
    """`count` records in a tight cluster around (lat, lon)."""
    return [
        _feature(f"{prefix}{index}", f"{prefix.title()} {index}", leaf, lat + 0.0005 * index, lon + 0.0005 * index, **extra)
        for index in range(count)
    ]


# Far corner of the destination box (about 12 km from the centre) and two more distinct areas.
_REMOTE = (49.905, 9.905)
_NORTH = (50.085, 10.0)  # about 9.5 km north of the centre
_EAST = (50.0, 10.09)  # about 6.4 km east


def _region_wide_catalogue() -> dict[str, list[dict[str, Any]]]:
    """Every group lists its remote records FIRST, so an unbiased request sees only those --
    the shape a bounded probe observed for a destination resolved to a large boundary."""
    museum = ["entertainment", "entertainment.museum"]
    return {
        _SIGHTS: [
            *_rows("farsight", ["tourism", "tourism.sights", "tourism.sights.castle"], 25, *_REMOTE),
            *_rows("memorial", ["tourism", "tourism.sights", "tourism.sights.memorial"], 12, *_CENTRE),
        ],
        _ATTRACTIONS: [
            *_rows("farview", ["tourism", "tourism.attraction", "tourism.attraction.viewpoint"], 12, *_REMOTE),
            *_rows("artwork", ["tourism", "tourism.attraction", "tourism.attraction.artwork"], 8, *_CENTRE),
        ],
        _CULTURE: [
            *_rows("farmuseum", museum, 20, *_REMOTE),
            *_rows("museum", museum, 20, *_CENTRE),
            *_rows("northmuseum", museum, 8, *_NORTH),
            *_rows("eastmuseum", museum, 8, *_EAST),
        ],
        _PARKS: [
            *_rows("farpark", ["leisure", "leisure.park"], 15, *_REMOTE),
            *_rows("park", ["leisure", "leisure.park"], 10, *_CENTRE),
            *_rows("northpark", ["leisure", "leisure.park"], 5, *_NORTH),
        ],
        _FOOD: [
            *_rows("farcafe", ["catering", "catering.cafe"], 30, *_REMOTE),
            *_rows("cafe", ["catering", "catering.cafe"], 20, *_CENTRE),
            *_rows("northcafe", ["catering", "catering.cafe"], 6, *_NORTH),
        ],
        "accommodation": _rows("hotel", ["accommodation", "accommodation.hotel"], 4, *_CENTRE),
    }


def _sent(network: _Network) -> list[dict[str, Any]]:
    """Every Places request as plain facts, in the order sent (arrival order of a serial run)."""
    return [
        {
            "categories": request.url.params["categories"],
            "limit": int(request.url.params["limit"]),
            "offset": int(request.url.params["offset"]),
            "bias": request.url.params.get("bias"),
            "filter": request.url.params["filter"],
        }
        for request in network.requests["places"]
    ]


def _of(sent: list[dict[str, Any]], key: str, biased: bool | None = None) -> list[dict[str, Any]]:
    return [r for r in sent if key in r["categories"] and (biased is None or (r["bias"] is not None) == biased)]


@pytest.fixture()
def serial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requests one after another, so the order they are SENT in is the planned order."""
    monkeypatch.setenv("PROVIDER_IO_CONCURRENCY_ENABLED", "false")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, hold: bool = False, interests: list[str] | None = None,
         budget: int = 100, catalogue: dict[str, list[dict[str, Any]]] | None = None) -> tuple[Any, ...]:
    network = _Network(places=_Provider(catalogue or _region_wide_catalogue()))
    context = _context(budget)
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "cache.sqlite3")).bound_to(context)
    attraction_filters: dict[str, Any] = {"pool_size": 60}
    food_filters: dict[str, Any] = {"pool_size": 30}
    if interests:
        attraction_filters["interest_groups"] = interests
    if hold:
        attraction_filters["hold_back_for_must_visits"] = True
        food_filters["hold_back_for_must_visits"] = True
    responses = adapter.search_broad_inventory(_CITY, attraction_filters, food_filters)
    return adapter, network, context, responses


# =====================================================================================
# 1. The fixed allocation
# =====================================================================================


def test_the_split_is_exact_and_the_pool_is_no_larger(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    _, network, context, (attractions, restaurants, _stay) = _run(monkeypatch, tmp_path)
    sent = _sent(network)

    expected = {_SIGHTS: (14, 7), _ATTRACTIONS: (6, 3), _CULTURE: (6, 12), _PARKS: (6, 6)}
    for key, (broad, local) in expected.items():
        assert [r["limit"] for r in _of(sent, key, biased=False)] == [broad], key
        assert [r["limit"] for r in _of(sent, key, biased=True)] == [local], key
    attraction_requests = [r for r in sent if _FOOD not in r["categories"] and "accommodation" not in r["categories"]]
    assert sum(r["limit"] for r in attraction_requests) == 60 and len(attraction_requests) == 8
    assert sum(r["limit"] for r in attraction_requests if r["bias"] is None) == 32
    assert sum(r["limit"] for r in attraction_requests if r["bias"] is not None) == 28
    # restaurants: half broad, half local, the same allowance
    assert [r["limit"] for r in _of(sent, _FOOD, biased=False)] == [15]
    assert [r["limit"] for r in _of(sent, _FOOD, biased=True)] == [15]
    # accommodation is untouched: one unbiased request
    assert [(r["limit"], r["bias"]) for r in _of(sent, "accommodation")] == [(20, None)]

    # the SAME geographic filter for both kinds; the bias is the destination point (lon,lat); nothing is paged
    assert {r["filter"] for r in sent} == {"place:dest01"}
    assert {r["bias"] for r in sent if r["bias"]} == {"proximity:10.0,50.0"}
    assert {r["offset"] for r in sent} == {0}
    # deterministic order: every broad attraction request, then every local one
    kinds = ["local" if r["bias"] else "broad" for r in attraction_requests]
    assert kinds == ["broad"] * 4 + ["local"] * 4
    # nothing is held back for a trip without must-visits, and a follow-up then asks for nothing
    assert context.held_place_requests == []
    assert attractions.status == ProviderStatus.SUCCESS and restaurants.status == ProviderStatus.SUCCESS


def test_local_shares_are_fixed_fractions_of_any_pool_size() -> None:
    plan = GeoapifyPlacesAdapter._attraction_plan
    for pool in (60, 75, 110):
        totals = dict((group.key, limit) for group, limit in plan({"pool_size": pool}))
        for key, limit in totals.items():
            local, held = categories.local_limit(key, limit), categories.held_back_limit(key, limit)
            assert 0 < local < limit and 0 <= held <= local // 2 + 1
            assert held == (local // 2 if key in categories.MUST_VISIT_FOLLOW_UP_GROUPS else 0)
    assert [categories.local_limit(key, limit) for key, limit in
            (("sights", 21), ("attractions", 9), ("culture", 18), ("outdoors_heritage_markets", 12), ("food", 30))] == [7, 3, 12, 6, 15]
    assert [categories.held_back_limit(key, limit) for key, limit in
            (("sights", 21), ("attractions", 9), ("culture", 18), ("outdoors_heritage_markets", 12), ("food", 30))] == [0, 0, 6, 3, 7]
    assert categories.local_limit("accommodation", 20) == 0 == categories.held_back_limit("accommodation", 20)


def test_an_expansion_page_and_an_unknown_group_are_unchanged(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    network = _Network(places=_Provider(_region_wide_catalogue()))
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "cache.sqlite3")).bound_to(_context())
    adapter.search_attractions(_CITY, {"pool_size": 60, "page": 1})
    sent = _sent(network)
    assert [(r["limit"], r["offset"], r["bias"]) for r in sent] == [(21, 21, None), (9, 9, None), (18, 18, None), (12, 12, None)]


# =====================================================================================
# 2. A region-wide pool with a central activity area
# =====================================================================================


def test_a_region_wide_provider_now_yields_central_candidates_and_keeps_regional_ones(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    _, _, context, (attractions, restaurants, _stay) = _run(monkeypatch, tmp_path)

    def near_centre(place: Any) -> bool:
        return _km(_CENTRE, (place.coordinates.lat, place.coordinates.lng)) <= 3.0

    central = [place for place in attractions.data if near_centre(place)]
    regional = [place for place in attractions.data if not near_centre(place)]
    # before the split every one of these 60 records was the remote kind
    assert len(central) == 28 and len(regional) == 32 and len(attractions.data) == 60
    assert {place.place_id.split("/")[1].rstrip("0123456789") for place in regional} == {
        "farsight", "farview", "farmuseum", "farpark",
    }  # intentionally distant places remain available: the broad share is fixed
    assert sum(near_centre(place) for place in restaurants.data) == 15 and len(restaurants.data) == 30
    # applied in plan order: every broad record before any local one, whatever was requested when
    sources = ["local" if near_centre(place) else "broad" for place in attractions.data]
    assert sources == sorted(sources)
    assert [place.place_id for place in context.place_pool] == [place.place_id for place in attractions.data]


def test_low_value_local_objects_do_not_displace_regional_attractions(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    _, _, _, (attractions, _restaurants, _stay) = _run(monkeypatch, tmp_path)
    by_prefix: dict[str, list[Any]] = {}
    for place in attractions.data:
        by_prefix.setdefault(place.place_id.split("/")[1].rstrip("0123456789"), []).append(place)
    # the local attraction and sights shares came back as single artworks and memorials ...
    assert len(by_prefix["artwork"]) == 3 and len(by_prefix["memorial"]) == 7
    assert all(taxonomy.classify_place(p.provider_tags, p.category).low_value for p in by_prefix["artwork"])
    # ... which the existing rules already mark; the regional records of the same groups are all still there
    assert len(by_prefix["farview"]) == 6 and len(by_prefix["farsight"]) == 14
    assert not any(taxonomy.classify_place(p.provider_tags, p.category).low_value for p in by_prefix["farview"])


def test_short_local_and_empty_broad_responses_never_raise_a_limit(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    thin = {
        _CULTURE: _rows("museum", ["entertainment", "entertainment.museum"], 4, *_CENTRE),  # 4 for a local share of 12
        _PARKS: [],  # nothing at all, broad or local
    }
    _, network, context, (attractions, _restaurants, _stay) = _run(monkeypatch, tmp_path, catalogue=thin)
    sent = _sent(network)
    assert len(attractions.data) == 4  # the same four records, seen by the broad and the local request, once
    assert context.entity_merges == {}  # one record seen twice is not two candidates merged
    assert len(sent) == 11 and sum(r["limit"] for r in sent if "accommodation" not in r["categories"] and _FOOD not in r["categories"]) == 60
    assert attractions.status == ProviderStatus.SUCCESS


# =====================================================================================
# 3. Must-visit locality
# =====================================================================================


def _held(context: Any) -> list[tuple[str, str, int, int]]:
    return list(context.held_place_requests)


def test_part_of_the_local_share_is_held_back_only_for_a_trip_with_must_visits(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    _, network, context, _ = _run(monkeypatch, tmp_path, hold=True)
    sent = _sent(network)
    assert [r["limit"] for r in _of(sent, _CULTURE, biased=True)] == [6]  # 12 local, 6 held
    assert [r["limit"] for r in _of(sent, _PARKS, biased=True)] == [3]  # 6 local, 3 held
    assert [r["limit"] for r in _of(sent, _FOOD, biased=True)] == [8]  # 15 local, 7 held
    assert [r["limit"] for r in _of(sent, _SIGHTS, biased=True)] == [7]  # sights and attractions are never held
    assert [r["limit"] for r in _of(sent, _ATTRACTIONS, biased=True)] == [3]
    assert _held(context) == [
        ("attractions", "culture", 6, 6), ("attractions", "outdoors_heritage_markets", 3, 3), ("restaurants", "food", 7, 8),
    ]
    # requested now + held = the same allocation as a trip without must-visits
    attraction_limits = sum(
        r["limit"] for r in sent if _FOOD not in r["categories"] and "accommodation" not in r["categories"]
    )
    assert attraction_limits + 6 + 3 == 60


def _follow_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, points: list[tuple[float, float]]) -> tuple[Any, ...]:
    adapter, network, context, responses = _run(monkeypatch, tmp_path, hold=True)
    before = len(network.requests["places"])
    found = adapter.search_must_visit_local_inventory(_CITY, [GeoPoint(lat=lat, lng=lon) for lat, lon in points])
    return adapter, context, responses, found, _sent(network)[before:]


def test_two_distinct_grounded_must_visits_each_get_a_share_of_the_follow_up(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    third = (49.92, 10.08)  # a third far-apart must-visit: only the first two distinct anchors are used
    _, context, (attractions, _restaurants, _stay), (new_attractions, new_restaurants), sent = _follow_up(
        monkeypatch, tmp_path, [_NORTH, _EAST, third]
    )
    north, east = "proximity:10.0,50.085", "proximity:10.09,50.0"
    assert [(r["limit"], r["bias"], r["offset"]) for r in _of(sent, _CULTURE)] == [(3, north, 0), (3, east, 0)]
    assert [(r["limit"], r["bias"], r["offset"]) for r in _of(sent, _PARKS)] == [(2, north, 0), (1, east, 0)]
    assert [(r["limit"], r["bias"], r["offset"]) for r in _of(sent, _FOOD)] == [(4, north, 0), (3, east, 0)]
    assert len(sent) == 6 and all(r["filter"] == "place:dest01" for r in sent)  # the same geographic filter
    assert context.held_place_requests == []  # spent once

    prefixes = [place.place_id.split("/")[1].rstrip("0123456789") for place in new_attractions]
    assert prefixes.count("northmuseum") == 3 and prefixes.count("eastmuseum") == 3 and prefixes.count("northpark") == 2
    # (the single park slot of the second anchor returned a record another request already supplied)
    assert all(place.place_id not in {p.place_id for p in attractions.data} for place in new_attractions)
    assert {place.place_id.split("/")[1].rstrip("0123456789") for place in new_restaurants} >= {"northcafe"}
    # the follow-up places join the generation's pool after the first batch, in request order
    assert [p.place_id for p in context.place_pool[len(attractions.data):]] == [p.place_id for p in new_attractions]


def test_one_distinct_must_visit_takes_the_whole_follow_up(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    nearby_duplicate = (_NORTH[0] + 0.01, _NORTH[1])  # about 1 km from the first anchor: the same locality
    _, _, _, _, sent = _follow_up(monkeypatch, tmp_path, [_NORTH, nearby_duplicate])
    north = "proximity:10.0,50.085"
    assert [(r["limit"], r["bias"]) for r in sent] == [(6, north), (3, north), (7, north)]


@pytest.mark.parametrize(
    "points",
    [[], [(50.01, 10.01)], [(50.01, 10.01), (49.99, 9.99)]],
    ids=["nothing grounded", "one must-visit near the centre", "must-visits clustered near the centre"],
)
def test_without_a_distinct_anchor_the_held_share_returns_to_the_destination(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path, points: list[tuple[float, float]]  # noqa: F811
) -> None:
    _, context, (attractions, restaurants, _stay), (new_attractions, new_restaurants), sent = _follow_up(
        monkeypatch, tmp_path, points
    )
    centre = "proximity:10.0,50.0"
    # the destination-local list is continued AFTER the places already requested there
    assert [(r["limit"], r["offset"], r["bias"]) for r in sent] == [(6, 6, centre), (3, 3, centre), (7, 8, centre)]
    museums = [p for p in (*attractions.data, *new_attractions) if p.place_id.startswith("geoapify/museum")]
    assert len(museums) == 12 and len({p.place_id for p in museums}) == 12  # the full local culture share, no repeat
    assert len(restaurants.data) + len(new_restaurants) == 30
    assert context.held_place_requests == []


def test_nothing_is_requested_when_nothing_was_held(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    adapter, network, _, _ = _run(monkeypatch, tmp_path)  # a trip without must-visits
    before = len(network.requests["places"])
    assert adapter.search_must_visit_local_inventory(_CITY, [GeoPoint(lat=_NORTH[0], lng=_NORTH[1])]) == ([], [])
    assert len(network.requests["places"]) == before
    # an adapter that is not bound to a generation never holds anything back
    unbound = _adapter(monkeypatch, _Network(places=_Provider(_region_wide_catalogue())), ProviderCacheStore(tmp_path / "u.sqlite3"))
    assert unbound.search_must_visit_local_inventory(_CITY, []) == ([], [])


def test_a_follow_up_record_never_replaces_an_identity_the_pool_holds(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    # The follow-up returns records the first batch already supplied (the destination list
    # continued from offset 0 would): none is added a second time.
    adapter, network, context, (attractions, _restaurants, _stay) = _run(monkeypatch, tmp_path, hold=True)
    context.held_place_requests = [("attractions", "culture", 6, 0)]  # deliberately overlapping the first batch
    new_attractions, _ = adapter.search_must_visit_local_inventory(_CITY, [])
    assert new_attractions == []
    assert len({p.place_id for p in context.place_pool}) == len(context.place_pool) == len(attractions.data)

    # Service level: the grounded must-visit keeps the identity and the terms grounding gave it.
    must_visit = attractions.data[0].model_dump(mode="json")
    record_grounded_term(must_visit, "the place I asked for")
    pool = [must_visit, attractions.data[1].model_dump(mode="json")]
    same_identity = attractions.data[0].model_copy(update={"name": "A later record of the same provider identity"})
    brand_new = attractions.data[2]
    seen: list[list[GeoPoint]] = []

    def search_local(destination: str, anchors: list[GeoPoint]) -> tuple[list[Any], list[Any]]:
        seen.append(anchors)
        return [same_identity, brand_new], []

    merged, restaurants = DestinationContextService._append_must_visit_local_inventory(search_local, _CITY, pool, [])
    assert [poi["place_id"] for poi in merged] == [must_visit["place_id"], pool[1]["place_id"], brand_new.place_id]
    assert merged[0]["name"] == must_visit["name"] and grounded_terms(merged[0]) == ["the place I asked for"]
    assert restaurants == []
    # only a GROUNDED must-visit's coordinates are an anchor
    assert [(a.lat, a.lng) for a in seen[0]] == [(must_visit["coordinates"]["lat"], must_visit["coordinates"]["lng"])]

    def failing(destination: str, anchors: list[GeoPoint]) -> tuple[list[Any], list[Any]]:
        raise RuntimeError("provider unavailable")

    assert DestinationContextService._append_must_visit_local_inventory(failing, _CITY, list(pool), []) == (pool, [])


# =====================================================================================
# 4. Identity, duplicates and waterfront
# =====================================================================================


def test_duplicate_records_of_one_place_become_one_candidate(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    def osm(way: int) -> dict[str, Any]:
        return {"sourcename": "openstreetmap", "raw": {"osm_id": way, "osm_type": "w", "amenity": "marketplace"}}

    market = ["commercial", "commercial.marketplace"]
    aquarium = ["entertainment", "entertainment.aquarium"]
    duplicates = {
        # one market under three provider records (two share the source identity, one shares name and place)
        _PARKS: [
            _feature("market_a", "Hall Market", market, 50.001, 10.001, datasource=osm(11)),
            _feature("market_b", "Hall Market", market, 50.0011, 10.0011, datasource=osm(11)),
            _feature("market_c", "Hall Market", market, 50.0012, 10.0012),
        ],
        # one aquarium under two records
        _CULTURE: [
            _feature("aquarium_a", "Bay Aquarium", aquarium, 50.002, 10.002),
            _feature("aquarium_b", "Bay Aquarium", aquarium, 50.0021, 10.0021),
        ],
    }
    _, _, context, (attractions, _restaurants, _stay) = _run(monkeypatch, tmp_path, catalogue=duplicates)
    assert sorted(place.name for place in attractions.data) == ["Bay Aquarium", "Hall Market"]
    # merged by the EXISTING identity rules (the same record seen by a broad and a local request is not a merge)
    assert context.entity_merges == {"source_identity": 1, "name_proximity": 2}


def test_waterfront_discovery_and_classification(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    waterfront = {
        **_region_wide_catalogue(),
        "man_made.pier": [
            _feature("pier0", "Landing Stage", ["man_made", "man_made.pier"], 50.004, 10.004),
            _feature("beach0", "Town Beach", ["beach"], 50.005, 10.005),
            _feature("resort0", "Managed Beach", ["beach", "beach.beach_resort"], 50.006, 10.006),
            _feature("farbeach0", "Far Beach", ["beach"], 49.905, 9.905),
        ],
    }
    _, network, _, (attractions, _restaurants, _stay) = _run(
        monkeypatch, tmp_path, interests=["waterfront"], catalogue=waterfront
    )
    sent = _sent(network)
    request = _of(sent, "man_made.pier")
    # one request, six places taken from the largest general group, near the destination; no marina is asked for
    assert [(r["categories"], r["limit"], r["bias"]) for r in request] == [
        ("man_made.pier,beach,beach.beach_resort", 6, "proximity:10.0,50.0")
    ]
    assert [r["limit"] for r in _of(sent, _SIGHTS, biased=False)] == [10] and [r["limit"] for r in _of(sent, _SIGHTS, biased=True)] == [5]
    attraction_requests = [r for r in sent if _FOOD not in r["categories"] and "accommodation" not in r["categories"]]
    assert sum(r["limit"] for r in attraction_requests) == 60

    found = {place.name: place for place in attractions.data}
    for name in ("Landing Stage", "Town Beach", "Managed Beach", "Far Beach"):
        classified = taxonomy.classify_place(found[name].provider_tags, found[name].category)
        assert "waterfront" in taxonomy.matched_interests(classified, ["waterfront"]), name
    # classified from the provider category alone (these records carry no raw tags)
    assert found["Town Beach"].provider_tags["natural"] == "beach"

    # a marina record met some other way is classified accurately -- by mapping, or by its raw tag
    by_mapping = taxonomy.classify_place(categories.taxonomy_tags_from_categories(["maritime", "maritime.marina"]))
    by_raw_tag = taxonomy.classify_place({"leisure": "marina"})
    assert taxonomy.WATERFRONT in by_mapping.categories and taxonomy.WATERFRONT in by_raw_tag.categories
    # ... and an ordinary park, a coastal feature without a mapped category, or a name, is not waterfront
    for tags in ({"leisure": "park"}, categories.taxonomy_tags_from_categories(["natural", "natural.coastal"]), {"tourism": "attraction"}):
        assert taxonomy.WATERFRONT not in taxonomy.classify_place(tags).categories


def test_waterfront_classification_is_not_usefulness() -> None:
    # A marina is CLASSIFIED as waterfront; whether it deserves a slot is still Q2's question.
    from app.services.candidate_usefulness import usefulness_by_place_id
    from app.tests.services.test_candidate_usefulness_q2 import _state
    from app.tests.services.test_final_quality_correction_203c2b import _poi

    marina = _poi("marina", "Mooring Basin", GeoPoint(lat=50.0, lng=10.0), category="marina", provider_tags={"leisure": "marina"})
    museum = _poi("museum", "Documented Museum", GeoPoint(lat=50.001, lng=10.001), category="museum",
                  provider_tags={"tourism": "museum", "wikipedia": "en:Documented Museum"})
    # nobody asked for waterfront: the marina carries no usefulness evidence at all
    unrequested = usefulness_by_place_id(_state([marina, museum], interests=["museum"]))
    assert unrequested[marina["place_id"]].evidence_band == 0
    assert unrequested[museum["place_id"]].evidence_band > unrequested[marina["place_id"]].evidence_band
    # requested: it gains exactly the interest-fit evidence the ordering always gave, nothing more
    requested = usefulness_by_place_id(_state([marina, museum], interests=["harbour"]))
    assert requested[marina["place_id"]].interest_fit is True and requested[marina["place_id"]].evidence_band == 1
    assert requested[marina["place_id"]].provider_significance is False and requested[marina["place_id"]].must_visit is False


# =====================================================================================
# 5. Budgets, cache identity, fallbacks, determinism
# =====================================================================================


def test_the_worst_case_reservation_is_exact_and_checked_before_any_request(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    # Worst case at the default pool: waterfront requested, must-visits, two distinct anchors.
    adapter, network, context, _ = _run(monkeypatch, tmp_path, hold=True, interests=["waterfront"])
    first_batch = _sent(network)
    attraction_requests = [r for r in first_batch if _FOOD not in r["categories"] and "accommodation" not in r["categories"]]
    assert len(attraction_requests) == 9  # 4 broad + 4 local + the waterfront group
    assert sum(places_request_credits(r["limit"]) for r in attraction_requests) == 9  # each billed by itself
    assert len(first_batch) == 12 and sum(places_request_credits(r["limit"]) for r in first_batch) == 12
    assert GeoapifyPlacesAdapter._worst_case_follow_up_credits(_held(context)) == 6  # 3 held groups x 2 anchors

    before = len(network.requests["places"])
    adapter.search_must_visit_local_inventory(_CITY, [GeoPoint(lat=_NORTH[0], lng=_NORTH[1]), GeoPoint(lat=_EAST[0], lng=_EAST[1])])
    follow_up = _sent(network)[before:]
    assert len(follow_up) == 6 and sum(places_request_credits(r["limit"]) for r in follow_up) == 6
    # 18 Places requests and at most 18 credits in the worst case; what came back was billed, never more
    assert len(network.requests["places"]) == 18
    assert context.usage_tracker.credits_used("places") <= 18 and context.usage_tracker.snapshot()["refused_calls"] == 0


def test_a_budget_that_cannot_cover_the_split_gets_the_unsplit_plan_and_holds_nothing(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    # 1 credit is spent resolving the destination; the split batch plus its follow-up would
    # reserve 17, the unsplit one 3 + 1 + 1 + 1 for attractions, 3 for food, 1 for accommodation
    # (a request above 20 places reserves 1 + one per 20).
    _, network, context, _ = _run(monkeypatch, tmp_path, hold=True, budget=12)
    sent = _sent(network)
    assert all(r["bias"] is None for r in sent)  # no local request at all
    assert [r["limit"] for r in sent] == [21, 9, 18, 12, 30, 20]  # every discovery request was sent
    assert sum(places_request_credits(r["limit"]) for r in sent) == 10
    # (the optional Place Details lookups that follow are refused locally once the budget is
    # spent, as before; no discovery request is, and the cap is never exceeded)
    assert context.held_place_requests == []
    assert context.usage_tracker.credits_used() <= 12


def test_cache_identity_covers_group_filter_bias_limit_and_offset(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    import sqlite3

    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    network = _Network(places=_Provider(_region_wide_catalogue()))

    def generation(points: list[tuple[float, float]]) -> Any:
        context = _context()
        adapter = _adapter(monkeypatch, network, store).bound_to(context)
        adapter.search_broad_inventory(
            _CITY, {"pool_size": 60, "hold_back_for_must_visits": True}, {"pool_size": 30, "hold_back_for_must_visits": True}
        )
        adapter.search_must_visit_local_inventory(_CITY, [GeoPoint(lat=lat, lng=lon) for lat, lon in points])
        return context

    def entries() -> int:
        return sqlite3.connect(tmp_path / "cache.sqlite3").execute(
            "select count(*) from provider_cache where source = 'geoapify_places'"
        ).fetchone()[0]

    generation([_NORTH])
    assert len(network.requests["places"]) == 11 + 3 and entries() == 14  # one entry per distinct request
    # the identical generation again: every request is a cache hit and costs nothing
    again = generation([_NORTH])
    assert len(network.requests["places"]) == 14 and again.usage_tracker.credits_used("places") == 0
    # a different anchor is a different request: only the follow-up is fetched again
    generation([_EAST])
    assert len(network.requests["places"]) == 14 + 3 and entries() == 17
    # no anchor: the destination list continued from an offset -- again different requests
    generation([])
    assert len(network.requests["places"]) == 17 + 3 and entries() == 20


def test_the_geographic_fallback_filters_apply_to_local_requests_too(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    provider = _Provider(_region_wide_catalogue())

    def refusing_boundary(request: httpx.Request) -> Any:
        if request.url.params["filter"].startswith("place:"):
            return httpx.Response(400, json={"message": "filter is not supported"})
        return provider(request)

    network = _Network(places=refusing_boundary)
    adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / "cache.sqlite3")).bound_to(_context())
    attractions = adapter.search_attractions(_CITY, {"pool_size": 60})
    sent = _sent(network)
    accepted = [r for r in sent if r["filter"].startswith("rect:")]
    # every request was retried once with the bounding box, and a local request kept its bias
    assert len(accepted) == 8 and len(sent) == 16
    assert sum(r["bias"] is not None for r in accepted) == 4 and {r["bias"] for r in accepted if r["bias"]} == {"proximity:10.0,50.0"}
    assert len(attractions.data) == 60


def test_serial_and_concurrent_runs_merge_identically(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path  # noqa: F811
) -> None:
    from app.core.config import get_settings

    def generation(enabled: str, name: str) -> tuple[list[str], list[str], list[str], dict[str, int], dict[str, Any]]:
        monkeypatch.setenv("PROVIDER_IO_CONCURRENCY_ENABLED", enabled)
        get_settings.cache_clear()
        network = _Network(places=_Provider(_region_wide_catalogue()))
        context = _context()
        adapter = _adapter(monkeypatch, network, ProviderCacheStore(tmp_path / f"{name}.sqlite3")).bound_to(context)
        attractions, restaurants, _stay = adapter.search_broad_inventory(
            _CITY, {"pool_size": 60, "hold_back_for_must_visits": True}, {"pool_size": 30, "hold_back_for_must_visits": True}
        )
        more_attractions, more_restaurants = adapter.search_must_visit_local_inventory(
            _CITY, [GeoPoint(lat=_NORTH[0], lng=_NORTH[1]), GeoPoint(lat=_EAST[0], lng=_EAST[1])]
        )
        return (
            [p.place_id for p in (*attractions.data, *more_attractions)],
            [p.place_id for p in (*restaurants.data, *more_restaurants)],
            [p.place_id for p in context.place_pool],
            dict(context.entity_merges),
            context.usage_tracker.snapshot(),
        )

    try:
        assert generation("false", "serial") == generation("true", "concurrent")
    finally:
        get_settings.cache_clear()


def test_discovery_adds_no_model_call_and_reports_each_request(
    monkeypatch: pytest.MonkeyPatch, configured: None, serial: None, tmp_path: Path  # noqa: F811
) -> None:
    from app.core import generation_diagnostics

    # the adapter and the stage that drives the follow-up reach no model provider
    for module in (places_module, __import__("app.services.destination_context_service", fromlist=["x"])):
        source = Path(module.__file__).read_text()
        assert "ai_candidate_proposal" not in source and "ai_itinerary" not in source and "groq" not in source.lower()

    recorder = generation_diagnostics.GenerationDiagnostics()
    with generation_diagnostics.activate(recorder):
        adapter, _, _, _ = _run(monkeypatch, tmp_path, hold=True, interests=["waterfront"])
        adapter.search_must_visit_local_inventory(_CITY, [GeoPoint(lat=_NORTH[0], lng=_NORTH[1])])
    requests = recorder.snapshot()["discovery_requests"]
    sources = [item["source"] for item in requests if item["kind"] == "attractions"]
    assert sources == [SOURCE_BROAD] * 4 + [SOURCE_DESTINATION_LOCAL] * 4 + [SOURCE_INTEREST]
    follow_up = [item for item in requests if item["kind"].endswith("_follow_up")]
    assert {item["source"] for item in follow_up} == {SOURCE_MUST_VISIT_LOCAL} and len(follow_up) == 3
    assert sum(item["requested"] for item in requests if item["kind"].startswith("attractions")) == 60
    assert all(item["returned"] == len(item["place_ids"]) <= item["requested"] for item in requests)
    assert all(not item["failed"] for item in requests)
