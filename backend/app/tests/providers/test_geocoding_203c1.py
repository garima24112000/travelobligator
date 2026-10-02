from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
import redis

from app.core.config import Settings
from app.core.operational_config import (
    OperationalConfigurationError,
    startup_config_summary,
    validate_runtime_configuration,
)
from app.core.redaction import redact
from app.models.ai_candidate_proposal import (
    AICandidateProposal,
    AICandidateType,
    AICandidateVerificationRequirement,
)
from app.models.ai_provider_discovery import AIProviderDiscoveryAttemptStatus
from app.models.common import DataStatus, ProviderStatus
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.base import CurrencyProvider, HolidayProvider, WeatherProvider
from app.providers.gateway import ProviderGateway
from app.providers.geocoding import geoapify_adapter, nominatim_adapter
from app.providers.geocoding.base import GeocoderError, GeocodingProvider
from app.providers.geocoding.factory import get_geocoding_provider
from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
from app.providers.geocoding.nominatim_adapter import NominatimGeocoder
from app.providers.places import destination_resolution, openstreetmap_adapter
from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter
from app.services.ai_directed_provider_discovery_service import AIDirectedProviderDiscoveryService
from app.services.destination_context_service import DestinationContextService
from app.storage.provider_cache_store import ProviderCacheStore
from app.storage.redis_provider_cache_store import RedisProviderCacheStore
from app.tests.repositories.test_redis_provider_cache_store import FakeRedis

# Section 203C.1: production geocoder resilience. Every HTTP exchange here is
# an in-process `httpx.MockTransport` -- no Geoapify, Nominatim or Overpass
# request is ever made, and the key below is a sentinel, not a credential.

_KEY = "SENTINEL_203C1_GEOAPIFY_KEY_5527"
_RealClient = httpx.Client

_LISBON = {
    "datasource": {"sourcename": "openstreetmap"},
    "country": "Portugal",
    "country_code": "pt",
    "county": "Lisbon",
    "city": "Lisbon",
    "lon": -9.1365919,
    "lat": 38.7077507,
    "result_type": "city",
    "formatted": "Lisbon, Portugal",
    "category": "administrative",
    "rank": {"confidence": 1, "match_type": "full_match"},
    "place_id": "51aa",
    "bbox": {"lon1": -9.2298, "lat1": 38.6913, "lon2": -9.0863, "lat2": 38.7967},
}
_BELEM = {
    "datasource": {"sourcename": "openstreetmap"},
    "name": "Torre de Belém",
    "country": "Portugal",
    "city": "Lisbon",
    "lon": -9.2159,
    "lat": 38.6916,
    "result_type": "amenity",
    "formatted": "Torre de Belém, Avenida Brasília, 1400-038 Lisbon, Portugal",
    "category": "tourism.sights.tower",
    "rank": {"confidence": 1, "match_type": "full_match"},
    "place_id": "51bb",
}
_OVERPASS_ELEMENTS = {
    "elements": [
        {"type": "node", "id": 1, "lat": 38.7139, "lon": -9.1335, "tags": {"name": "Castelo de São Jorge", "historic": "castle"}},
        {"type": "node", "id": 2, "lat": 38.7100, "lon": -9.1400, "tags": {"name": "Museu do Fado", "tourism": "museum"}},
        {"type": "node", "id": 3, "lat": 38.7120, "lon": -9.1390, "tags": {"name": "Sé de Lisboa", "historic": "cathedral"}},
    ]
}


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


class _Network:
    """Routes requests by host and records them. `geocode` maps a request to
    an `httpx.Response` (or raises)."""

    def __init__(self, geocode: Callable[[httpx.Request], httpx.Response]) -> None:
        self._geocode = geocode
        self.geocode_requests: list[httpx.Request] = []
        self.overpass_requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if "overpass" in request.url.host:
            self.overpass_requests += 1
            return httpx.Response(200, json=_OVERPASS_ELEMENTS)
        if "geoapify" not in request.url.host and "nominatim" not in request.url.host:
            return httpx.Response(404)  # any other provider: never a real network call
        self.geocode_requests.append(request)
        return self._geocode(request)

    def client(self, **kwargs: Any) -> httpx.Client:
        return _RealClient(transport=httpx.MockTransport(self), **kwargs)


def _results(*results: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json={"results": list(results)})


def _by_text(request: httpx.Request) -> httpx.Response:
    text = request.url.params.get("text") or ""
    if text == "Lisbon, Portugal":
        return httpx.Response(200, json={"results": [_LISBON]})
    if text.startswith("Torre de Belém"):
        return httpx.Response(200, json={"results": [_BELEM]})
    return httpx.Response(200, json={"results": []})


@pytest.fixture()
def geoapify(monkeypatch: pytest.MonkeyPatch) -> GeoapifyGeocoder:
    monkeypatch.setattr(
        geoapify_adapter, "get_settings", lambda: _settings(GEOCODING_PROVIDER="geoapify", GEOAPIFY_API_KEY=_KEY)
    )
    return GeoapifyGeocoder()


def _places_adapter(
    monkeypatch: pytest.MonkeyPatch,
    network: _Network,
    geocoder: GeocodingProvider,
    store: Any,
) -> OpenStreetMapPlacesAdapter:
    monkeypatch.setattr(openstreetmap_adapter.httpx, "Client", network.client)
    return OpenStreetMapPlacesAdapter(cache_store=store, geocoder=geocoder)


# -- Geoapify adapter ---------------------------------------------------------------------------


def test_geoapify_destination_lookup_is_bounded_and_translated(geoapify: GeoapifyGeocoder) -> None:
    network = _Network(_results(_LISBON))
    hit = geoapify.search_destination(network.client(), "Lisbon, Portugal")

    params = network.geocode_requests[0].url.params
    assert network.geocode_requests[0].url.path == "/v1/geocode/search"
    assert (params["text"], params["limit"], params["format"]) == ("Lisbon, Portugal", "1", "json")
    assert hit is not None
    assert (hit.lat, hit.lon) == (38.7077507, -9.1365919)
    assert hit.feature_class == "place"
    assert hit.address["country"] == "Portugal"
    assert hit.bounding_box == (38.6913, 38.7967, -9.2298, -9.0863)
    assert (hit.source, hit.provider_place_id) == ("geoapify", "geoapify/51aa")


def test_geoapify_named_place_keeps_geoapify_identity_and_is_constrained_to_the_destination(
    geoapify: GeoapifyGeocoder,
) -> None:
    network = _Network(_results(_BELEM))
    hit = geoapify.search_named_place(
        network.client(), "Torre de Belém, Lisbon, Portugal", bounding_box=(38.69, 38.80, -9.23, -9.08)
    )

    assert network.geocode_requests[0].url.params["filter"] == "rect:-9.23,38.69,-9.08,38.8"
    assert hit is not None
    assert hit.name == "Torre de Belém"
    assert (hit.lat, hit.lon) == (38.6916, -9.2159)
    # Geoapify returns no OSM type/id: the identity is Geoapify's own, never an OSM-looking id.
    assert (hit.source, hit.provider_place_id) == ("geoapify", "geoapify/51bb")
    assert hit.feature_type == "tower"
    # Section 203C.2B: Geoapify's own category, renamed into the taxonomy's tag
    # vocabulary (provider classification only; no OSM identity is claimed).
    assert hit.tags == {"man_made": "tower", "category_path": "tourism.sights.tower"}


def test_geoapify_no_match_is_none(geoapify: GeoapifyGeocoder) -> None:
    network = _Network(_results())
    assert geoapify.search_destination(network.client(), "Zzyzx Nowhere") is None
    assert geoapify.search_named_place(network.client(), "Zzyzx, Lisbon") is None


@pytest.mark.parametrize(
    "result",
    [
        {**_LISBON, "rank": {"confidence": 1, "match_type": "match_by_city_or_district"}},  # city fallback
        {**_BELEM, "result_type": "street"},
        {**_BELEM, "rank": {"confidence": 0.2, "match_type": "full_match"}},  # fuzzy
        {**_BELEM, "rank": {"confidence": 1, "match_type": "match_by_street"}},
        {key: value for key, value in _BELEM.items() if key != "rank"},  # no match evidence
        {key: value for key, value in _BELEM.items() if key != "name"},
    ],
)
def test_geoapify_fuzzy_or_fallback_result_is_not_a_named_place(
    geoapify: GeoapifyGeocoder, result: dict[str, Any]
) -> None:
    network = _Network(_results(result))
    assert geoapify.search_named_place(network.client(), "Torre de Belém, Lisbon, Portugal") is None


def _raise_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize(
    ("handler", "kind"),
    [
        (lambda request: httpx.Response(200, text="<html>not json</html>"), "malformed"),
        (lambda request: httpx.Response(200, json={"features": []}), "malformed"),
        (lambda request: httpx.Response(200, json={"results": ["x"]}), "malformed"),
        (lambda request: httpx.Response(200, json={"results": [{**_LISBON, "lat": "north"}]}), "malformed"),
        (_raise_timeout, "timeout"),
        (lambda request: httpx.Response(401, json={"message": "Invalid apiKey"}), "auth"),
        (lambda request: httpx.Response(403), "auth"),
        (lambda request: httpx.Response(429), "rate_limited"),
        (lambda request: httpx.Response(503), "server"),
    ],
)
def test_geoapify_failures_are_classified_with_a_fixed_secret_free_message(
    geoapify: GeoapifyGeocoder, handler: Callable[[httpx.Request], httpx.Response], kind: str
) -> None:
    network = _Network(handler)
    with pytest.raises(GeocoderError) as excinfo:
        geoapify.search_destination(network.client(), "Lisbon, Portugal")

    assert excinfo.value.kind == kind
    assert _KEY not in str(excinfo.value) and "http" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None  # the httpx error (whose text holds the URL) is not chained


def test_geoapify_429_and_rejected_key_stop_further_requests(geoapify: GeoapifyGeocoder) -> None:
    for status, kind in ((429, "rate_limited"), (401, "auth")):
        geoapify_adapter.request_breaker.reset()
        network = _Network(lambda request, status=status: httpx.Response(status))
        for _ in range(3):
            with pytest.raises(GeocoderError) as excinfo:
                geoapify.search_named_place(network.client(), "Torre de Belém, Lisbon, Portugal")
            assert excinfo.value.kind == kind
        assert len(network.geocode_requests) == 1


def test_geoapify_without_a_key_is_not_connected_and_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(geoapify_adapter, "get_settings", lambda: _settings(GEOCODING_PROVIDER="geoapify"))
    network = _Network(_results(_LISBON))
    with pytest.raises(GeocoderError) as excinfo:
        GeoapifyGeocoder().search_destination(network.client(), "Lisbon, Portugal")
    assert excinfo.value.kind == "not_connected"
    assert network.geocode_requests == []


def test_geoapify_key_never_reaches_logs_or_provider_messages(
    monkeypatch: pytest.MonkeyPatch, geoapify: GeoapifyGeocoder, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="app")
    messages: list[str] = []
    for handler in (lambda request: httpx.Response(500), _raise_timeout, lambda request: httpx.Response(429)):
        geoapify_adapter.request_breaker.reset()
        adapter = _places_adapter(
            monkeypatch, _Network(handler), geoapify, ProviderCacheStore(tmp_path / f"c{len(messages)}.sqlite3")
        )
        response = adapter.search_attractions("Lisbon, Portugal")
        assert response.status == ProviderStatus.FAILED
        messages.append(response.message or "")
        assert adapter.resolve_coordinates("Lisbon, Portugal") is None

    app_log = "\n".join(record.getMessage() for record in caplog.records if record.name.startswith("app"))
    assert "Place geocoding failed (provider=geoapify, kind=" in app_log
    for text in (app_log, *messages):
        assert _KEY not in text and "apiKey" not in text and "api.geoapify.com" not in text


def test_the_configured_geoapify_key_is_redacted_from_any_log_line(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.core.config as config_module

    monkeypatch.setattr(config_module, "get_settings", lambda: _settings(GEOAPIFY_API_KEY=_KEY))
    line = f'HTTP Request: GET https://api.geoapify.com/v1/geocode/search?text=x&apiKey={_KEY} "HTTP/1.1 200 OK"'
    assert _KEY not in redact(line)
    assert _KEY not in repr(_settings(GEOAPIFY_API_KEY=_KEY))


# -- factory / configuration --------------------------------------------------------------------


def test_factory_selects_the_configured_geocoder(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings().geocoding_provider == "nominatim"  # development/test default
    assert isinstance(get_geocoding_provider("nominatim"), NominatimGeocoder)
    assert isinstance(get_geocoding_provider("geoapify"), GeoapifyGeocoder)

    import app.providers.geocoding.factory as factory_module

    monkeypatch.setattr(factory_module, "get_settings", lambda: _settings(GEOCODING_PROVIDER=" Geoapify "))
    assert isinstance(get_geocoding_provider(), GeoapifyGeocoder)
    with pytest.raises(ValueError):
        _settings(GEOCODING_PROVIDER="google")


_PRODUCTION = {
    "APP_ENV": "production",
    "APP_DEBUG": "false",
    "PERSISTENCE_BACKEND": "postgres",
    "DATABASE_URL": "postgresql://owner:SENTINEL_203C1_DB_PASSWORD@db.example.test/app?sslmode=require",
    "PROVIDER_CACHE_BACKEND": "redis",
    "REDIS_URL": "rediss://default:SENTINEL_203C1_REDIS_PASSWORD@cache.example.test:6379",
    "SESSION_SECRET_KEY": "SENTINEL_203C1_SESSION_SECRET_" + "x" * 24,
    "SESSION_COOKIE_SECURE": "true",
    "BACKEND_CORS_ORIGINS": "https://frontend.example.test",
    # Section 203C.2B: a complete production configuration also selects Geoapify Places
    # and keeps the inventory sufficiency gate on.
    "PLACES_PROVIDER": "geoapify",
    "INVENTORY_SUFFICIENCY_GATE_ENABLED": "true",
}


def _production_rejection(**overrides: Any) -> str:
    with pytest.raises(OperationalConfigurationError) as excinfo:
        validate_runtime_configuration(_settings(**{**_PRODUCTION, **overrides}))
    assert _KEY not in str(excinfo.value)
    return str(excinfo.value)


def test_production_requires_a_geoapify_key_when_geoapify_is_selected() -> None:
    validate_runtime_configuration(_settings(**_PRODUCTION, GEOCODING_PROVIDER="geoapify", GEOAPIFY_API_KEY=_KEY))
    assert "GEOAPIFY_API_KEY" in _production_rejection(GEOCODING_PROVIDER="geoapify")
    assert "GEOAPIFY_API_KEY" in _production_rejection(GEOCODING_PROVIDER="geoapify", GEOAPIFY_API_KEY="  ")


def test_production_rejects_public_nominatim_unless_explicitly_allowed() -> None:
    assert "public Nominatim" in _production_rejection()  # the default geocoder
    assert "public Nominatim" in _production_rejection(GEOCODING_PROVIDER="nominatim", GEOAPIFY_API_KEY=_KEY)
    validate_runtime_configuration(
        _settings(**_PRODUCTION, GEOAPIFY_API_KEY=_KEY, ALLOW_PUBLIC_NOMINATIM_IN_PRODUCTION="true")
    )
    validate_runtime_configuration(
        _settings(**_PRODUCTION, GEOAPIFY_API_KEY=_KEY, NOMINATIM_API_URL="https://nominatim.internal.example.test")
    )


def test_development_keeps_nominatim_and_geoapify_without_a_key_usable() -> None:
    validate_runtime_configuration(_settings(PERSISTENCE_BACKEND="local_json"))
    validate_runtime_configuration(_settings(PERSISTENCE_BACKEND="local_json", GEOCODING_PROVIDER="geoapify"))
    summary = startup_config_summary(_settings(GEOCODING_PROVIDER="geoapify", GEOAPIFY_API_KEY=_KEY))
    assert summary["geocoding_provider"] == "geoapify"
    assert _KEY not in repr(summary)


# -- cache --------------------------------------------------------------------------------------


def test_geocode_cache_miss_calls_provider_and_sets_then_hit_avoids_provider(
    monkeypatch: pytest.MonkeyPatch, geoapify: GeoapifyGeocoder, tmp_path: Path
) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    network = _Network(_by_text)

    first = _places_adapter(monkeypatch, network, geoapify, store).search_must_visit_place(
        "Torre de Belém", "Lisbon, Portugal"
    )
    assert first.status == ProviderStatus.SUCCESS and first.data[0].data_status == DataStatus.LIVE
    assert len(network.geocode_requests) == 2  # destination + named place

    # a NEW adapter (new process): both lookups are served from the geoapify cache namespace
    second = _places_adapter(monkeypatch, network, geoapify, store).search_must_visit_place(
        "Torre de Belém", "Lisbon, Portugal"
    )
    assert len(network.geocode_requests) == 2
    assert second.data[0].data_status == DataStatus.CACHED
    assert (second.data[0].place_id, second.data[0].source) == ("geoapify/51bb", "geoapify")
    assert second.data[0].coordinates == first.data[0].coordinates

    destination_hash = openstreetmap_adapter.make_query_hash(geoapify.destination_cache_query("Lisbon, Portugal"))
    entry = store.get("geoapify_geocode", destination_hash)
    assert entry is not None
    assert store.get("openstreetmap_geocode", destination_hash) is None  # distinct provider namespace
    ttl = (entry.expires_at - entry.fetched_at).total_seconds()
    assert ttl == pytest.approx(_settings().osm_geocode_cache_ttl_seconds, abs=5)


def test_failures_and_no_match_are_never_cached(
    monkeypatch: pytest.MonkeyPatch, geoapify: GeoapifyGeocoder, tmp_path: Path
) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    for handler in (
        lambda request: httpx.Response(429),
        lambda request: httpx.Response(401),
        lambda request: httpx.Response(500),
        lambda request: httpx.Response(200, text="not json"),
        _results(),
    ):
        geoapify_adapter.request_breaker.reset()
        adapter = _places_adapter(monkeypatch, _Network(handler), geoapify, store)
        assert adapter.resolve_coordinates("Lisbon, Portugal") is None

    # nothing was stored: the next (healthy) call still has to ask the provider
    network = _Network(_by_text)
    geoapify_adapter.request_breaker.reset()
    assert _places_adapter(monkeypatch, network, geoapify, store).resolve_coordinates("Lisbon, Portugal") is not None
    assert len(network.geocode_requests) == 1


def test_redis_outage_degrades_to_an_uncached_geocoder_call(
    monkeypatch: pytest.MonkeyPatch, geoapify: GeoapifyGeocoder
) -> None:
    fake = FakeRedis()
    fake.fail_get = redis.ConnectionError("down")
    fake.fail_set = redis.ConnectionError("down")
    store = RedisProviderCacheStore(fake, key_prefix="tobl", clock=lambda: fake.now)
    network = _Network(_by_text)

    for _ in range(2):
        response = _places_adapter(monkeypatch, network, geoapify, store).search_must_visit_place(
            "Torre de Belém", "Lisbon, Portugal"
        )
        assert response.status == ProviderStatus.SUCCESS
        assert response.data[0].data_status == DataStatus.LIVE
    assert len(network.geocode_requests) == 4  # every call went to the provider, uncached
    assert fake.data == {}


# -- Nominatim safety ---------------------------------------------------------------------------


def _nominatim_result(name: str = "Lisbon") -> dict[str, Any]:
    return {
        "lat": "38.7077507",
        "lon": "-9.1365919",
        "name": name,
        "display_name": f"{name}, Portugal",
        "category": "boundary",
        "type": "administrative",
        "osm_type": "relation",
        "osm_id": 5400890,
        "address": {"city": name, "country": "Portugal"},
        "boundingbox": ["38.6913", "38.7967", "-9.2298", "-9.0863"],
    }


def test_nominatim_requests_carry_an_identifying_user_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    network = _Network(lambda request: httpx.Response(200, json=[_nominatim_result()]))
    adapter = _places_adapter(monkeypatch, network, NominatimGeocoder(), ProviderCacheStore(tmp_path / "c.sqlite3"))
    assert adapter.resolve_coordinates("Lisbon, Portugal") is not None
    assert network.geocode_requests[0].headers["User-Agent"].startswith("TravelObligator/")

    monkeypatch.setattr(
        destination_resolution, "get_settings", lambda: _settings(OSM_USER_AGENT_CONTACT="ops@example.test")
    )
    adapter = _places_adapter(monkeypatch, network, NominatimGeocoder(), ProviderCacheStore(tmp_path / "d.sqlite3"))
    assert adapter.resolve_coordinates("Lisbon, Portugal") is not None
    assert network.geocode_requests[1].headers["User-Agent"] == "TravelObligator/0.1 (ops@example.test)"


def test_nominatim_requests_are_paced_to_one_per_second(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 100.0}
    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(nominatim_adapter.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(nominatim_adapter.time, "sleep", _sleep)
    monkeypatch.setattr(nominatim_adapter.request_guard, "min_interval_seconds", 1.0)

    network = _Network(lambda request: httpx.Response(200, json=[_nominatim_result()]))
    geocoder = NominatimGeocoder()
    for _ in range(3):
        clock["now"] += 0.25  # the caller comes back sooner than the policy allows
        assert geocoder.search_destination(network.client(), "Lisbon, Portugal") is not None

    assert sleeps == [pytest.approx(0.75), pytest.approx(0.75)]  # never before the first request
    assert len(network.geocode_requests) == 3


def test_first_nominatim_429_stops_the_rest_of_the_search_burst(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["q"] == "Lisbon, Portugal":
            return httpx.Response(200, json=[_nominatim_result()])
        return httpx.Response(429, headers={"Retry-After": "120"})

    network = _Network(_handler)
    adapter = _places_adapter(monkeypatch, network, NominatimGeocoder(), ProviderCacheStore(tmp_path / "c.sqlite3"))

    responses = [
        adapter.search_must_visit_place(term, "Lisbon, Portugal")
        for term in ("Torre de Belém", "Mosteiro dos Jerónimos", "Castelo de São Jorge", "Pastéis de Belém")
    ]

    assert len(network.geocode_requests) == 2  # the destination, then ONE named place -> 429 -> no more
    for response in responses:
        assert response.status == ProviderStatus.FAILED
        assert response.failure_reason == "geocoder_rate_limited"
        assert response.data is None
        assert response.message == "Place geocoding provider (Nominatim) was unavailable."
    # ... and a brand-new destination lookup is refused locally too, for the cooldown
    assert adapter.resolve_coordinates("Porto, Portugal") is None
    assert len(network.geocode_requests) == 2


# -- integration --------------------------------------------------------------------------------


class _RecordingWeather(WeatherProvider):
    def __init__(self) -> None:
        self.coordinates: list[Any] = []

    def get_weather_forecast(self, destination, dates, coordinates=None):
        self.coordinates.append(coordinates)
        return self.not_connected(unavailable_fields=["weather_forecast"])


def _lisbon_state() -> PlanningState:
    return PlanningState(
        trip_request=TripRequest(
            primary_destination="Lisbon, Portugal",
            start_date="2026-11-10",
            end_date="2026-11-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        )
    )


def test_geocoder_success_enables_the_overpass_stage(
    monkeypatch: pytest.MonkeyPatch, geoapify: GeoapifyGeocoder, tmp_path: Path
) -> None:
    network = _Network(_by_text)
    weather = _RecordingWeather()
    adapter = _places_adapter(monkeypatch, network, geoapify, ProviderCacheStore(tmp_path / "c.sqlite3"))
    state = DestinationContextService(gateway=ProviderGateway(places=adapter, weather=weather, holiday=HolidayProvider(), currency=CurrencyProvider())).run(_lisbon_state())

    assert network.overpass_requests >= 3  # attractions, restaurants, accommodation POIs
    assert len(network.geocode_requests) == 1  # one destination geocode shared by every search
    context = state.destination_context
    assert {poi["name"] for poi in context.candidate_pois} == {"Castelo de São Jorge", "Museu do Fado", "Sé de Lisboa"}
    # Overpass POIs keep their OpenStreetMap identity; only the destination came from Geoapify
    assert all(poi["source"] == "openstreetmap_places" and poi["place_id"].startswith("node/") for poi in context.candidate_pois)
    assert context.resolved_destination["display_name"] == "Lisbon, Portugal"
    assert weather.coordinates[0] is not None and weather.coordinates[0].lat == 38.7077507


def test_geocoder_failure_yields_no_poi_or_weather_data_and_an_honest_geocoder_reason(
    monkeypatch: pytest.MonkeyPatch, geoapify: GeoapifyGeocoder, tmp_path: Path
) -> None:
    network = _Network(lambda request: httpx.Response(429))
    weather = _RecordingWeather()
    adapter = _places_adapter(monkeypatch, network, geoapify, ProviderCacheStore(tmp_path / "c.sqlite3"))
    state = DestinationContextService(gateway=ProviderGateway(places=adapter, weather=weather, holiday=HolidayProvider(), currency=CurrencyProvider())).run(_lisbon_state())

    assert network.overpass_requests == 0
    assert len(network.geocode_requests) == 1  # the 429 stopped every later lookup
    context = state.destination_context
    assert context.candidate_pois == [] and context.candidate_restaurants == []
    assert context.candidate_accommodation_pois == []
    assert context.resolved_destination is None
    assert context.destination_resolution is None  # a provider outage is not "destination unresolved"
    assert weather.coordinates == [None]  # no invented coordinates reach the weather provider
    assert state.provider_coverage.places == "failed"

    places_entries = [
        entry for entry in state.provider_status.values() if entry.provider_name == "openstreetmap_places"
    ]
    assert places_entries
    for entry in places_entries:
        assert entry.error_message == "Place geocoding provider (Geoapify) was unavailable."
        assert "Overpass" not in entry.error_message


def test_overpass_failure_is_still_reported_as_an_openstreetmap_poi_failure(
    monkeypatch: pytest.MonkeyPatch, geoapify: GeoapifyGeocoder, tmp_path: Path
) -> None:
    class _OverpassDown(_Network):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if "overpass" in request.url.host:
                return httpx.Response(504)
            return super().__call__(request)

    adapter = _places_adapter(monkeypatch, _OverpassDown(_by_text), geoapify, ProviderCacheStore(tmp_path / "c.sqlite3"))
    response = adapter.search_attractions("Lisbon, Portugal")
    assert response.status == ProviderStatus.FAILED
    assert response.message.startswith("OpenStreetMap/Overpass request failed")
    assert response.failure_reason is None


def _proposal(proposal_id: str, name: str) -> AICandidateProposal:
    return AICandidateProposal(
        proposal_id=proposal_id,
        candidate_name=name,
        candidate_type=AICandidateType.ATTRACTION,
        why_consider="A landmark worth checking.",
        verification_requirements=[AICandidateVerificationRequirement.MUST_GROUND_BY_NAME_AND_LOCATION],
        confidence=0.8,
    )


def test_ai_proposal_grounding_uses_the_configured_geocoder_and_promotes_nothing_unmatched(
    monkeypatch: pytest.MonkeyPatch, geoapify: GeoapifyGeocoder, tmp_path: Path
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        text = request.url.params["text"]
        if text.startswith("Fabricated Sky Palace"):
            # Geoapify's fallback for an unknown name: the surrounding city
            return httpx.Response(
                200, json={"results": [{**_LISBON, "rank": {"confidence": 0.3, "match_type": "match_by_city_or_district"}}]}
            )
        if text.startswith("Eiffel Tower"):
            return httpx.Response(
                200, json={"results": [{**_BELEM, "name": "Eiffel Tower", "lat": 48.8584, "lon": 2.2945, "place_id": "51cc"}]}
            )
        return _by_text(request)

    network = _Network(_handler)
    adapter = _places_adapter(monkeypatch, network, geoapify, ProviderCacheStore(tmp_path / "c.sqlite3"))
    result = AIDirectedProviderDiscoveryService(gateway=ProviderGateway(places=adapter)).discover(
        _lisbon_state(),
        [
            _proposal("p1", "Torre de Belém"),
            _proposal("p2", "Fabricated Sky Palace"),
            _proposal("p3", "Eiffel Tower"),
        ],
        [],
    )

    by_id = {attempt.proposal_id: attempt for attempt in result.attempts}
    matched = by_id["p1"]
    assert matched.status == AIProviderDiscoveryAttemptStatus.MATCHED
    # identity and coordinates are the geocoder's, never the proposal's
    assert (matched.match.provider_name, matched.match.provider_place_id) == ("geoapify", "geoapify/51bb")
    assert (matched.match.coordinates.lat, matched.match.coordinates.lng) == (38.6916, -9.2159)
    assert matched.match.name == "Torre de Belém"
    for proposal_id in ("p2", "p3"):  # city-level fallback; real place outside the destination
        assert by_id[proposal_id].status == AIProviderDiscoveryAttemptStatus.NOT_FOUND
        assert by_id[proposal_id].match is None
    assert result.matched_count == 1
    assert all("api.geoapify.com" in request.url.host for request in network.geocode_requests)


def test_ai_proposal_grounding_stops_calling_the_geocoder_after_a_429(
    monkeypatch: pytest.MonkeyPatch, geoapify: GeoapifyGeocoder, tmp_path: Path
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["text"] == "Lisbon, Portugal":
            return httpx.Response(200, json={"results": [_LISBON]})
        return httpx.Response(429)

    network = _Network(_handler)
    adapter = _places_adapter(monkeypatch, network, geoapify, ProviderCacheStore(tmp_path / "c.sqlite3"))
    result = AIDirectedProviderDiscoveryService(gateway=ProviderGateway(places=adapter)).discover(
        _lisbon_state(), [_proposal(f"p{i}", f"Landmark {i}") for i in range(5)], []
    )

    assert len(network.geocode_requests) == 2  # destination + the single lookup that got the 429
    assert {attempt.status for attempt in result.attempts} == {AIProviderDiscoveryAttemptStatus.PROVIDER_FAILED}
    assert result.matched_count == 0 and all(attempt.match is None for attempt in result.attempts)
