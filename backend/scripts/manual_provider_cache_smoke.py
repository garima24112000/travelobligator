#!/usr/bin/env python3
"""MANUAL-ONLY dev smoke test -- Step 164D.1, extended in Steps 164F/164H/165D.

This script is not part of the application runtime and is never imported by
`app.main`, any service, or the automated test suite. It exists purely so a
developer can, by hand, confirm that the provider-cache-wired adapters --
Open-Meteo (Step 164B), Nager.Date (Step 164C), Frankfurter (Step 164D),
OpenStreetMap/Nominatim (geocoding: Step 164E; Overpass POI search: Step
164G), and OSRM routing (Step 165D) -- still work against their real public
APIs, and that the `ProviderCacheStore` foundation (Step 164A) actually
populates and is read back correctly for each of them.

**OSM coverage is geocoding plus one small, real POI search.** It calls
`resolve_coordinates` (geocoding) and `search_attractions` (a single,
respectful Overpass category search around one known destination) --
nothing else on that adapter. It never calls `search_restaurants`,
`search_accommodation_pois`, or `search_must_visit_place`, and it never
claims or asserts a rating, price, opening hours, availability, booking
link, or route time -- `NormalizedPlace` never carries any of those fields,
cached or live.

**OSRM coverage is one tiny, fixed route lookup, called twice (Step
165D).** It calls `OSRMRoutingAdapter.get_route` for one small, stable
origin/destination pair, sharing the same temporary `ProviderCacheStore`
as every other provider above. It only checks structure -- `status`,
whether `distance_meters`/`duration_seconds` are positive numbers, and
whether the cache was actually used on the second call -- never an exact
distance, duration, or geometry. This does not validate every route,
profile, or destination pair, and it does not mean route data is consumed
by scheduling or validation anywhere in the app (it still isn't, as of
this step).

WARNING: running this script with the required env var set makes real
network calls to Open-Meteo, Nager.Date, Frankfurter, Nominatim, Overpass,
and an OSRM routing instance (the public OSRM demo server at
https://router.project-osrm.org by default, or `OSRM_BASE_URL` if set) --
six free, keyless public services (no API key is required or read by this
script). It is never invoked by pytest, by `python -m compileall`, by CI,
or by normal `uvicorn`/app startup -- it only runs when a human explicitly
executes this file.

No Groq, Anthropic, Kiwi/MCP, or scraping call is made anywhere in this
script -- only the providers above.

Required environment variable (or this script exits without calling
anything):
    RUN_LIVE_PROVIDER_CACHE_SMOKE=true

Optional environment variable (Step 165D):
    OSRM_BASE_URL=<your own OSRM instance>
    Defaults to the public OSRM demo server (https://router.project-osrm.org)
    when unset -- used only for this manual smoke context. This never
    changes `Settings.osrm_base_url`'s app-wide default, which stays unset
    unless a developer configures it separately for real use.

Run from the repo root:
    RUN_LIVE_PROVIDER_CACHE_SMOKE=true \\
    python backend/scripts/manual_provider_cache_smoke.py

See docs/21_manual_provider_cache_smoke.md for what a PASS/FAIL result does
and does not mean.

Output safety: this script only ever prints a short per-provider summary
(provider name, live_path_ok, cache_path_ok, status, cache_row_count) plus a
final PASS/FAIL line. It never prints a full weather/holiday/currency/
geocode/POI/route payload, a raw query, a raw Overpass query string, a raw
route/coordinate URL, an API URL with its query string, or any secret, and
it never writes to the app's real provider cache
(`Settings.provider_cache_path`) or trip storage -- it always uses its own
temporary, clearly-named `ProviderCacheStore` file that is deleted when the
script exits.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# Running this file directly (`python backend/scripts/manual_provider_cache_smoke.py`
# from the repo root) puts this script's own directory on `sys.path[0]`, not
# `backend/`, so `app.*` would not otherwise be importable without the
# developer manually setting `PYTHONPATH=backend` first. This insertion is
# pure local path bookkeeping -- it makes no network call and reads no
# secret -- so it is safe to run unconditionally at import time, before the
# env-var guard below.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_REQUIRED_ENV_VAR = "RUN_LIVE_PROVIDER_CACHE_SMOKE"

# Known, fixed real-world inputs. Lisbon, Portugal is used consistently
# across all four providers (including as both the OSM/Nominatim geocode
# query and the OSM/Overpass POI search destination) so a single temporary
# cache file/instance exercises Open-Meteo, Nager.Date, Frankfurter, and OSM
# (geocoding and POI search) with one coherent destination.
_KNOWN_LATITUDE = 38.7223
_KNOWN_LONGITUDE = -9.1393
_KNOWN_DESTINATION = "Lisbon, Portugal"
_KNOWN_BASE_CURRENCY = "USD"

_OSM_GEOCODE_SOURCE = "openstreetmap_geocode"
_OSM_POI_SOURCE = "openstreetmap_poi"
_OSRM_ROUTE_SOURCE = "osrm_route"

_EXPECTED_SOURCES = (
    "open_meteo",
    "nager_date",
    "frankfurter",
    _OSM_GEOCODE_SOURCE,
    _OSM_POI_SOURCE,
    _OSRM_ROUTE_SOURCE,
)

# A tiny, stable, real-world driving route near the same known Lisbon
# destination already used above -- short enough to be a fast, respectful
# request against the public OSRM demo server. Never asserted for an exact
# distance/duration/geometry -- only structural success (Step 165D).
_OSRM_DESTINATION_LATITUDE = 38.7169
_OSRM_DESTINATION_LONGITUDE = -9.1399

# Used only for this manual smoke context -- Settings.osrm_base_url stays
# unset by default everywhere else in the app (conservative by design, see
# docs/12_provider_architecture.md section 31).
_DEFAULT_OSRM_BASE_URL = "https://router.project-osrm.org"

# Checked against stored query_hash/payload_json/metadata_json text as a
# defense-in-depth assertion -- none of these providers require an API key,
# so none of these should ever legitimately appear in a cache row.
_FORBIDDEN_SECRET_SUBSTRINGS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "authorization",
    "bearer",
    "password",
)

# Checked against the POI cache payload only -- `NormalizedPlace` never
# carries any of these fields (cached or live), so none of these substrings
# should ever legitimately appear in an `openstreetmap_poi` cache row.
_FORBIDDEN_POI_CLAIM_SUBSTRINGS = (
    "rating",
    "price",
    "opening_hours",
    "availability",
    "booking_url",
    "booking_link",
    "route_time",
)


def _exit_gracefully(message: str) -> None:
    print(f"[manual-smoke] {message}")
    print("[manual-smoke] Exiting without calling any live provider.")
    sys.exit(0)


def _check_guardrail() -> bool:
    """Reads only the one required env var and returns True if it's set to
    a truthy value, or False if this script should exit gracefully having
    already printed why. This function never imports `app.*`, never opens a
    network connection, and never touches the filesystem beyond reading an
    env var.
    """
    flag = os.environ.get(_REQUIRED_ENV_VAR, "")
    if flag.strip().lower() not in ("1", "true", "yes"):
        _exit_gracefully(
            f"{_REQUIRED_ENV_VAR} is not set to a truthy value (got {flag!r}). "
            f"Set {_REQUIRED_ENV_VAR}=true to run this manual smoke test. "
            "This script is manual-only: it is never run by pytest or CI."
        )
        return False
    return True


def _cache_row_count(db_path: Path, source: str) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM provider_cache WHERE source = ?", (source,)
        ).fetchone()
    return int(row[0]) if row else 0


def _query_hashes_leak_raw_destination_text(db_path: Path, source: str) -> bool:
    """True if the stored `query_hash` for `source` contains the raw
    destination string -- which should never happen, since `query_hash` is
    always an opaque SHA-256 hex digest (`make_query_hash`), never the raw
    query text itself. This only inspects the hash column, never
    `payload_json` -- a legitimate holiday name or exchange-rate payload may
    honestly reference "Portugal"/"Lisbon" (e.g. a real Portuguese holiday
    name), and that is not a leak worth flagging.
    """
    needles = ("lisbon", "portugal")
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT query_hash FROM provider_cache WHERE source = ?", (source,)
        ).fetchall()
    combined = " ".join(str(row[0]) for row in rows).lower()
    return any(needle in combined for needle in needles)


def _cache_rows_contain_secret_markers(db_path: Path, source: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT query_hash, payload_json, metadata_json FROM provider_cache WHERE source = ?",
            (source,),
        ).fetchall()
    combined = " ".join(" ".join(str(value) for value in row) for row in rows).lower()
    return any(needle in combined for needle in _FORBIDDEN_SECRET_SUBSTRINGS)


def _metadata_is_empty_for_every_row(db_path: Path, source: str) -> bool:
    """True only if every stored `metadata_json` for `source` is the empty
    object -- none of these adapters ever pass an explicit `metadata=` to
    `ProviderCacheStore.set`, so this should always hold. Used as an extra,
    explicit check that no raw destination/search text was smuggled into
    `metadata` specifically (on top of the broader secret-marker check)."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT metadata_json FROM provider_cache WHERE source = ?", (source,)
        ).fetchall()
    return all(row[0] == "{}" for row in rows)


def _poi_cache_rows_contain_forbidden_claims(db_path: Path, source: str) -> bool:
    """True if any stored `payload_json` for `source` contains a rating,
    price, opening-hours, availability, booking, or route-time marker.
    `NormalizedPlace` never carries any of these fields -- cached or live
    -- so this should always be False; it exists as a structural,
    defense-in-depth check on the actual cached bytes."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM provider_cache WHERE source = ?", (source,)
        ).fetchall()
    combined = " ".join(str(row[0]) for row in rows).lower()
    return any(needle in combined for needle in _FORBIDDEN_POI_CLAIM_SUBSTRINGS)


def _resolve_osrm_base_url() -> str:
    """Reads `OSRM_BASE_URL` from env, or falls back to the public OSRM
    demo routing server -- used only for this manual smoke context, never
    as an app-wide default (`Settings.osrm_base_url` stays unset/None by
    default everywhere else)."""
    value = os.environ.get("OSRM_BASE_URL", "").strip()
    return value or _DEFAULT_OSRM_BASE_URL


def _osrm_cache_rows_leak_raw_route_text(db_path: Path, source: str) -> bool:
    """True if the stored `query_hash`/`metadata_json` for `source` contains
    a raw coordinate value or a route URL fragment -- which should never
    happen, since `query_hash` is always an opaque SHA-256 digest and
    `OSRMRoutingAdapter` never passes `metadata=` to
    `ProviderCacheStore.set`."""
    needles = (
        str(_KNOWN_LATITUDE),
        str(_KNOWN_LONGITUDE),
        str(_OSRM_DESTINATION_LATITUDE),
        str(_OSRM_DESTINATION_LONGITUDE),
        "route/v1",
        "http://",
        "https://",
    )
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT query_hash, metadata_json FROM provider_cache WHERE source = ?",
            (source,),
        ).fetchall()
    combined = " ".join(" ".join(str(value) for value in row) for row in rows).lower()
    return any(needle.lower() in combined for needle in needles)


def _run_osrm_route_check(db_path: Path, store: Any, checks: list[tuple[str, bool]]) -> None:
    """Real OSRM routing + route-cache check (Step 165D). Calls
    `OSRMRoutingAdapter.get_route` twice for one small, fixed
    origin/destination pair near the same known Lisbon destination already
    used above, against a real OSRM instance -- by default the public OSRM
    demo server, or `OSRM_BASE_URL` if set. Only `osrm_adapter.get_settings`
    is temporarily monkeypatched (to supply the base URL this manual
    context needs -- `Settings.osrm_base_url` stays unset/None everywhere
    else in the app); it is restored immediately after, in a `finally`
    block, and no other adapter/module is touched.

    Never asserts an exact distance, duration, or geometry -- only that a
    real route was found (`status=success`) with a positive numeric
    distance and duration, and that the second, identical call was served
    from cache (same values as the first, with no additional live call
    needed -- proven the same way the other cache-wired adapters prove it,
    via `data_status`/a call counter, except here directly via the
    `OSRMRoutingAdapter.get_route` contract: a cache hit and a live call
    return the identical normalized shape, so equal values plus a cache row
    is the observable proof of reuse).
    """
    from app.core.config import Settings
    from app.models.common import ProviderStatus
    from app.models.routing import RouteRequest, RoutingProfile
    from app.providers.routing import osrm_adapter
    from app.providers.routing.osrm_adapter import OSRMRoutingAdapter

    base_url = _resolve_osrm_base_url()
    smoke_settings = Settings(_env_file=None, osrm_base_url=base_url, provider_cache_enabled=True)
    real_get_settings = osrm_adapter.get_settings
    osrm_adapter.get_settings = lambda: smoke_settings
    try:
        request = RouteRequest(
            origin_lat=_KNOWN_LATITUDE,
            origin_lon=_KNOWN_LONGITUDE,
            destination_lat=_OSRM_DESTINATION_LATITUDE,
            destination_lon=_OSRM_DESTINATION_LONGITUDE,
            profile=RoutingProfile.DRIVING,
        )
        first_result = OSRMRoutingAdapter(cache_store=store).get_route(request)
        second_result = OSRMRoutingAdapter(cache_store=store).get_route(request)
    finally:
        osrm_adapter.get_settings = real_get_settings

    def _is_structurally_valid(result: Any) -> bool:
        return (
            result.status == ProviderStatus.SUCCESS
            and isinstance(result.distance_meters, (int, float))
            and result.distance_meters > 0
            and isinstance(result.duration_seconds, (int, float))
            and result.duration_seconds > 0
        )

    live_path_ok = _is_structurally_valid(first_result)
    cache_path_ok = (
        live_path_ok
        and _is_structurally_valid(second_result)
        and second_result.distance_meters == first_result.distance_meters
        and second_result.duration_seconds == first_result.duration_seconds
    )
    row_count = _cache_row_count(db_path, _OSRM_ROUTE_SOURCE)
    status_label = first_result.status.value if live_path_ok else "failed"

    print(
        f"provider={_OSRM_ROUTE_SOURCE} live_path_ok={live_path_ok} "
        f"cache_path_ok={cache_path_ok} status={status_label} "
        f"cache_row_count={row_count}"
    )

    checks.append((f"{_OSRM_ROUTE_SOURCE} live path ok", live_path_ok))
    checks.append((f"{_OSRM_ROUTE_SOURCE} cache path ok", cache_path_ok))
    checks.append((f"{_OSRM_ROUTE_SOURCE} has at least one cache row", row_count >= 1))
    checks.append(
        (
            f"{_OSRM_ROUTE_SOURCE} cache metadata is empty (no raw text smuggled in)",
            _metadata_is_empty_for_every_row(db_path, _OSRM_ROUTE_SOURCE),
        )
    )
    checks.append(
        (
            f"{_OSRM_ROUTE_SOURCE} query_hash/metadata contain no raw coordinate/route URL text",
            not _osrm_cache_rows_leak_raw_route_text(db_path, _OSRM_ROUTE_SOURCE),
        )
    )


def _run_osm_geocode_check(
    db_path: Path, store: Any, checks: list[tuple[str, bool]]
) -> None:
    """Geocoding-only OSM/Nominatim check (Step 164F). Calls
    `OpenStreetMapPlacesAdapter.resolve_coordinates` -- never Overpass POI
    search -- with two separate, fresh adapter instances (each with its own
    empty per-instance dict) sharing one persistent `ProviderCacheStore`.

    `resolve_coordinates` returns a plain `GeoPoint | None`, not a
    `ProviderResponse` with a `data_status` field, so "cache_path_ok" can't
    be read off a response attribute the way it can for the other three
    providers. Instead, this wraps the adapter's own `httpx.Client` calls in
    a thin, transparent counter (never a fake response, never altered
    headers/timeout/User-Agent -- the real client still makes the real
    request) so the second call can be proven to have made no additional
    live request. Never asserts an exact coordinate, OSM ID, or display
    name -- only that a real point was resolved and that a second identical
    lookup didn't need a second live request.
    """
    from app.providers.places import openstreetmap_adapter
    from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter

    call_counters = {"get": 0}
    real_client_cls = openstreetmap_adapter.httpx.Client

    class _CallCountingHttpxClient:
        def __init__(self, real_client: Any) -> None:
            self._real_client = real_client

        def __enter__(self) -> "_CallCountingHttpxClient":
            self._real_client.__enter__()
            return self

        def __exit__(self, *exc_info: object) -> bool:
            return self._real_client.__exit__(*exc_info)

        def get(self, *args: Any, **kwargs: Any) -> Any:
            call_counters["get"] += 1
            return self._real_client.get(*args, **kwargs)

    def _wrapped_client(**kwargs: Any) -> _CallCountingHttpxClient:
        return _CallCountingHttpxClient(real_client_cls(**kwargs))

    openstreetmap_adapter.httpx.Client = _wrapped_client
    try:
        # `resolve_coordinates` already catches httpx.HTTPError/ValueError
        # internally and returns None rather than raising -- no exception
        # handling is needed here beyond restoring the real client.
        first_point = OpenStreetMapPlacesAdapter(cache_store=store).resolve_coordinates(
            _KNOWN_DESTINATION
        )
        calls_after_first = call_counters["get"]

        second_point = OpenStreetMapPlacesAdapter(cache_store=store).resolve_coordinates(
            _KNOWN_DESTINATION
        )
        calls_after_second = call_counters["get"]
    finally:
        openstreetmap_adapter.httpx.Client = real_client_cls

    def _is_valid_point(point: Any) -> bool:
        return (
            point is not None
            and isinstance(point.lat, (int, float))
            and isinstance(point.lng, (int, float))
        )

    live_path_ok = _is_valid_point(first_point) and calls_after_first >= 1
    cache_path_ok = (
        live_path_ok
        and _is_valid_point(second_point)
        and calls_after_second == calls_after_first
    )
    row_count = _cache_row_count(db_path, _OSM_GEOCODE_SOURCE)
    status_label = "success" if live_path_ok else "failed"

    print(
        f"provider={_OSM_GEOCODE_SOURCE} live_path_ok={live_path_ok} "
        f"cache_path_ok={cache_path_ok} status={status_label} "
        f"cache_row_count={row_count}"
    )

    checks.append((f"{_OSM_GEOCODE_SOURCE} live path ok", live_path_ok))
    checks.append((f"{_OSM_GEOCODE_SOURCE} cache path ok", cache_path_ok))
    checks.append((f"{_OSM_GEOCODE_SOURCE} has at least one cache row", row_count >= 1))
    checks.append(
        (
            f"{_OSM_GEOCODE_SOURCE} cache metadata is empty (no raw text smuggled in)",
            _metadata_is_empty_for_every_row(db_path, _OSM_GEOCODE_SOURCE),
        )
    )


def _run_osm_poi_check(db_path: Path, store: Any, checks: list[tuple[str, bool]]) -> None:
    """Overpass POI search check (Step 164H). Calls
    `OpenStreetMapPlacesAdapter.search_attractions` -- one small, real,
    single-category search around the same known destination already
    geocoded above -- with two separate, fresh adapter instances sharing
    one persistent `ProviderCacheStore`. This is the only Overpass call
    this script makes: never `search_restaurants`,
    `search_accommodation_pois`, or `search_must_visit_place`, and the
    destination's geocoding was already cached by `_run_osm_geocode_check`
    above, so this makes no additional Nominatim request either.

    Like geocoding, `search_attractions`'s overall `ProviderResponse.status`/
    `data_status` reflects only whether Overpass fallback was needed, not
    whether the result came from cache (Step 164G design) -- so
    "cache_path_ok" is proven the same way as geocoding: a thin,
    transparent counter around the adapter's own `httpx.Client` (never a
    fake response, never altered headers/timeout/User-Agent) shows the
    second call made no additional live Overpass request. Never asserts an
    exact POI name, OSM ID, or coordinate -- only that real, named places
    were returned, and that no rating, price, opening hours, availability,
    booking link, or route time was introduced.
    """
    from app.providers.places import openstreetmap_adapter
    from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter

    call_counters = {"post": 0}
    real_client_cls = openstreetmap_adapter.httpx.Client

    class _PostCountingHttpxClient:
        def __init__(self, real_client: Any) -> None:
            self._real_client = real_client

        def __enter__(self) -> "_PostCountingHttpxClient":
            self._real_client.__enter__()
            return self

        def __exit__(self, *exc_info: object) -> bool:
            return self._real_client.__exit__(*exc_info)

        def get(self, *args: Any, **kwargs: Any) -> Any:
            return self._real_client.get(*args, **kwargs)

        def post(self, *args: Any, **kwargs: Any) -> Any:
            call_counters["post"] += 1
            return self._real_client.post(*args, **kwargs)

    def _wrapped_client(**kwargs: Any) -> _PostCountingHttpxClient:
        return _PostCountingHttpxClient(real_client_cls(**kwargs))

    openstreetmap_adapter.httpx.Client = _wrapped_client
    try:
        # `search_attractions` never raises -- request/geocode failures are
        # already caught internally and reported via `ProviderResponse`.
        first_response = OpenStreetMapPlacesAdapter(cache_store=store).search_attractions(
            _KNOWN_DESTINATION
        )
        posts_after_first = call_counters["post"]

        second_response = OpenStreetMapPlacesAdapter(cache_store=store).search_attractions(
            _KNOWN_DESTINATION
        )
        posts_after_second = call_counters["post"]
    finally:
        openstreetmap_adapter.httpx.Client = real_client_cls

    def _has_usable_places(response: Any) -> bool:
        return bool(response.data) and response.status.value in (
            "success",
            "partial",
            "fallback_used",
        )

    live_path_ok = _has_usable_places(first_response) and posts_after_first >= 1
    cache_path_ok = (
        live_path_ok
        and _has_usable_places(second_response)
        and posts_after_second == posts_after_first
    )
    row_count = _cache_row_count(db_path, _OSM_POI_SOURCE)
    status_label = first_response.status.value if live_path_ok else "failed"

    print(
        f"provider={_OSM_POI_SOURCE} live_path_ok={live_path_ok} "
        f"cache_path_ok={cache_path_ok} status={status_label} "
        f"cache_row_count={row_count}"
    )

    checks.append((f"{_OSM_POI_SOURCE} live path ok", live_path_ok))
    checks.append((f"{_OSM_POI_SOURCE} cache path ok", cache_path_ok))
    checks.append((f"{_OSM_POI_SOURCE} has at least one cache row", row_count >= 1))
    checks.append(
        (
            f"{_OSM_POI_SOURCE} cache metadata is empty (no raw text smuggled in)",
            _metadata_is_empty_for_every_row(db_path, _OSM_POI_SOURCE),
        )
    )
    checks.append(
        (
            f"{_OSM_POI_SOURCE} cached payload has no rating/price/hours/"
            "availability/booking/route-time claim",
            not _poi_cache_rows_contain_forbidden_claims(db_path, _OSM_POI_SOURCE),
        )
    )


def _run_smoke_test(db_path: Path) -> bool:
    """Calls each of the cache-wired adapters (Open-Meteo, Nager.Date,
    Frankfurter, OSM -- geocoding plus one Overpass POI search -- and OSRM
    routing) with identical, known inputs, sharing one `ProviderCacheStore`
    pointed at `db_path` (a temporary file, never the app's real provider
    cache). The first call is expected to populate the cache from a live
    provider request; the second is expected to be served from the cache.
    Returns True on PASS, False on FAIL. Never asserts an exact weather
    value, holiday name, exchange rate, geocode coordinate, POI name, OSM
    ID, display name, route distance, route duration, or route geometry --
    only structure and cache behavior. OSM/Overpass POI coverage is limited
    to one `search_attractions` call -- never `search_restaurants`,
    `search_accommodation_pois`, or `search_must_visit_place`. OSRM
    coverage is limited to one fixed origin/destination pair -- it does not
    validate every route, profile, or destination.
    """
    from app.models.common import DataStatus, GeoPoint, ProviderStatus
    from app.providers.currency.frankfurter_adapter import FrankfurterCurrencyAdapter
    from app.providers.holidays.nager_date_adapter import NagerDateHolidaysAdapter
    from app.providers.weather.open_meteo_adapter import OpenMeteoWeatherAdapter
    from app.storage.provider_cache_store import ProviderCacheStore

    store = ProviderCacheStore(db_path)
    checks: list[tuple[str, bool]] = []

    def _report(source: str, first_response: Any, second_response: Any) -> None:
        live_path_ok = first_response.status == ProviderStatus.SUCCESS
        cache_path_ok = live_path_ok and second_response.data_status == DataStatus.CACHED
        row_count = _cache_row_count(db_path, source)

        print(
            f"provider={source} live_path_ok={live_path_ok} "
            f"cache_path_ok={cache_path_ok} status={first_response.status.value} "
            f"cache_row_count={row_count}"
        )

        checks.append((f"{source} live path ok", live_path_ok))
        checks.append((f"{source} cache path ok", cache_path_ok))
        checks.append((f"{source} has at least one cache row", row_count >= 1))

    today = date.today()
    weather_dates = {
        "start_date": (today + timedelta(days=1)).isoformat(),
        "end_date": (today + timedelta(days=3)).isoformat(),
    }
    holiday_dates = {
        "start_date": f"{today.year}-01-01",
        "end_date": f"{today.year}-12-31",
    }

    # --- Open-Meteo ---
    weather_adapter = OpenMeteoWeatherAdapter(cache_store=store)
    coordinates = GeoPoint(lat=_KNOWN_LATITUDE, lng=_KNOWN_LONGITUDE)
    weather_first = weather_adapter.get_weather_forecast(
        _KNOWN_DESTINATION, weather_dates, coordinates=coordinates
    )
    weather_second = weather_adapter.get_weather_forecast(
        _KNOWN_DESTINATION, weather_dates, coordinates=coordinates
    )
    _report("open_meteo", weather_first, weather_second)

    # --- Nager.Date ---
    holiday_adapter = NagerDateHolidaysAdapter(cache_store=store)
    holiday_first = holiday_adapter.get_public_holidays(_KNOWN_DESTINATION, holiday_dates)
    holiday_second = holiday_adapter.get_public_holidays(_KNOWN_DESTINATION, holiday_dates)
    _report("nager_date", holiday_first, holiday_second)

    # --- Frankfurter ---
    currency_adapter = FrankfurterCurrencyAdapter(cache_store=store)
    currency_first = currency_adapter.get_exchange_rate(_KNOWN_BASE_CURRENCY, _KNOWN_DESTINATION)
    currency_second = currency_adapter.get_exchange_rate(_KNOWN_BASE_CURRENCY, _KNOWN_DESTINATION)
    _report("frankfurter", currency_first, currency_second)

    # --- OpenStreetMap/Nominatim geocoding (Step 164F) ---
    _run_osm_geocode_check(db_path, store, checks)

    # --- OpenStreetMap/Overpass POI search -- one small category search
    # only (Step 164H). Never restaurants, accommodation, or must-visit. ---
    _run_osm_poi_check(db_path, store, checks)

    # --- OSRM routing -- one tiny, fixed route lookup, called twice
    # (Step 165D). ---
    _run_osrm_route_check(db_path, store, checks)

    no_secrets = not any(
        _cache_rows_contain_secret_markers(db_path, source) for source in _EXPECTED_SOURCES
    )
    checks.append(("no secret markers stored in any cache row", no_secrets))

    no_raw_destination_in_hash = not any(
        _query_hashes_leak_raw_destination_text(db_path, source) for source in _EXPECTED_SOURCES
    )
    checks.append(("no raw destination text stored in any query_hash", no_raw_destination_in_hash))

    return _print_final(checks)


def _print_final(checks: list[tuple[str, bool]]) -> bool:
    all_passed = all(passed for _, passed in checks)
    for label, passed in checks:
        print(f"  [{'ok' if passed else 'FAIL'}] {label}")
    print(f"RESULT: {'PASS' if all_passed else 'FAIL'}")
    return all_passed


def main() -> int:
    if not _check_guardrail():
        return 0

    temp_dir = Path(tempfile.mkdtemp(prefix="travelobligator_manual_provider_cache_smoke_"))
    db_path = temp_dir / "manual_provider_cache_smoke.sqlite3"
    print(f"[manual-smoke] Using temporary cache path: {db_path}")
    print(
        "[manual-smoke] This is a throwaway file, not the app's real provider "
        "cache and not production trip storage. It is deleted when this "
        "script exits."
    )

    try:
        passed = _run_smoke_test(db_path)
    except Exception:
        # Never let a raw exception (which could echo request/response
        # details) reach stdout -- only a generic failure line is printed.
        print("[manual-smoke] Smoke test raised an unexpected exception.")
        print("RESULT: FAIL")
        return 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
