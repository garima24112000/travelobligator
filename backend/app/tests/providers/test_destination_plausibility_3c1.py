from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
from app.providers.places import destination_resolution as resolution
from app.providers.places.geoapify_places_adapter import GeoapifyPlacesAdapter
from app.storage.provider_cache_store import ProviderCacheStore

# Section 3C.1: destination geocode plausibility. A provider answers in one
# language, so a city with more than one romanised spelling can come back
# under a spelling the traveller did not type. Every fixture here is
# synthetic (no real city, country or coordinate), in the provider's own
# result shape, and judged through the real adapter translation.


def _geoapify(**fields: Any) -> dict[str, Any]:
    """One Geoapify forward-geocoding result (only the fields the adapter reads)."""
    return {"place_id": "abc123", "lat": 10.0, "lon": 20.0, **fields}


def _reason(query: str, result: dict[str, Any]) -> str | None:
    hit = GeoapifyGeocoder()._hit(result)
    assert hit is not None
    return resolution.destination_rejection_reason(query, resolution._hit_evidence(hit))


def _city(name: str, country: str = "Fixtureland", **extra: Any) -> dict[str, Any]:
    return _geoapify(
        result_type="city", name=name, city=name, country=country, country_code="fx",
        formatted=f"{name}, {country}", **extra,
    )


# 1. exact city + country
def test_an_exact_city_and_country_is_accepted() -> None:
    assert _reason("Tamrakesh, Fixtureland", _city("Tamrakesh")) is None
    assert _reason("Tamrakesh", _city("Tamrakesh")) is None


# 2. normalized spelling variation / equivalent structured locality
@pytest.mark.parametrize(
    ("query", "provider_name"),
    [
        ("Tamrakech, Fixtureland", "Tamrakesh"),  # one letter substituted (a second romanisation)
        ("Tamrakesh, Fixtureland", "Tamrakech"),  # the same, the other way round
        ("Tamrakesh, Fixtureland", "Tamrakeesh"),  # one letter inserted
        ("Tamrakeesh, Fixtureland", "Tamrakesh"),  # one letter dropped
        ("Tamrakehs, Fixtureland", "Tamrakesh"),  # two adjacent letters swapped
        ("Port Tamrakech, Fixtureland", "Port Tamrakesh"),  # a multi-word name
    ],
)
def test_an_equivalent_spelling_of_the_same_city_is_accepted_on_structured_evidence(
    query: str, provider_name: str
) -> None:
    assert _reason(query, _city(provider_name)) is None


# 3. accent / Unicode variation
@pytest.mark.parametrize(
    ("query", "provider_name", "country"),
    [
        ("Tamrákesh, Fixtureland", "Tamrakesh", "Fixtureland"),
        ("Tamrakesh, Fixtureland", "Tamrákesh", "Fixtureland"),
        ("TAMRAKESH, fixtureland", "Tamrakesh", "Fixtureland"),
        ("Tamrakesh, Fïxtureland", "Tamrakesh", "Fixtureland"),
        ("Tamrákech, Fixtureland", "Tamrakesh", "Fixtureland"),  # an accent AND an alternate spelling
        ("Tam-Rakesh, Fixtureland", "Tam Rakesh", "Fixtureland"),  # punctuation is not part of a name
    ],
)
def test_accents_case_and_punctuation_do_not_matter(query: str, provider_name: str, country: str) -> None:
    assert _reason(query, _city(provider_name, country)) is None


# 4. alternate administrative representation
def test_an_alternate_administrative_representation_of_the_city_is_accepted() -> None:
    # the city returned as its own county / prefecture, named after it
    county = _geoapify(
        result_type="county", county="Tamrakesh Prefecture", state="Northern Region", country="Fixtureland",
        formatted="Tamrakesh Prefecture, Fixtureland",
    )
    assert _reason("Tamrakesh, Fixtureland", county) is None
    # a district-level result whose city component is the requested city
    district = _geoapify(
        result_type="district", name="Old Quarter", district="Old Quarter", city="Tamrakesh", country="Fixtureland",
        formatted="Old Quarter, Tamrakesh, Fixtureland",
    )
    assert _reason("Tamrakesh, Fixtureland", district) is None
    # the city with its region supplied as a middle segment
    assert _reason("Tamrakesh, Northern Region, Fixtureland", _city("Tamrakesh", state="Northern Region")) is None
    assert _reason("Tamrakech, Northern Region, Fixtureland", _city("Tamrakesh", state="Northern Region")) is None


# 5. same city name in the wrong country -> reject
def test_the_same_city_name_in_another_country_is_rejected() -> None:
    assert _reason("Tamrakesh, Fixtureland", _city("Tamrakesh", "Otherland")) == resolution.REJECT_COUNTRY_MISMATCH
    # an alternate spelling never excuses a wrong country: the name itself is then not accepted
    assert _reason("Tamrakech, Fixtureland", _city("Tamrakesh", "Otherland")) == resolution.REJECT_LOCALITY_MISMATCH
    # ... and a contradicting region still rejects an otherwise equivalent city
    assert (
        _reason("Tamrakech, Southern Region, Fixtureland", _city("Tamrakesh", state="Northern Region"))
        == resolution.REJECT_REGION_MISMATCH
    )


def test_an_alternate_spelling_needs_strong_country_agreement() -> None:
    city = _city("Tamrakesh")
    # no country in the query, or one too short to verify: token-exact names only
    assert _reason("Tamrakech", city) == resolution.REJECT_LOCALITY_MISMATCH
    assert _reason("Tamrakech, FX", city) == resolution.REJECT_LOCALITY_MISMATCH
    # the provider gave no country component at all
    no_country = _geoapify(result_type="city", name="Tamrakesh", city="Tamrakesh", formatted="Tamrakesh, Fixtureland")
    assert _reason("Tamrakech, Fixtureland", no_country) == resolution.REJECT_LOCALITY_MISMATCH
    # a partial country name is not agreement for an alternate spelling
    assert _reason("Tamrakech, Fixtureland", _city("Tamrakesh", "Fixtureland Republic")) == resolution.REJECT_LOCALITY_MISMATCH
    # ... though an exactly named city keeps the long-standing subset rule
    assert _reason("Tamrakesh, Fixtureland", _city("Tamrakesh", "Fixtureland Republic")) is None


# 6. unrelated locality in the correct country -> reject
@pytest.mark.parametrize(
    "result",
    [
        _city("Belvarro"),  # a different city altogether
        _city("Tamrakand"),  # similar, but more than one edit away
        _city("Tamraskeh"),  # two separate edits
        _city("Tamras"),  # a different, shorter name
        # an alternate spelling is only accepted for a CITY-level result: never for a
        # suburb, a county or a whole region that merely carries a similar name
        _geoapify(result_type="suburb", name="Tamrakesh", suburb="Tamrakesh", city="Belvarro",
                  country="Fixtureland", formatted="Tamrakesh, Belvarro, Fixtureland"),
        _geoapify(result_type="county", county="Tamrakesh", country="Fixtureland",
                  formatted="Tamrakesh, Fixtureland"),
        _geoapify(result_type="state", state="Tamrakesh-Safira", country="Fixtureland",
                  formatted="Tamrakesh-Safira, Fixtureland"),
    ],
)
def test_an_unrelated_locality_in_the_right_country_is_rejected(result: dict[str, Any]) -> None:
    assert _reason("Tamrakech, Fixtureland", result) == resolution.REJECT_LOCALITY_MISMATCH


def test_short_names_never_match_by_a_single_edit() -> None:
    # a one-letter difference between short names is a different place, not a spelling
    assert _reason("Linz, Fixtureland", _city("Lienz")) == resolution.REJECT_LOCALITY_MISMATCH
    assert _reason("Bern, Fixtureland", _city("Bonn")) == resolution.REJECT_LOCALITY_MISMATCH


# 7. malformed / insufficient provider result -> reject
def test_an_unsupported_result_type_is_rejected() -> None:
    for result_type in ("amenity", "building", "street", "postcode", "unknown"):
        result = _geoapify(
            result_type=result_type, name="Tamrakesh", city="Tamrakesh", country="Fixtureland",
            formatted="Tamrakesh, Fixtureland",
        )
        assert _reason("Tamrakesh, Fixtureland", result) == resolution.REJECT_UNSUPPORTED_RESULT_TYPE
    # no result type at all
    untyped = _geoapify(name="Tamrakesh", city="Tamrakesh", country="Fixtureland", formatted="Tamrakesh, Fixtureland")
    assert _reason("Tamrakesh, Fixtureland", untyped) == resolution.REJECT_UNSUPPORTED_RESULT_TYPE


def test_a_result_without_usable_geographic_evidence_is_rejected() -> None:
    # an area result that names nothing
    assert _reason("Tamrakesh, Fixtureland", _geoapify(result_type="city")) == resolution.REJECT_INSUFFICIENT_GEO_EVIDENCE
    # a query with no place name
    assert _reason(" , -- ", _city("Tamrakesh")) == resolution.REJECT_INSUFFICIENT_GEO_EVIDENCE
    # no coordinates / no identity: the adapter yields no hit at all
    geocoder = GeoapifyGeocoder()
    assert geocoder._hit({"result_type": "city", "name": "Tamrakesh", "place_id": "abc"}) is None
    assert geocoder._hit({"result_type": "city", "name": "Tamrakesh", "lat": 1.0, "lon": 2.0}) is None
    # no structural evidence and nothing in common with the query (legacy shape)
    assert (
        resolution.destination_rejection_reason("Tamrakesh", {"display_name": "Belvarro, Fixtureland"})
        == resolution.REJECT_LOCALITY_MISMATCH
    )
    assert (
        resolution.destination_rejection_reason("NY", {"display_name": "NY, Fixtureland"})
        == resolution.REJECT_INSUFFICIENT_GEO_EVIDENCE
    )


def test_single_edit_distance_is_exact() -> None:
    apart = resolution._single_edit_apart
    assert apart("tamrakech", "tamrakesh") and apart("tamrakesh", "tamrakeesh") and apart("tamrakeesh", "tamrakesh")
    assert apart("tamrakehs", "tamrakesh") and apart("xtamrakesh", "tamrakesh") and apart("tamrakes", "tamrakesh")
    assert not apart("tamrakesh", "tamrakesh")  # identical is not "an edit apart"
    assert not apart("tamrakech", "tamrakand") and not apart("tamrakesh", "tamraskeh")
    assert not apart("tamrakesh", "tamrakeshes") and not apart("", "ab")
    assert not apart("abcdef", "badcfe")


def test_the_bool_wrapper_agrees_with_the_reason_and_existing_rules_are_unchanged() -> None:
    hit = GeoapifyGeocoder()._hit(_city("Tamrakesh"))
    evidence = resolution._hit_evidence(hit)
    assert evidence["type"] == "city" and evidence["category"] == "place"
    assert resolution._is_plausible_geocode_result("Tamrakech, Fixtureland", evidence) is True
    assert resolution._is_plausible_geocode_result("Tamrakech, Otherland", evidence) is False


# -- through the real resolution path: accepted, cached, and safely logged ------------------------------


class _Network:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return httpx.Response(200, json={"results": [self.result]})


def _adapter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, network: _Network) -> GeoapifyPlacesAdapter:
    monkeypatch.setenv("GEOAPIFY_API_KEY", "SENTINEL_3C1_GEOCODE_KEY")
    monkeypatch.setenv("GEOCODING_PROVIDER", "geoapify")
    get_settings.cache_clear()
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(network.handler), **kwargs)
    )
    return GeoapifyPlacesAdapter(cache_store=ProviderCacheStore(tmp_path / "cache.sqlite3"), geocoder=GeoapifyGeocoder())


def test_an_equivalent_city_resolves_once_and_is_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    network = _Network(_city("Tamrakesh", bbox={"lat1": 9.9, "lat2": 10.1, "lon1": 19.9, "lon2": 20.1}))
    adapter = _adapter(monkeypatch, tmp_path, network)

    described = adapter.describe_destination("Tamrakech, Fixtureland")
    assert described is not None
    # the provider's own name and components are kept exactly as returned -- nothing is rewritten
    assert described["display_name"] == "Tamrakesh, Fixtureland"
    assert described["city"] == "Tamrakesh" and described["country"] == "Fixtureland"
    point = adapter.resolve_coordinates("Tamrakech, Fixtureland")
    assert point is not None and (point.lat, point.lng) == (10.0, 20.0)
    assert network.requests == 1  # resolved once, then served from the cache


def test_a_rejection_logs_a_fixed_reason_code_and_never_the_query_or_the_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    network = _Network(_city("Tamrakesh", "Otherland"))
    adapter = _adapter(monkeypatch, tmp_path, network)

    with caplog.at_level(logging.WARNING, logger=resolution.logger.name):
        assert adapter.describe_destination("Tamrakesh, Fixtureland") is None

    messages = [record.getMessage() for record in caplog.records if record.name == resolution.logger.name]
    assert messages == [
        "Rejecting implausible geocode match (provider=geoapify, reason=country_mismatch, result_type=city)."
    ]
    logged = " ".join(messages)
    for forbidden in ("Tamrakesh", "Fixtureland", "Otherland", "SENTINEL", "apiKey", "http"):
        assert forbidden not in logged
    # a rejected result is never cached as a resolution
    assert adapter.resolve_coordinates("Tamrakesh, Fixtureland") is None and network.requests == 2
