from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.models.common import DataStatus, ProviderStatus
from app.providers.places import openstreetmap_adapter
from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter


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

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
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


def _geocode_ok() -> _FakeResponse:
    return _FakeResponse(json_data=[{"lat": "34.0522", "lon": "-118.2437"}])


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
    fallback_elements = [
        _element(10, "Echo Park", leisure="park"),
        _element(11, "La Brea Tar Pits", tourism="museum"),
        _element(12, "Hollywood Bowl", amenity="arts_centre"),
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
    response = adapter.search_attractions("Los Angeles")

    assert response.status == ProviderStatus.FALLBACK_USED
    assert response.data_status == DataStatus.FALLBACK_USED
    assert response.fallback_used is True
    assert response.fallback_provider == "openstreetmap_places"
    assert "fallback" in (response.message or "").lower()
    names = {place.name for place in response.data}
    assert names == {"Echo Park", "La Brea Tar Pits", "Hollywood Bowl"}
    # Both the primary (failed) and fallback queries were attempted.
    assert fake_client.post_call_count == 2


def test_fallback_still_filters_unnamed_places(monkeypatch: pytest.MonkeyPatch) -> None:
    primary_elements = [_element(1, None, tourism="attraction")]  # unnamed only
    fallback_elements = [
        _element(20, None, leisure="park"),  # unnamed, must be dropped
        _element(21, "Runyon Canyon Park", leisure="park"),
        _element(22, None, amenity="arts_centre"),  # unnamed, must be dropped
    ]
    fake_client = _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(json_data={"elements": primary_elements}),
            _FakeResponse(json_data={"elements": fallback_elements}),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Los Angeles")

    assert response.fallback_used is True
    assert [place.name for place in response.data] == ["Runyon Canyon Park"]
    assert fake_client.post_call_count == 2


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
    _install_fake_client(
        monkeypatch,
        geocode_response=_geocode_ok(),
        post_responses=[
            _FakeResponse(should_fail=True),
            _FakeResponse(should_fail=True),
        ],
    )

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_attractions("Nowhere Land")

    assert response.status == ProviderStatus.FAILED
    assert response.data_status == DataStatus.FAILED
    assert response.data is None
    assert response.fallback_used is False
    assert "attractions" in response.unavailable_fields


def test_fallback_result_creates_no_fake_rating_price_review_or_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The raw OSM element carries opening_hours/rating-shaped tags, but the
    # adapter must not read or propagate them into NormalizedPlace.
    fallback_elements = [
        _element(
            40,
            "Griffith Park",
            leisure="park",
            opening_hours="Mo-Su 05:00-22:30",
            stars="4.5",
        ),
        _element(41, "Bronson Canyon", leisure="park"),
        _element(42, "Barnsdall Art Park", amenity="arts_centre"),
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
        geocode_response=_geocode_ok(),
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
        get_responses=[_FakeResponse(json_data=[_nominatim_result("Griffith Observatory")])],
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
    # unconstrained global search.
    assert fake_client.get_call_count == 1


def test_search_must_visit_place_no_results_does_not_invent_a_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_client_for_get(monkeypatch, get_responses=[_FakeResponse(json_data=[])])

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
    _install_fake_client_for_get(monkeypatch, get_responses=[_FakeResponse(json_data=[result])])

    adapter = OpenStreetMapPlacesAdapter()
    response = adapter.search_must_visit_place("Some Place", "Los Angeles")

    assert response.data is None
    assert response.status == ProviderStatus.UNAVAILABLE


def test_search_must_visit_place_request_failure_stays_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_client_for_get(
        monkeypatch, get_responses=[_FakeResponse(should_fail=True)]
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
        get_responses=[_FakeResponse(json_data=[_nominatim_result("Griffith Observatory")])],
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
