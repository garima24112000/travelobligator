from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.models.common import DataStatus, ProviderStatus
from app.providers.places import openstreetmap_adapter
from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter
from app.storage.provider_cache_store import ProviderCacheStore, make_query_hash


@pytest.fixture(autouse=True)
def _isolated_default_provider_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test in this file gets a fresh, isolated `ProviderCacheStore`
    whenever an adapter is constructed *without* an explicit `cache_store`
    (Step 164E lazily resolves one via `get_provider_cache_store`). Without
    this, the adapter's default lazy resolution would share one real,
    process-wide cache file (`get_provider_cache_store` is a singleton
    keyed by resolved path) across every test function in this file --
    including the many pre-existing tests below that construct
    `OpenStreetMapPlacesAdapter()` with no arguments and geocode the same
    destination strings ("Los Angeles", "New York") repeatedly with
    different expected HTTP call counts/results. Without isolation, an
    earlier test's persistently cached geocode result would silently
    satisfy a later test's `.get()` call-count assertions -- or worse,
    shift a `_get_responses`-mode fake client's response list out of sync
    -- instead of exercising the fake HTTP client as each test expects, and
    would write to the real repo-local `.data/provider_cache.sqlite3` file.
    """
    fresh_store = ProviderCacheStore(tmp_path / "isolated_default_cache.sqlite3")
    monkeypatch.setattr(
        openstreetmap_adapter, "get_provider_cache_store", lambda path: fresh_store
    )
    return fresh_store


def _geocode_query_hash(query: str) -> str:
    return make_query_hash({"query": query.strip().lower(), "format": "jsonv2", "limit": 1})


def _poi_query_hash(
    lat: float = 34.0522,
    lon: float = -118.2437,
    radius_meters: int = 6000,
    tags: list[str] | None = None,
) -> str:
    tags = tags if tags is not None else openstreetmap_adapter._ATTRACTION_TAG_FILTERS
    return make_query_hash(
        {
            "lat": lat,
            "lon": lon,
            "radius_meters": radius_meters,
            "tags": sorted(tags),
            "limit": openstreetmap_adapter._MAX_RESULTS,
        }
    )


def _settings(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Settings:
    """A `Settings` instance for adapter construction, isolated from any
    real `.env` file so cache-enabled/TTL behavior in these tests is
    deterministic regardless of local dev environment contents."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    return Settings(**overrides)


class _FakeResponse:
    """Stands in for an `httpx.Response`. `should_fail=True` makes
    `raise_for_status` raise, simulating a request-level failure."""

    def __init__(self, json_data: Any = None, should_fail: bool = False) -> None:
        self._json_data = json_data
        self._should_fail = should_fail

    def raise_for_status(self) -> None:
        if self._should_fail:
            raise httpx.HTTPError("simulated request failure")

    def json(self) -> Any:
        return self._json_data


class _FakeClient:
    """Stands in for `httpx.Client`. `post_responses` is consumed in order,
    one per Overpass `.post()` call (primary, then fallback if attempted)."""

    def __init__(
        self,
        geocode_response: _FakeResponse | None = None,
        post_responses: list[_FakeResponse] | None = None,
        get_responses: list[_FakeResponse] | None = None,
    ) -> None:
        self._geocode_response = geocode_response
        self._post_responses = list(post_responses or [])
        self._get_responses = list(get_responses) if get_responses is not None else None
        self.post_call_count = 0
        self.get_call_count = 0
        # Every `q` param sent to Nominatim, in call order -- lets tests
        # prove no query is ever retried with a truncated/looser string.
        self.get_queries: list[str] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        self.get_queries.append((params or {}).get("q"))
        if self._get_responses is not None:
            response = self._get_responses[self.get_call_count]
            self.get_call_count += 1
            return response
        assert self._geocode_response is not None
        return self._geocode_response

    def post(self, url: str, data: dict[str, Any] | None = None) -> _FakeResponse:
        response = self._post_responses[self.post_call_count]
        self.post_call_count += 1
        return response


def _geocode_ok(query: str = "Los Angeles") -> _FakeResponse:
    # `display_name` deliberately echoes `query` so `_is_plausible_geocode_match`
    # accepts it regardless of which destination string a given test uses.
    # `boundingbox` is a generous box (comfortably covering every fixed
    # coordinate used by `_element`/`_nominatim_result` below) so existing
    # containment-agnostic tests keep passing once containment is enforced.
    return _FakeResponse(
        json_data=[
            {
                "lat": "34.0522",
                "lon": "-118.2437",
                "display_name": f"{query}, California, United States",
                "boundingbox": ["33.5", "34.5", "-118.8", "-117.5"],
            }
        ]
    )


def _element(
    element_id: int,
    name: str | None,
    lat: float = 34.0,
    lon: float = -118.0,
    **extra_tags: str,
) -> dict[str, Any]:
    tags = dict(extra_tags)
    if name is not None:
        tags["name"] = name
    return {"type": "node", "id": element_id, "lat": lat, "lon": lon, "tags": tags}


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, geocode_response: _FakeResponse, post_responses: list[_FakeResponse]
) -> _FakeClient:
    fake_client = _FakeClient(geocode_response=geocode_response, post_responses=post_responses)
    monkeypatch.setattr(
        openstreetmap_adapter.httpx, "Client", lambda **kwargs: fake_client
    )
    return fake_client


def _install_fake_client_for_get(
    monkeypatch: pytest.MonkeyPatch, get_responses: list[_FakeResponse]
) -> _FakeClient:
    """Install a fake client for adapter methods that only ever call `.get()`
    (e.g. `search_must_visit_place`, which uses Nominatim search directly and
    never touches Overpass `.post()`)."""
    fake_client = _FakeClient(get_responses=get_responses)
    monkeypatch.setattr(
        openstreetmap_adapter.httpx, "Client", lambda **kwargs: fake_client
    )
    return fake_client


def _nominatim_result(
    name: str,
    lat: str = "34.0522",
    lon: str = "-118.2437",
    osm_type: str = "way",
    osm_id: int = 123,
    place_type: str = "attraction",
) -> dict[str, Any]:
    return {
        "place_id": 999,
        "osm_type": osm_type,
        "osm_id": osm_id,
        "lat": lat,
        "lon": lon,
        "display_name": f"{name}, Some Street, Some City",
        "namedetails": {"name": name},
        "type": place_type,
        "class": "tourism",
    }


def test_primary_attraction_query_success_uses_primary_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_elements = [
        _element(1, "Griffith Observatory", tourism="attraction"),
        _element(2, "The Getty", tourism="museum"),
        _element(3, "Walt Disney Concert Hall", tourism="attraction"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[_FakeResponse(json_data={"elements": primary_elements})],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Los Angeles")

    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.fallback_used is False
    assert response.fallback_provider is None
    names = {place.name for place in response.data}
    assert names == {"Griffith Observatory", "The Getty", "Walt Disney Concert Hall"}
    # Only the primary Overpass query was made; fallback was never attempted.
    assert fake_client.post_call_count == 1


def test_primary_attraction_failure_uses_fallback_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Attraction fallback has 4 tag filters, each now queried one at a time;
    # named results from each successful tag query are aggregated together.
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(should_fail=True),  # primary query fails
            _FakeResponse(json_data={"elements": [_element(11, "La Brea Tar Pits", tourism="museum")]}),
            _FakeResponse(json_data={"elements": []}),  # historic tag: nothing usable
            _FakeResponse(json_data={"elements": [_element(12, "Hollywood Bowl", amenity="arts_centre")]}),
            _FakeResponse(json_data={"elements": [_element(10, "Echo Park", leisure="park")]}),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Los Angeles")

    assert response.status == ProviderStatus.FALLBACK_USED
    assert response.data_status == DataStatus.FALLBACK_USED
    assert response.fallback_used is True
    assert response.fallback_provider == "openstreetmap_places"
    assert "fallback" in (response.message or "").lower()
    names = {place.name for place in response.data}
    assert names == {"Echo Park", "La Brea Tar Pits", "Hollywood Bowl"}
    # The primary (failed) query plus all 4 individual fallback tag queries.
    assert fake_client.post_call_count == 5


def test_fallback_still_filters_unnamed_places(monkeypatch: pytest.MonkeyPatch) -> None:
    primary_elements = [_element(1, None, tourism="attraction")]  # unnamed only
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(json_data={"elements": primary_elements}),
            _FakeResponse(json_data={"elements": []}),  # tourism~attraction|museum|viewpoint tag
            _FakeResponse(json_data={"elements": []}),  # historic tag
            _FakeResponse(json_data={"elements": [_element(22, None, amenity="arts_centre")]}),  # unnamed, dropped
            _FakeResponse(
                json_data={
                    "elements": [
                        _element(20, None, leisure="park"),  # unnamed, must be dropped
                        _element(21, "Runyon Canyon Park", leisure="park"),
                    ]
                }
            ),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Los Angeles")

    assert response.fallback_used is True
    assert [place.name for place in response.data] == ["Runyon Canyon Park"]
    assert fake_client.post_call_count == 5


def test_restaurant_fallback_works(monkeypatch: pytest.MonkeyPatch) -> None:
    fallback_elements = [
        _element(30, "Tacos El Gordo", amenity="fast_food"),
        _element(31, "Republique", amenity="restaurant"),
        _element(32, "Verve Coffee", amenity="cafe"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(json_data={"elements": []}),  # primary returns nothing usable
            _FakeResponse(json_data={"elements": fallback_elements}),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_restaurants("Los Angeles")

    assert response.status == ProviderStatus.FALLBACK_USED
    assert response.fallback_used is True
    names = {place.name for place in response.data}
    assert names == {"Tacos El Gordo", "Republique", "Verve Coffee"}
    assert fake_client.post_call_count == 2


def test_both_primary_and_fallback_failing_stays_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Primary fails, and every one of the 4 individual attraction fallback
    # tag queries also fails -- the field must stay honestly failed rather
    # than silently downgrading to a partial/empty success.
    _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok("Nowhere Land"),
        post_responses=[
            _FakeResponse(should_fail=True),  # primary
            _FakeResponse(should_fail=True),  # fallback tag 1
            _FakeResponse(should_fail=True),  # fallback tag 2
            _FakeResponse(should_fail=True),  # fallback tag 3
            _FakeResponse(should_fail=True),  # fallback tag 4
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Nowhere Land")

    assert response.status == ProviderStatus.FAILED
    assert response.data_status == DataStatus.FAILED
    assert response.data is None
    assert response.fallback_used is False
    assert "attractions" in response.unavailable_fields


def test_one_failing_fallback_tag_does_not_fail_field_when_another_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 2 of the 4 attraction fallback tag queries fail outright (request
    # error), but the other 2 succeed with named results. The field must
    # still succeed via fallback -- one bad tag can't sink the others.
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(should_fail=True),  # primary query fails
            _FakeResponse(should_fail=True),  # fallback tag 1 fails
            _FakeResponse(json_data={"elements": [_element(11, "La Brea Tar Pits", tourism="museum")]}),
            _FakeResponse(should_fail=True),  # fallback tag 3 fails
            _FakeResponse(json_data={"elements": [_element(12, "Hollywood Bowl", amenity="arts_centre")]}),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Los Angeles")

    assert response.status == ProviderStatus.FALLBACK_USED
    assert response.fallback_used is True
    names = {place.name for place in response.data}
    assert names == {"La Brea Tar Pits", "Hollywood Bowl"}
    assert fake_client.post_call_count == 5


def test_fallback_aggregation_deduplicates_by_place_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same OSM element (same type/id) can legitimately match more than
    # one fallback tag filter (e.g. a place tagged both "historic" and
    # "tourism=attraction"). It must only appear once in the aggregated
    # result.
    _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(should_fail=True),  # primary query fails
            _FakeResponse(json_data={"elements": [_element(11, "La Brea Tar Pits", tourism="museum")]}),
            _FakeResponse(json_data={"elements": [_element(11, "La Brea Tar Pits", tourism="museum")]}),
            _FakeResponse(json_data={"elements": []}),
            _FakeResponse(json_data={"elements": [_element(12, "Hollywood Bowl", amenity="arts_centre")]}),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Los Angeles")

    assert response.fallback_used is True
    names = [place.name for place in response.data]
    assert names == ["La Brea Tar Pits", "Hollywood Bowl"]
    assert len({place.place_id for place in response.data}) == len(response.data)


def test_fallback_stops_once_max_results_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Lower the cap so it's reachable within a single fallback tag query's
    # results. Once reached, remaining fallback tag filters must not be
    # queried at all -- only enough fake responses for the primary query and
    # the one fallback tag query that fills the cap are provided, so an
    # extra call would raise an IndexError.
    monkeypatch.setattr(openstreetmap_adapter, "_MAX_RESULTS", 2)
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(should_fail=True),  # primary query fails
            _FakeResponse(
                json_data={
                    "elements": [
                        _element(11, "La Brea Tar Pits", tourism="museum"),
                        _element(12, "The Getty", tourism="museum"),
                        _element(13, "Griffith Observatory", tourism="attraction"),
                    ]
                }
            ),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Los Angeles")

    assert response.fallback_used is True
    assert len(response.data) == 2
    # Only the primary query and the first fallback tag query were made;
    # the remaining 3 fallback tag filters were never queried.
    assert fake_client.post_call_count == 2


def test_all_fallback_tag_queries_returning_nothing_usable_stays_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Primary returns an unnamed-only element (no usable results, no
    # request failure), and every fallback tag query also comes back with
    # nothing usable (empty, not a request failure). The field must stay
    # honestly unavailable rather than fabricating a success.
    primary_elements = [_element(1, None, tourism="attraction")]  # unnamed only
    _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok("Nowhere Land"),
        post_responses=[
            _FakeResponse(json_data={"elements": primary_elements}),
            _FakeResponse(json_data={"elements": []}),
            _FakeResponse(json_data={"elements": []}),
            _FakeResponse(json_data={"elements": []}),
            _FakeResponse(json_data={"elements": [_element(2, None, leisure="park")]}),  # unnamed, dropped
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Nowhere Land")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None
    assert response.fallback_used is False
    assert "attractions" in response.unavailable_fields


def test_fallback_result_creates_no_fake_rating_price_review_or_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The raw OSM element carries opening_hours/rating-shaped tags, but the
    # adapter must not read or propagate them into NormalizedPlace.
    _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(should_fail=True),  # primary query fails
            _FakeResponse(json_data={"elements": []}),  # tourism~attraction|museum|viewpoint tag
            _FakeResponse(json_data={"elements": []}),  # historic tag
            _FakeResponse(json_data={"elements": [_element(42, "Barnsdall Art Park", amenity="arts_centre")]}),
            _FakeResponse(
                json_data={
                    "elements": [
                        _element(
                            40,
                            "Griffith Park",
                            leisure="park",
                            opening_hours="Mo-Su 05:00-22:30",
                            stars="4.5",
                        ),
                        _element(41, "Bronson Canyon", leisure="park"),
                    ]
                }
            ),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Los Angeles")

    assert response.fallback_used is True
    for place in response.data:
        dumped = place.model_dump()
        assert set(dumped.keys()) == {
            "place_id",
            "name",
            "category",
            "coordinates",
            "address",
            "source",
            "data_status",
            "confidence",
        }
        for forbidden_field in ("rating", "price", "review", "opening_hours", "booking_url", "availability"):
            assert forbidden_field not in dumped


def test_primary_accommodation_query_success_uses_primary_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_elements = [
        _element(50, "The Ritz-Carlton", tourism="hotel"),
        _element(51, "Downtown Hostel", tourism="hostel"),
        _element(52, "Sunset Guest House", tourism="guest_house"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[_FakeResponse(json_data={"elements": primary_elements})],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_accommodation_pois("Los Angeles")

    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.fallback_used is False
    assert response.fallback_provider is None
    names = {place.name for place in response.data}
    assert names == {"The Ritz-Carlton", "Downtown Hostel", "Sunset Guest House"}
    # Only the primary Overpass query was made; fallback was never attempted.
    assert fake_client.post_call_count == 1


def test_primary_accommodation_failure_uses_fallback_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_elements = [
        _element(60, "Palm Springs Resort", tourism="resort"),
        _element(61, "Mountain Chalet", tourism="chalet"),
        _element(62, "City Apartments", tourism="apartment"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(should_fail=True),  # primary query fails
            _FakeResponse(json_data={"elements": fallback_elements}),  # fallback succeeds
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_accommodation_pois("Los Angeles")

    assert response.status == ProviderStatus.FALLBACK_USED
    assert response.data_status == DataStatus.FALLBACK_USED
    assert response.fallback_used is True
    assert response.fallback_provider == "openstreetmap_places"
    assert "fallback" in (response.message or "").lower()
    names = {place.name for place in response.data}
    assert names == {"Palm Springs Resort", "Mountain Chalet", "City Apartments"}
    # Both the primary (failed) and fallback queries were attempted.
    assert fake_client.post_call_count == 2


def test_accommodation_fallback_still_filters_unnamed_places(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_elements = [
        _element(70, None, tourism="hotel"),  # unnamed, must be dropped
        _element(71, "Hillside Motel", tourism="motel"),
        _element(72, None, tourism="resort"),  # unnamed, must be dropped
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(json_data={"elements": []}),  # primary returns nothing usable
            _FakeResponse(json_data={"elements": fallback_elements}),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_accommodation_pois("Los Angeles")

    assert response.fallback_used is True
    assert [place.name for place in response.data] == ["Hillside Motel"]
    assert fake_client.post_call_count == 2


def test_accommodation_fallback_result_creates_no_fake_price_rating_review_or_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The raw OSM element carries opening_hours/rating/price-shaped tags, but
    # the adapter must not read or propagate them into NormalizedPlace --
    # accommodation POIs are open-data location candidates only, never
    # bookable inventory.
    fallback_elements = [
        _element(
            80,
            "Seaside Resort",
            tourism="resort",
            opening_hours="24/7",
            stars="4.5",
            fee="yes",
        ),
        _element(81, "Backpacker Hostel", tourism="hostel"),
        _element(82, "Lakeview Chalet", tourism="chalet"),
    ]
    _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(should_fail=True),
            _FakeResponse(json_data={"elements": fallback_elements}),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_accommodation_pois("Los Angeles")

    assert response.fallback_used is True
    for place in response.data:
        dumped = place.model_dump()
        assert set(dumped.keys()) == {
            "place_id",
            "name",
            "category",
            "coordinates",
            "address",
            "source",
            "data_status",
            "confidence",
        }
        for forbidden_field in (
            "rating",
            "price",
            "review",
            "opening_hours",
            "booking_url",
            "reservation_url",
            "availability",
        ):
            assert forbidden_field not in dumped


def test_both_primary_and_fallback_accommodation_failing_stays_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok("Nowhere Land"),
        post_responses=[
            _FakeResponse(should_fail=True),
            _FakeResponse(should_fail=True),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_accommodation_pois("Nowhere Land")

    assert response.status == ProviderStatus.FAILED
    assert response.data_status == DataStatus.FAILED
    assert response.data is None
    assert response.fallback_used is False
    assert "accommodation_pois" in response.unavailable_fields


def test_search_must_visit_place_returns_named_result_with_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client_for_get(
        monkeypatch,
        get_responses=[
            _geocode_ok("Los Angeles"),  # destination resolution
            _FakeResponse(json_data=[_nominatim_result("Griffith Observatory")]),  # must-visit lookup
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_must_visit_place("Griffith Observatory", "Los Angeles")

    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.data is not None
    assert len(response.data) == 1

    place = response.data[0]
    assert place.name == "Griffith Observatory"
    assert place.place_id == "way/123"
    assert place.coordinates is not None
    assert place.coordinates.lat == pytest.approx(34.0522)
    assert place.coordinates.lng == pytest.approx(-118.2437)
    assert place.source == "openstreetmap_places"

    # The query combines the must-visit term with the destination, never an
    # unconstrained global search. Two GET calls: resolving the destination,
    # then the targeted must-visit lookup.
    assert fake_client.get_call_count == 2


def test_search_must_visit_place_no_results_does_not_invent_a_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_client_for_get(
        monkeypatch,
        get_responses=[_geocode_ok("Los Angeles"), _FakeResponse(json_data=[])],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_must_visit_place("Nonexistent Landmark", "Los Angeles")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None
    assert "must_visit_place" in response.unavailable_fields


def test_search_must_visit_place_missing_coordinates_does_not_invent_a_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _nominatim_result("Some Place")
    result["lat"] = None
    result["lon"] = None
    _install_fake_client_for_get(
        monkeypatch,
        get_responses=[_geocode_ok("Los Angeles"), _FakeResponse(json_data=[result])],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_must_visit_place("Some Place", "Los Angeles")

    assert response.data is None
    assert response.status == ProviderStatus.UNAVAILABLE


def test_search_must_visit_place_request_failure_stays_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_client_for_get(
        monkeypatch,
        get_responses=[_geocode_ok("Los Angeles"), _FakeResponse(should_fail=True)],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_must_visit_place("Griffith Observatory", "Los Angeles")

    assert response.status == ProviderStatus.FAILED
    assert response.data_status == DataStatus.FAILED
    assert response.data is None
    assert "must_visit_place" in response.unavailable_fields


def test_search_must_visit_place_result_has_no_fake_rating_price_review_or_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_client_for_get(
        monkeypatch,
        get_responses=[
            _geocode_ok("Los Angeles"),
            _FakeResponse(json_data=[_nominatim_result("Griffith Observatory")]),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_must_visit_place("Griffith Observatory", "Los Angeles")

    assert response.data is not None
    dumped = response.data[0].model_dump()
    assert set(dumped.keys()) == {
        "place_id",
        "name",
        "category",
        "coordinates",
        "address",
        "source",
        "data_status",
        "confidence",
    }
    for forbidden_field in ("rating", "price", "review", "opening_hours", "booking_url", "availability"):
        assert forbidden_field not in dumped


def test_resolve_coordinates_returns_geocoded_point(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client_for_get(monkeypatch, get_responses=[_geocode_ok()])

    adapter = OpenStreetMapPlacesAdapter()
    point = adapter.resolve_coordinates("Los Angeles")

    assert point is not None
    assert point.lat == pytest.approx(34.0522)
    assert point.lng == pytest.approx(-118.2437)


def test_resolve_coordinates_reuses_geocode_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _install_fake_client_for_get(monkeypatch, get_responses=[_geocode_ok()])

    adapter = OpenStreetMapPlacesAdapter()
    first = adapter.resolve_coordinates("Los Angeles")
    second = adapter.resolve_coordinates("Los Angeles")

    assert first == second
    # Only one real geocoding request was made; the second call was served
    # from the same cache `_search` already uses.
    assert fake_client.get_call_count == 1


def test_resolve_coordinates_returns_none_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client_for_get(monkeypatch, get_responses=[_FakeResponse(json_data=[])])

    adapter = OpenStreetMapPlacesAdapter()
    point = adapter.resolve_coordinates("Nowhere")

    assert point is None


def test_resolve_coordinates_returns_none_on_request_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_client_for_get(monkeypatch, get_responses=[_FakeResponse(should_fail=True)])

    adapter = OpenStreetMapPlacesAdapter()
    point = adapter.resolve_coordinates("Nowhere")

    assert point is None


# ---------------------------------------------------------------------------
# Step 155C: destination grounding and provider containment
# ---------------------------------------------------------------------------


def _nyc_geocode_response() -> _FakeResponse:
    return _FakeResponse(
        json_data=[
            {
                "lat": "40.7128",
                "lon": "-74.0060",
                "display_name": "New York, United States",
                "boundingbox": ["40.4961", "40.9153", "-74.2557", "-73.7002"],
            }
        ]
    )


def test_is_plausible_geocode_match_requires_shared_significant_word() -> None:
    assert (
        openstreetmap_adapter._is_plausible_geocode_match(
            "New York", "New York, United States"
        )
        is True
    )
    # The exact failure mode from the reported bug: a degenerate query
    # resolving to a completely unrelated place must be rejected.
    assert (
        openstreetmap_adapter._is_plausible_geocode_match(
            "New York", "Rheydt, Mönchengladbach, North Rhine-Westphalia, Germany"
        )
        is False
    )


def test_is_plausible_geocode_match_rejects_when_query_has_no_significant_tokens() -> None:
    # "NY" has no token >= 3 characters, so there is nothing conservative
    # left to verify -- this must not be trusted by default.
    assert openstreetmap_adapter._is_plausible_geocode_match("NY", "NY, United States") is False


def test_parse_bounding_box_parses_valid_values() -> None:
    box = openstreetmap_adapter._parse_bounding_box(["1.0", "2.0", "3.0", "4.0"])
    assert box == (1.0, 2.0, 3.0, 4.0)


def test_parse_bounding_box_returns_none_for_malformed_input() -> None:
    assert openstreetmap_adapter._parse_bounding_box(None) is None
    assert openstreetmap_adapter._parse_bounding_box(["only", "two"]) is None
    assert openstreetmap_adapter._parse_bounding_box(["a", "b", "c", "d"]) is None


def test_implausible_geocode_match_is_rejected_and_no_overpass_query_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root cause fix: a Nominatim top result whose display_name shares no
    significant word with the query (e.g. a query for "New York" resolving
    to a German town) must be rejected -- the field is reported
    unavailable and no Overpass query is ever anchored to the wrong point.
    """
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_FakeResponse(
            json_data=[
                {
                    "lat": "51.1805",
                    "lon": "6.4428",
                    "display_name": "Rheydt, Mönchengladbach, North Rhine-Westphalia, Germany",
                    "boundingbox": ["51.1", "51.2", "6.4", "6.5"],
                }
            ]
        ),
        post_responses=[],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("New York")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None
    assert "attractions" in response.unavailable_fields
    # No Overpass query was ever made against the wrongly-resolved point.
    assert fake_client.post_call_count == 0


def test_geocode_never_retries_with_a_truncated_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """If resolving the full destination string fails, the adapter must
    never retry with a shorter/looser fragment (e.g. "New" instead of "New
    York") -- there is no such fallback path at all.
    """
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_FakeResponse(json_data=[]),  # Nominatim finds nothing
        post_responses=[],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("New York")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert fake_client.post_call_count == 0
    # Exactly one geocoding request was made, and it used the full,
    # untruncated destination string -- never "New" or any other fragment.
    assert fake_client.get_queries == ["New York"]


def test_attraction_results_outside_bounding_box_are_filtered_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a real, named Overpass result is discarded if its coordinates
    fall outside the resolved destination's bounding box.
    """
    elements = [
        _element(1, "Empire State Building", lat=40.7484, lon=-73.9857, tourism="attraction"),
        _element(2, "Rathaus Rheydt", lat=51.1743, lon=6.4453, tourism="attraction"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_nyc_geocode_response(),
        post_responses=[_FakeResponse(json_data={"elements": elements})],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("New York")

    names = {place.name for place in response.data}
    assert names == {"Empire State Building"}
    assert "Rathaus Rheydt" not in names
    assert fake_client.post_call_count == 1


def test_all_results_outside_bounding_box_reports_unavailable_not_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every returned POI falls outside the resolved destination, the
    field must honestly report unavailable -- never "success" just because
    *a* provider response came back.
    """
    elements = [
        _element(1, "Rathaus Rheydt", lat=51.1743, lon=6.4453, tourism="attraction"),
        _element(2, "Balderich", lat=51.18, lon=6.44, historic="yes"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_nyc_geocode_response(),
        post_responses=[
            _FakeResponse(json_data={"elements": elements}),  # primary: all outside bbox
            _FakeResponse(json_data={"elements": []}),  # fallback tag 1
            _FakeResponse(json_data={"elements": []}),  # fallback tag 2
            _FakeResponse(json_data={"elements": []}),  # fallback tag 3
            _FakeResponse(json_data={"elements": []}),  # fallback tag 4
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("New York")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None
    assert fake_client.post_call_count == 5


def test_must_visit_place_outside_destination_bounding_box_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 6: a must-visit lookup result is only accepted if it
    falls inside the resolved destination -- an out-of-area match (even a
    real, named one) must be reported unavailable, never silently accepted
    or substituted with something else.
    """
    bad_result = _nominatim_result("Rathaus Rheydt", lat="51.1743", lon="6.4453")
    fake_client = _install_fake_client_for_get(
        monkeypatch,
        get_responses=[_nyc_geocode_response(), _FakeResponse(json_data=[bad_result])],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_must_visit_place("Empire State Building", "New York")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None
    assert "must_visit_place" in response.unavailable_fields
    assert fake_client.get_call_count == 2


def test_must_visit_place_reports_unavailable_when_destination_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client_for_get(
        monkeypatch, get_responses=[_FakeResponse(json_data=[])]
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_must_visit_place("Empire State Building", "New")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data is None
    # The must-visit-specific lookup is never attempted once the
    # destination itself can't be resolved.
    assert fake_client.get_call_count == 1


# ---------------------------------------------------------------------------
# Provider cache wiring: geocoding only (Step 164E,
# docs/12_provider_architecture.md "Provider Cache Foundation" section).
# Every test below injects its own `ProviderCacheStore` via the constructor
# -- never the real repo-local `.data/provider_cache.sqlite3` file -- and
# never makes a real network call; only the existing `_FakeClient`/
# `_FakeResponse` doubles are used. Overpass POI search behavior is
# intentionally not touched or tested here -- see the tests above, which
# are unchanged and still pass.
# ---------------------------------------------------------------------------


def test_geocode_cache_hit_returns_cached_response_without_http_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = _install_fake_client_for_get(monkeypatch, get_responses=[])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set(
        "openstreetmap_geocode",
        _geocode_query_hash("Los Angeles"),
        {
            "lat": 34.0522,
            "lng": -118.2437,
            "bounding_box": [33.5, 34.5, -118.8, -117.5],
            "display_name": "Los Angeles, California, United States",
        },
        ttl_seconds=2592000,
    )

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    point = adapter.resolve_coordinates("Los Angeles")

    assert fake_client.get_call_count == 0
    assert point is not None
    assert point.lat == pytest.approx(34.0522)
    assert point.lng == pytest.approx(-118.2437)


def test_geocode_cache_miss_calls_http_once_and_writes_normalized_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = _install_fake_client_for_get(monkeypatch, get_responses=[_geocode_ok("Los Angeles")])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    point = adapter.resolve_coordinates("Los Angeles")

    assert fake_client.get_call_count == 1
    assert point is not None
    assert point.lat == pytest.approx(34.0522)

    entry = store.get("openstreetmap_geocode", _geocode_query_hash("Los Angeles"))
    assert entry is not None
    assert entry.payload == {
        "lat": 34.0522,
        "lng": -118.2437,
        "bounding_box": [33.5, 34.5, -118.8, -117.5],
        "display_name": "Los Angeles, California, United States",
    }


def test_second_identical_geocode_call_across_instances_uses_persistent_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinct from the pre-existing `test_resolve_coordinates_reuses_geocode_cache`
    (which proves the per-instance in-memory dict shortcut) -- this uses two
    *separate* adapter instances sharing one persistent `ProviderCacheStore`
    to prove the durable cache itself is what's serving the second call."""
    fake_client = _install_fake_client_for_get(monkeypatch, get_responses=[_geocode_ok("Los Angeles")])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    first_point = OpenStreetMapPlacesAdapter(cache_store=store).resolve_coordinates("Los Angeles")
    second_point = OpenStreetMapPlacesAdapter(cache_store=store).resolve_coordinates("Los Angeles")

    assert fake_client.get_call_count == 1
    assert first_point == second_point


def test_expired_geocode_cache_entry_behaves_like_miss_and_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timedelta, timezone

    fake_client = _install_fake_client_for_get(monkeypatch, get_responses=[_geocode_ok("Los Angeles")])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    already_expired = datetime.now(timezone.utc) - timedelta(hours=1)
    store.set(
        "openstreetmap_geocode",
        _geocode_query_hash("Los Angeles"),
        {
            "lat": 0.0,
            "lng": 0.0,
            "bounding_box": None,
            "display_name": "stale entry",
        },
        ttl_seconds=1,
        now=already_expired,
    )

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    point = adapter.resolve_coordinates("Los Angeles")

    assert fake_client.get_call_count == 1
    assert point is not None
    assert point.lat == pytest.approx(34.0522)
    assert point.lng == pytest.approx(-118.2437)

    refreshed_entry = store.get("openstreetmap_geocode", _geocode_query_hash("Los Angeles"))
    assert refreshed_entry is not None
    assert refreshed_entry.payload["lat"] == pytest.approx(34.0522)


def test_provider_cache_disabled_bypasses_cache_and_calls_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = _install_fake_client_for_get(
        monkeypatch, get_responses=[_geocode_ok("Los Angeles"), _geocode_ok("Los Angeles")]
    )
    disabled_settings = _settings(monkeypatch, provider_cache_enabled=False)
    monkeypatch.setattr(openstreetmap_adapter, "get_settings", lambda: disabled_settings)
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    # Two separate adapter instances (each with its own empty in-memory
    # dict) sharing one injected cache_store -- provider_cache_enabled=False
    # must skip the persistent cache completely, even though it was
    # explicitly injected.
    first_point = OpenStreetMapPlacesAdapter(cache_store=store).resolve_coordinates("Los Angeles")
    second_point = OpenStreetMapPlacesAdapter(cache_store=store).resolve_coordinates("Los Angeles")

    assert fake_client.get_call_count == 2
    assert first_point is not None and second_point is not None
    assert store.get("openstreetmap_geocode", _geocode_query_hash("Los Angeles")) is None


def test_geocode_cache_read_failure_falls_back_to_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client_for_get(monkeypatch, get_responses=[_geocode_ok("Los Angeles")])

    class _RaisingGetCacheStore:
        def get(self, source: str, query_hash: str, now: Any = None) -> None:
            raise RuntimeError("cache backend unavailable")

        def set(self, *args: Any, **kwargs: Any) -> None:
            pass

    adapter = OpenStreetMapPlacesAdapter(cache_store=_RaisingGetCacheStore())
    point = adapter.resolve_coordinates("Los Angeles")

    assert fake_client.get_call_count == 1
    assert point is not None
    assert point.lat == pytest.approx(34.0522)


def test_geocode_cache_write_failure_still_returns_live_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client_for_get(monkeypatch, get_responses=[_geocode_ok("Los Angeles")])

    class _RaisingSetCacheStore:
        def get(self, source: str, query_hash: str, now: Any = None) -> None:
            return None

        def set(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("cache write failed")

    adapter = OpenStreetMapPlacesAdapter(cache_store=_RaisingSetCacheStore())
    point = adapter.resolve_coordinates("Los Angeles")

    assert fake_client.get_call_count == 1
    assert point is not None
    assert point.lat == pytest.approx(34.0522)


def test_geocode_cache_key_is_deterministic_for_equivalent_normalized_queries() -> None:
    assert _geocode_query_hash("Los Angeles") == _geocode_query_hash("  LOS ANGELES  ")
    assert _geocode_query_hash("Los Angeles") == _geocode_query_hash("los angeles")


def test_different_destination_text_produces_different_query_hashes() -> None:
    assert _geocode_query_hash("Los Angeles") != _geocode_query_hash("New York")


def test_geocode_cache_entry_stores_no_secrets_in_payload_or_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client_for_get(monkeypatch, get_responses=[_geocode_ok("Los Angeles")])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    adapter.resolve_coordinates("Los Angeles")

    entry = store.get("openstreetmap_geocode", _geocode_query_hash("Los Angeles"))
    assert entry is not None
    assert entry.metadata == {}

    dumped = str(entry.payload)
    for forbidden in ("api_key", "token", "secret", "authorization", "bearer"):
        assert forbidden not in dumped.lower()


def test_geocode_not_found_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client_for_get(monkeypatch, get_responses=[_FakeResponse(json_data=[])])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    point = adapter.resolve_coordinates("Nowhere")

    assert point is None
    assert store.get("openstreetmap_geocode", _geocode_query_hash("Nowhere")) is None


def test_geocode_implausible_match_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client_for_get(
        monkeypatch,
        get_responses=[
            _FakeResponse(
                json_data=[
                    {
                        "lat": "51.1805",
                        "lon": "6.4428",
                        "display_name": "Rheydt, Mönchengladbach, North Rhine-Westphalia, Germany",
                        "boundingbox": ["51.1", "51.2", "6.4", "6.5"],
                    }
                ]
            )
        ],
    )
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    point = adapter.resolve_coordinates("New York")

    assert point is None
    assert store.get("openstreetmap_geocode", _geocode_query_hash("New York")) is None


def test_geocode_request_failure_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client_for_get(monkeypatch, get_responses=[_FakeResponse(should_fail=True)])
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    point = adapter.resolve_coordinates("Los Angeles")

    assert point is None
    assert store.get("openstreetmap_geocode", _geocode_query_hash("Los Angeles")) is None


# ---------------------------------------------------------------------------
# Provider cache wiring: OSM/Overpass POI search (Step 164G,
# docs/12_provider_architecture.md "Provider Cache Foundation" section).
# Every test below injects its own `ProviderCacheStore` via the constructor
# -- never the real repo-local `.data/provider_cache.sqlite3` file -- and
# never makes a real network call; only the existing `_FakeClient`/
# `_FakeResponse` doubles are used. Geocoding remains covered by the tests
# above, unchanged by this section.
# ---------------------------------------------------------------------------

_CACHED_ATTRACTION_PAYLOAD = [
    {
        "place_id": "node/1",
        "name": "Griffith Observatory",
        "category": "attraction",
        "coordinates": {"lat": 34.0, "lng": -118.0},
        "address": None,
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": 0.6,
    },
    {
        "place_id": "node/2",
        "name": "The Getty",
        "category": "museum",
        "coordinates": {"lat": 34.0, "lng": -118.0},
        "address": None,
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": 0.6,
    },
    {
        "place_id": "node/3",
        "name": "Walt Disney Concert Hall",
        "category": "attraction",
        "coordinates": {"lat": 34.0, "lng": -118.0},
        "address": None,
        "source": "openstreetmap_places",
        "data_status": "live",
        "confidence": 0.6,
    },
]


def test_poi_cache_hit_returns_cached_response_without_http_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = _FakeClient()
    monkeypatch.setattr(openstreetmap_adapter.httpx, "Client", lambda **kwargs: fake_client)

    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set(
        "openstreetmap_geocode",
        _geocode_query_hash("Los Angeles"),
        {
            "lat": 34.0522,
            "lng": -118.2437,
            "bounding_box": [33.5, 34.5, -118.8, -117.5],
            "display_name": "Los Angeles, California, United States",
        },
        ttl_seconds=2592000,
    )
    store.set(
        "openstreetmap_poi",
        _poi_query_hash(),
        _CACHED_ATTRACTION_PAYLOAD,
        ttl_seconds=604800,
    )

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    response = adapter.search_attractions("Los Angeles")

    assert fake_client.get_call_count == 0
    assert fake_client.post_call_count == 0
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE  # envelope status unchanged by caching
    assert response.fallback_used is False
    names = {place.name for place in response.data}
    assert names == {"Griffith Observatory", "The Getty", "Walt Disney Concert Hall"}
    assert all(place.data_status == DataStatus.CACHED for place in response.data)


def test_poi_cache_miss_calls_http_once_and_writes_normalized_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_elements = [
        _element(1, "Griffith Observatory", tourism="attraction"),
        _element(2, "The Getty", tourism="museum"),
        _element(3, "Walt Disney Concert Hall", tourism="attraction"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[_FakeResponse(json_data={"elements": primary_elements})],
    )
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    response = adapter.search_attractions("Los Angeles")

    assert fake_client.post_call_count == 1
    assert response.status == ProviderStatus.SUCCESS

    entry = store.get("openstreetmap_poi", _poi_query_hash())
    assert entry is not None
    names = {item["name"] for item in entry.payload}
    assert names == {"Griffith Observatory", "The Getty", "Walt Disney Concert Hall"}
    for item in entry.payload:
        assert item["data_status"] == "live"


def test_second_identical_poi_call_across_instances_uses_persistent_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_elements = [
        _element(1, "Griffith Observatory", tourism="attraction"),
        _element(2, "The Getty", tourism="museum"),
        _element(3, "Walt Disney Concert Hall", tourism="attraction"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[_FakeResponse(json_data={"elements": primary_elements})],
    )
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    first = OpenStreetMapPlacesAdapter(cache_store=store).search_attractions("Los Angeles")
    second = OpenStreetMapPlacesAdapter(cache_store=store).search_attractions("Los Angeles")

    # Geocoding (Step 164E) and POI search (Step 164G) are both cached, so
    # the second, fresh adapter instance needs no live call for either.
    # `_FakeClient` in geocode_response/post_responses mode only tracks GET
    # calls via `get_queries` (see `_FakeClient.get`), not `get_call_count`.
    assert len(fake_client.get_queries) == 1
    assert fake_client.post_call_count == 1
    assert {p.name for p in first.data} == {p.name for p in second.data}
    assert all(place.data_status == DataStatus.CACHED for place in second.data)


def test_expired_poi_cache_entry_behaves_like_miss_and_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timedelta, timezone

    fresh_elements = [
        _element(1, "Griffith Observatory", tourism="attraction"),
        _element(2, "The Getty", tourism="museum"),
        _element(3, "Walt Disney Concert Hall", tourism="attraction"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[_FakeResponse(json_data={"elements": fresh_elements})],
    )
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    already_expired = datetime.now(timezone.utc) - timedelta(hours=1)
    store.set(
        "openstreetmap_poi",
        _poi_query_hash(),
        [
            {
                "place_id": "node/999",
                "name": "Stale Place",
                "category": "attraction",
                "coordinates": {"lat": 34.0, "lng": -118.0},
                "address": None,
                "source": "openstreetmap_places",
                "data_status": "live",
                "confidence": 0.6,
            }
        ],
        ttl_seconds=1,
        now=already_expired,
    )

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    response = adapter.search_attractions("Los Angeles")

    assert fake_client.post_call_count == 1
    names = {place.name for place in response.data}
    assert names == {"Griffith Observatory", "The Getty", "Walt Disney Concert Hall"}
    assert "Stale Place" not in names

    refreshed_entry = store.get("openstreetmap_poi", _poi_query_hash())
    assert refreshed_entry is not None
    refreshed_names = {item["name"] for item in refreshed_entry.payload}
    assert refreshed_names == {"Griffith Observatory", "The Getty", "Walt Disney Concert Hall"}


def test_poi_provider_cache_disabled_bypasses_cache_and_calls_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_elements = [
        _element(1, "Griffith Observatory", tourism="attraction"),
        _element(2, "The Getty", tourism="museum"),
        _element(3, "Walt Disney Concert Hall", tourism="attraction"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(json_data={"elements": primary_elements}),
            _FakeResponse(json_data={"elements": primary_elements}),
        ],
    )
    disabled_settings = _settings(monkeypatch, provider_cache_enabled=False)
    monkeypatch.setattr(openstreetmap_adapter, "get_settings", lambda: disabled_settings)
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    # Even with an explicit cache_store injected, provider_cache_enabled=False
    # must skip the cache completely -- two fresh instances both go live.
    first = OpenStreetMapPlacesAdapter(cache_store=store).search_attractions("Los Angeles")
    second = OpenStreetMapPlacesAdapter(cache_store=store).search_attractions("Los Angeles")

    assert len(fake_client.get_queries) == 2
    assert fake_client.post_call_count == 2
    assert first.status == ProviderStatus.SUCCESS
    assert second.status == ProviderStatus.SUCCESS
    assert store.get("openstreetmap_poi", _poi_query_hash()) is None


def test_poi_cache_read_failure_falls_back_to_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_elements = [
        _element(1, "Griffith Observatory", tourism="attraction"),
        _element(2, "The Getty", tourism="museum"),
        _element(3, "Walt Disney Concert Hall", tourism="attraction"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[_FakeResponse(json_data={"elements": primary_elements})],
    )

    class _RaisingGetCacheStore:
        def get(self, source: str, query_hash: str, now: Any = None) -> None:
            raise RuntimeError("cache backend unavailable")

        def set(self, *args: Any, **kwargs: Any) -> None:
            pass

    adapter = OpenStreetMapPlacesAdapter(cache_store=_RaisingGetCacheStore())
    response = adapter.search_attractions("Los Angeles")

    assert fake_client.post_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    names = {place.name for place in response.data}
    assert names == {"Griffith Observatory", "The Getty", "Walt Disney Concert Hall"}


def test_poi_cache_write_failure_still_returns_live_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_elements = [
        _element(1, "Griffith Observatory", tourism="attraction"),
        _element(2, "The Getty", tourism="museum"),
        _element(3, "Walt Disney Concert Hall", tourism="attraction"),
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[_FakeResponse(json_data={"elements": primary_elements})],
    )

    class _RaisingSetCacheStore:
        def get(self, source: str, query_hash: str, now: Any = None) -> None:
            return None

        def set(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("cache write failed")

    adapter = OpenStreetMapPlacesAdapter(cache_store=_RaisingSetCacheStore())
    response = adapter.search_attractions("Los Angeles")

    assert fake_client.post_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    names = {place.name for place in response.data}
    assert names == {"Griffith Observatory", "The Getty", "Walt Disney Concert Hall"}


def test_poi_cache_key_is_deterministic_for_equivalent_normalized_queries() -> None:
    query_a = {
        "lat": 34.0522,
        "lon": -118.2437,
        "radius_meters": 6000,
        "tags": sorted(["b_tag", "a_tag"]),
        "limit": 20,
    }
    query_b = {
        "limit": 20,
        "tags": sorted(["a_tag", "b_tag"]),
        "radius_meters": 6000,
        "lon": -118.2437,
        "lat": 34.0522,
    }
    assert make_query_hash(query_a) == make_query_hash(query_b)


def test_different_poi_category_radius_or_location_produces_different_query_hashes() -> None:
    base_hash = _poi_query_hash()

    different_tags_hash = _poi_query_hash(tags=openstreetmap_adapter._RESTAURANT_TAG_FILTERS)
    different_radius_hash = _poi_query_hash(radius_meters=12000)
    different_location_hash = _poi_query_hash(lat=40.7128, lon=-74.0060)

    assert base_hash != different_tags_hash
    assert base_hash != different_radius_hash
    assert base_hash != different_location_hash


def test_poi_cache_entry_stores_no_secrets_in_payload_or_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_elements = [
        _element(1, "Griffith Observatory", tourism="attraction"),
        _element(2, "The Getty", tourism="museum"),
        _element(3, "Walt Disney Concert Hall", tourism="attraction"),
    ]
    _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[_FakeResponse(json_data={"elements": primary_elements})],
    )
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    adapter.search_attractions("Los Angeles")

    entry = store.get("openstreetmap_poi", _poi_query_hash())
    assert entry is not None
    assert entry.metadata == {}

    dumped = str(entry.payload)
    for forbidden in ("api_key", "token", "secret", "authorization", "bearer"):
        assert forbidden not in dumped.lower()


def test_failed_poi_queries_are_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok("Nowhere Land"),
        post_responses=[_FakeResponse(should_fail=True)] * 5,
    )
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    response = adapter.search_attractions("Nowhere Land")

    assert response.status == ProviderStatus.FAILED
    assert store.get("openstreetmap_poi", _poi_query_hash()) is None
    assert (
        store.get(
            "openstreetmap_poi",
            _poi_query_hash(
                radius_meters=12000, tags=[openstreetmap_adapter._ATTRACTION_FALLBACK_TAG_FILTERS[0]]
            ),
        )
        is None
    )


def test_empty_unnamed_only_poi_queries_are_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Primary and every fallback tag query return unnamed-only/empty
    elements (no request failure, just nothing usable) -- none of these
    per-query results should be cached."""
    primary_elements = [_element(1, None, tourism="attraction")]  # unnamed only
    _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok("Nowhere Land"),
        post_responses=[
            _FakeResponse(json_data={"elements": primary_elements}),
            _FakeResponse(json_data={"elements": []}),
            _FakeResponse(json_data={"elements": []}),
            _FakeResponse(json_data={"elements": []}),
            _FakeResponse(json_data={"elements": [_element(2, None, leisure="park")]}),
        ],
    )
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    response = adapter.search_attractions("Nowhere Land")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert store.get("openstreetmap_poi", _poi_query_hash()) is None
    for fallback_tag in openstreetmap_adapter._ATTRACTION_FALLBACK_TAG_FILTERS:
        assert store.get("openstreetmap_poi", _poi_query_hash(radius_meters=12000, tags=[fallback_tag])) is None


def test_poi_cache_introduces_no_forbidden_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache-hit POI result must carry no rating, price, opening hours,
    availability, or booking/route-time field -- the same guarantee the
    live path already provides, confirmed here specifically through the
    cache-hit code path."""
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set("openstreetmap_geocode", _geocode_query_hash("Los Angeles"), {
        "lat": 34.0522,
        "lng": -118.2437,
        "bounding_box": [33.5, 34.5, -118.8, -117.5],
        "display_name": "Los Angeles, California, United States",
    }, ttl_seconds=2592000)
    store.set("openstreetmap_poi", _poi_query_hash(), _CACHED_ATTRACTION_PAYLOAD, ttl_seconds=604800)
    fake_client = _FakeClient()
    monkeypatch.setattr(openstreetmap_adapter.httpx, "Client", lambda **kwargs: fake_client)

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    response = adapter.search_attractions("Los Angeles")

    for place in response.data:
        dumped = place.model_dump()
        assert set(dumped.keys()) == {
            "place_id",
            "name",
            "category",
            "coordinates",
            "address",
            "source",
            "data_status",
            "confidence",
        }
        for forbidden_field in (
            "rating",
            "price",
            "review",
            "opening_hours",
            "booking_url",
            "availability",
            "route_time",
        ):
            assert forbidden_field not in dumped


def test_poi_cache_hit_does_not_change_provider_coverage_relevant_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The overall `ProviderResponse` envelope (`status`, `data_status`,
    `fallback_used`, `fallback_provider`, `unavailable_fields`) is
    unaffected by whether the underlying places came from cache -- exactly
    what `destination_context_service.py`'s `provider_coverage`/
    `data_sources_used` tracking reads. Only individual `NormalizedPlace.
    data_status` differs on a cache hit."""
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set("openstreetmap_geocode", _geocode_query_hash("Los Angeles"), {
        "lat": 34.0522,
        "lng": -118.2437,
        "bounding_box": [33.5, 34.5, -118.8, -117.5],
        "display_name": "Los Angeles, California, United States",
    }, ttl_seconds=2592000)
    store.set("openstreetmap_poi", _poi_query_hash(), _CACHED_ATTRACTION_PAYLOAD, ttl_seconds=604800)
    fake_client = _FakeClient()
    monkeypatch.setattr(openstreetmap_adapter.httpx, "Client", lambda **kwargs: fake_client)

    adapter = OpenStreetMapPlacesAdapter(cache_store=store)
    response = adapter.search_attractions("Los Angeles")

    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.fallback_used is False
    assert response.fallback_provider is None
    assert response.unavailable_fields == []
