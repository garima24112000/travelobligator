from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.models.common import ProviderStatus
from app.models.routing import RouteRequest, RoutingProfile
from app.providers.routing import osrm_adapter
from app.providers.routing.osrm_adapter import OSRMRoutingAdapter
from app.storage.provider_cache_store import ProviderCacheStore, make_query_hash

_ROUTE_CACHE_SOURCE = "osrm_route"

# Safety/skeleton tests for the Step 165A OSRM routing adapter. Every test
# here uses an in-file fake HTTP client -- never a real network call to any
# OSRM instance (public demo or otherwise). No test prints a URL, and no
# test asserts on raw payload/query text.


def _request(**overrides: object) -> RouteRequest:
    fields: dict[str, object] = {
        "origin_lat": 38.7223,
        "origin_lon": -9.1393,
        "destination_lat": 38.7169,
        "destination_lon": -9.1399,
    }
    fields.update(overrides)
    return RouteRequest(**fields)


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
    """Stands in for `httpx.Client`."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.get_call_count = 0

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        self.get_call_count += 1
        return self._response


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> _FakeClient:
    fake_client = _FakeClient(response)
    monkeypatch.setattr(osrm_adapter.httpx, "Client", lambda **kwargs: fake_client)
    return fake_client


def _settings_with_base_url(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Settings:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    fields: dict[str, Any] = {"osrm_base_url": "https://osrm.example.test"}
    fields.update(overrides)
    return Settings(**fields)


def _osrm_success_payload(distance: float = 1234.5, duration: float = 321.0) -> dict[str, Any]:
    return {
        "code": "Ok",
        "routes": [
            {
                "distance": distance,
                "duration": duration,
                "legs": [],
            }
        ],
        "waypoints": [],
    }


def _default_query_hash(**overrides: Any) -> str:
    fields: dict[str, Any] = {
        "origin_lat": 38.7223,
        "origin_lon": -9.1393,
        "destination_lat": 38.7169,
        "destination_lon": -9.1399,
        "profile": "driving",
    }
    fields.update(overrides)
    return make_query_hash(fields)


# ---------------------------------------------------------------------------
# 2. OSRM adapter with missing config/base URL returns not_connected, and
#    never opens the HTTP client at all.
# ---------------------------------------------------------------------------


def test_missing_base_url_returns_not_connected_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disabled_settings = Settings(_env_file=None, osrm_base_url=None)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: disabled_settings)

    # No fake client is installed at all -- if the adapter tried to open
    # one, `osrm_adapter.httpx.Client` would still be the real class and
    # would attempt a real connection, failing this test loudly.
    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.NOT_CONNECTED
    assert result.distance_meters is None
    assert result.duration_seconds is None


# ---------------------------------------------------------------------------
# 3. OSRM adapter parses a fake successful route response into
#    distance_meters and duration_seconds.
# ---------------------------------------------------------------------------


def test_successful_response_parses_distance_and_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    fake_client = _install_fake_client(
        monkeypatch, _FakeResponse(json_data=_osrm_success_payload(1234.5, 321.0))
    )

    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert fake_client.get_call_count == 1
    assert result.status == ProviderStatus.SUCCESS
    assert result.distance_meters == pytest.approx(1234.5)
    assert result.duration_seconds == pytest.approx(321.0)
    assert result.provider == "osrm"
    assert result.source == "osrm"
    assert result.geometry is None


def test_successful_response_uses_first_route_only(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    payload = _osrm_success_payload(1000.0, 100.0)
    payload["routes"].append({"distance": 9999.0, "duration": 9999.0, "legs": []})
    _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert result.distance_meters == pytest.approx(1000.0)
    assert result.duration_seconds == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# 4. OSRM adapter handles NoRoute without fabricating duration/distance.
# ---------------------------------------------------------------------------


def test_no_route_code_returns_unavailable_without_fabricating_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    _install_fake_client(
        monkeypatch, _FakeResponse(json_data={"code": "NoRoute", "routes": []})
    )

    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.UNAVAILABLE
    assert result.distance_meters is None
    assert result.duration_seconds is None


def test_empty_routes_list_with_ok_code_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    _install_fake_client(monkeypatch, _FakeResponse(json_data={"code": "Ok", "routes": []}))

    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.UNAVAILABLE
    assert result.distance_meters is None
    assert result.duration_seconds is None


def test_route_with_no_usable_distance_or_duration_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    _install_fake_client(
        monkeypatch,
        _FakeResponse(
            json_data={"code": "Ok", "routes": [{"distance": None, "duration": None}]}
        ),
    )

    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.UNAVAILABLE
    assert result.distance_meters is None
    assert result.duration_seconds is None


# ---------------------------------------------------------------------------
# 5. OSRM adapter handles malformed response safely.
# ---------------------------------------------------------------------------


def test_malformed_top_level_response_returns_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    _install_fake_client(monkeypatch, _FakeResponse(json_data=["not", "a", "dict"]))

    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.UNAVAILABLE
    assert result.distance_meters is None
    assert result.duration_seconds is None


def test_malformed_route_entry_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    _install_fake_client(
        monkeypatch, _FakeResponse(json_data={"code": "Ok", "routes": ["not-a-dict"]})
    )

    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.UNAVAILABLE
    assert result.distance_meters is None


def test_missing_code_field_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    _install_fake_client(monkeypatch, _FakeResponse(json_data={}))

    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# 6. OSRM adapter handles HTTP exception safely.
# ---------------------------------------------------------------------------


def test_request_failure_returns_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    _install_fake_client(monkeypatch, _FakeResponse(should_fail=True))

    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.FAILED
    assert result.distance_meters is None
    assert result.duration_seconds is None


def test_invalid_json_response_returns_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)

    class _RaisingJsonResponse(_FakeResponse):
        def json(self) -> Any:
            raise ValueError("invalid json")

    _install_fake_client(monkeypatch, _RaisingJsonResponse())

    adapter = OSRMRoutingAdapter()
    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.FAILED


# ---------------------------------------------------------------------------
# Profile handling: request profile is honored, default falls back to
# Settings.osrm_profile.
# ---------------------------------------------------------------------------


def test_walking_profile_is_used_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    captured_urls: list[str] = []

    class _CapturingClient(_FakeClient):
        def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
            captured_urls.append(url)
            return super().get(url, params)

    fake_client = _CapturingClient(_FakeResponse(json_data=_osrm_success_payload()))
    monkeypatch.setattr(osrm_adapter.httpx, "Client", lambda **kwargs: fake_client)

    adapter = OSRMRoutingAdapter()
    adapter.get_route(_request(profile=RoutingProfile.WALKING))

    assert len(captured_urls) == 1
    assert "/walking/" in captured_urls[0]


# ---------------------------------------------------------------------------
# Route cache (Step 165C, docs/12_provider_architecture.md "Provider Cache
# Foundation" section). A `ProviderCacheStore` is injected directly via the
# `cache_store` constructor param in every test below -- never the real
# process-wide singleton -- so these tests are independent of the
# conftest.py `_isolate_provider_cache_store` fixture (which exists to
# protect *other* tests in this file/session, not these).
# ---------------------------------------------------------------------------


def test_cache_hit_returns_cached_result_without_http_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    cache_store.set(
        _ROUTE_CACHE_SOURCE,
        _default_query_hash(),
        {"distance_meters": 5000.0, "duration_seconds": 600.0, "geometry": None, "confidence": 0.6},
        ttl_seconds=86400,
    )
    adapter = OSRMRoutingAdapter(cache_store=cache_store)

    # No fake client is installed -- if the adapter fell through to HTTP,
    # `osrm_adapter.httpx.Client` would still be the real class and would
    # attempt a real connection, failing this test loudly.
    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.SUCCESS
    assert result.distance_meters == pytest.approx(5000.0)
    assert result.duration_seconds == pytest.approx(600.0)
    assert result.provider == "osrm"
    assert result.source == "osrm"


def test_cache_miss_calls_http_once_and_writes_normalized_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    fake_client = _install_fake_client(
        monkeypatch, _FakeResponse(json_data=_osrm_success_payload(1234.5, 321.0))
    )
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    adapter = OSRMRoutingAdapter(cache_store=cache_store)

    result = adapter.get_route(_request())

    assert fake_client.get_call_count == 1
    assert result.status == ProviderStatus.SUCCESS

    entry = cache_store.get(_ROUTE_CACHE_SOURCE, _default_query_hash())
    assert entry is not None
    assert entry.payload["distance_meters"] == pytest.approx(1234.5)
    assert entry.payload["duration_seconds"] == pytest.approx(321.0)


def test_second_identical_call_uses_cache_and_skips_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    fake_client = _install_fake_client(
        monkeypatch, _FakeResponse(json_data=_osrm_success_payload(1234.5, 321.0))
    )
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    adapter = OSRMRoutingAdapter(cache_store=cache_store)

    first = adapter.get_route(_request())
    second = adapter.get_route(_request())

    assert fake_client.get_call_count == 1
    assert second.status == ProviderStatus.SUCCESS
    assert second.distance_meters == pytest.approx(first.distance_meters)
    assert second.duration_seconds == pytest.approx(first.duration_seconds)


def test_expired_cache_entry_behaves_like_miss_and_refreshes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    stale_time = datetime.now(timezone.utc) - timedelta(days=2)
    cache_store.set(
        _ROUTE_CACHE_SOURCE,
        _default_query_hash(),
        {"distance_meters": 1.0, "duration_seconds": 1.0, "geometry": None, "confidence": 0.6},
        ttl_seconds=60,
        now=stale_time,
    )
    fake_client = _install_fake_client(
        monkeypatch, _FakeResponse(json_data=_osrm_success_payload(2222.0, 222.0))
    )
    adapter = OSRMRoutingAdapter(cache_store=cache_store)

    result = adapter.get_route(_request())

    assert fake_client.get_call_count == 1
    assert result.status == ProviderStatus.SUCCESS
    assert result.distance_meters == pytest.approx(2222.0)

    refreshed = cache_store.get(_ROUTE_CACHE_SOURCE, _default_query_hash())
    assert refreshed is not None
    assert refreshed.payload["distance_meters"] == pytest.approx(2222.0)


def test_cache_disabled_bypasses_cache_and_calls_http_every_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings_with_base_url(monkeypatch, provider_cache_enabled=False)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    fake_client = _install_fake_client(
        monkeypatch, _FakeResponse(json_data=_osrm_success_payload())
    )
    adapter = OSRMRoutingAdapter()

    adapter.get_route(_request())
    adapter.get_route(_request())

    assert fake_client.get_call_count == 2


def test_cache_read_failure_falls_back_to_http(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    fake_client = _install_fake_client(
        monkeypatch, _FakeResponse(json_data=_osrm_success_payload(500.0, 50.0))
    )

    class _RaisingCacheStore:
        def get(self, source: str, query_hash: str) -> Any:
            raise RuntimeError("simulated cache read failure")

        def set(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("simulated cache write failure")

    adapter = OSRMRoutingAdapter(cache_store=_RaisingCacheStore())

    result = adapter.get_route(_request())

    assert fake_client.get_call_count == 1
    assert result.status == ProviderStatus.SUCCESS
    assert result.distance_meters == pytest.approx(500.0)
    assert result.duration_seconds == pytest.approx(50.0)


def test_cache_write_failure_still_returns_live_result(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    fake_client = _install_fake_client(
        monkeypatch, _FakeResponse(json_data=_osrm_success_payload(700.0, 70.0))
    )

    class _WriteFailingCacheStore:
        def get(self, source: str, query_hash: str) -> None:
            return None

        def set(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("simulated cache write failure")

    adapter = OSRMRoutingAdapter(cache_store=_WriteFailingCacheStore())

    result = adapter.get_route(_request())

    assert fake_client.get_call_count == 1
    assert result.status == ProviderStatus.SUCCESS
    assert result.distance_meters == pytest.approx(700.0)
    assert result.duration_seconds == pytest.approx(70.0)


def test_cache_key_is_deterministic_for_equivalent_requests() -> None:
    fields = {
        "origin_lat": 38.7223,
        "origin_lon": -9.1393,
        "destination_lat": 38.7169,
        "destination_lon": -9.1399,
        "profile": "driving",
    }
    reordered = dict(reversed(list(fields.items())))

    assert make_query_hash(fields) == make_query_hash(reordered)


def test_different_route_inputs_produce_different_query_hashes() -> None:
    base_hash = _default_query_hash()

    assert _default_query_hash(origin_lat=39.0) != base_hash
    assert _default_query_hash(destination_lon=-9.5) != base_hash
    assert _default_query_hash(profile="walking") != base_hash


def test_no_secrets_in_cache_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    _install_fake_client(monkeypatch, _FakeResponse(json_data=_osrm_success_payload(900.0, 90.0)))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    adapter = OSRMRoutingAdapter(cache_store=cache_store)

    adapter.get_route(_request())

    entry = cache_store.get(_ROUTE_CACHE_SOURCE, _default_query_hash())
    assert entry is not None
    assert entry.metadata == {}
    assert set(entry.payload.keys()) == {
        "distance_meters",
        "duration_seconds",
        "geometry",
        "confidence",
    }
    combined = f"{entry.payload}{entry.metadata}".lower()
    for forbidden in ("api_key", "token", "secret", "authorization", "coordinates="):
        assert forbidden not in combined


def test_noroute_and_failed_results_are_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    _install_fake_client(monkeypatch, _FakeResponse(json_data={"code": "NoRoute", "routes": []}))
    adapter = OSRMRoutingAdapter(cache_store=cache_store)
    no_route_result = adapter.get_route(_request())
    assert no_route_result.status == ProviderStatus.UNAVAILABLE
    assert cache_store.get(_ROUTE_CACHE_SOURCE, _default_query_hash()) is None

    _install_fake_client(monkeypatch, _FakeResponse(should_fail=True))
    failed_result = adapter.get_route(_request())
    assert failed_result.status == ProviderStatus.FAILED
    assert cache_store.get(_ROUTE_CACHE_SOURCE, _default_query_hash()) is None


def test_missing_config_returns_not_connected_and_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disabled_settings = Settings(_env_file=None, osrm_base_url=None)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: disabled_settings)
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    adapter = OSRMRoutingAdapter(cache_store=cache_store)

    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.NOT_CONNECTED
    assert cache_store.get(_ROUTE_CACHE_SOURCE, _default_query_hash()) is None


def test_no_fabricated_route_data_when_missing_with_cache_wired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings_with_base_url(monkeypatch)
    monkeypatch.setattr(osrm_adapter, "get_settings", lambda: settings)
    _install_fake_client(
        monkeypatch,
        _FakeResponse(json_data={"code": "Ok", "routes": [{"distance": None, "duration": None}]}),
    )
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    adapter = OSRMRoutingAdapter(cache_store=cache_store)

    result = adapter.get_route(_request())

    assert result.status == ProviderStatus.UNAVAILABLE
    assert result.distance_meters is None
    assert result.duration_seconds is None
    assert cache_store.get(_ROUTE_CACHE_SOURCE, _default_query_hash()) is None


# ---------------------------------------------------------------------------
# 7. No real OSRM/network call anywhere in this file -- confirmed by every
#    test above installing a fake client, plus a static import check.
# ---------------------------------------------------------------------------


def test_osrm_adapter_module_has_no_disallowed_imports() -> None:
    import ast
    import inspect

    source = inspect.getsource(osrm_adapter)
    tree = ast.parse(source)

    disallowed_substrings = (
        "langgraph",
        "langsmith",
        "groq",
        "anthropic",
        "openai",
        "gemini",
        "google.generativeai",
        "kiwi",
        "mcp",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


def test_osrm_adapter_imports_provider_cache_store_for_route_caching() -> None:
    """Step 165A's predecessor test asserted the opposite of this (no
    caching yet). Step 165C wires route caching through `ProviderCacheStore`
    -- this confirms the real import now exists, rather than leaving the
    old negative assertion stale/broken."""
    import ast
    import inspect

    source = inspect.getsource(osrm_adapter)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    assert any("provider_cache_store" in name.lower() for name in imported_names)
    assert "cache_store: ProviderCacheStore | None = None" in source
