from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.models.common import DataStatus, ProviderStatus
from app.providers.currency import frankfurter_adapter
from app.providers.currency.frankfurter_adapter import (
    FrankfurterCurrencyAdapter,
    infer_destination_currency,
)
from app.storage.provider_cache_store import ProviderCacheStore, make_query_hash


@pytest.fixture(autouse=True)
def _isolated_default_provider_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test in this file gets a fresh, isolated `ProviderCacheStore`
    whenever an adapter is constructed *without* an explicit `cache_store`
    (Step 164D lazily resolves one via `get_provider_cache_store`). Without
    this, the adapter's default lazy resolution would share one real,
    process-wide cache file (`get_provider_cache_store` is a singleton
    keyed by resolved path) across every test function in this file --
    including the pre-existing tests below that construct
    `FrankfurterCurrencyAdapter()` with no arguments and reuse the same
    base/destination currency pair (so the same cache key) across multiple
    tests with *different* expected payloads. Without isolation, an
    earlier test's cached rate would silently satisfy a later test's
    HTTP-call assertions instead of exercising the fake HTTP client, and
    would write to the real repo-local `.data/provider_cache.sqlite3` file.
    """
    fresh_store = ProviderCacheStore(tmp_path / "isolated_default_cache.sqlite3")
    monkeypatch.setattr(frankfurter_adapter, "get_provider_cache_store", lambda path: fresh_store)
    return fresh_store


def _query_hash(base_currency: str = "USD", destination_currency: str = "EUR") -> str:
    return make_query_hash(
        {
            "base_currency": base_currency,
            "destination_currency": destination_currency,
            "query_type": "latest",
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
    """Stands in for `httpx.Client`. Records the request params it was
    called with so tests can assert on from/to currency codes."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.get_call_count = 0
        self.last_params: dict[str, Any] | None = None

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get(self, url: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        self.get_call_count += 1
        self.last_params = params
        return self._response


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, response: _FakeResponse
) -> _FakeClient:
    fake_client = _FakeClient(response)
    monkeypatch.setattr(frankfurter_adapter.httpx, "Client", lambda **kwargs: fake_client)
    return fake_client


def test_infer_destination_currency_matches_examples() -> None:
    assert infer_destination_currency("Lisbon, Portugal") == "EUR"
    assert infer_destination_currency("Paris, France") == "EUR"
    assert infer_destination_currency("Madrid, Spain") == "EUR"
    assert infer_destination_currency("Rome, Italy") == "EUR"
    assert infer_destination_currency("New York, United States") == "USD"
    assert infer_destination_currency("New York, USA") == "USD"
    assert infer_destination_currency("London, United Kingdom") == "GBP"
    assert infer_destination_currency("London, UK") == "GBP"
    assert infer_destination_currency("Mumbai, India") == "INR"
    assert infer_destination_currency("Toronto, Canada") == "CAD"
    assert infer_destination_currency("Testville, Testland") is None
    assert infer_destination_currency("Los Angeles") is None
    assert infer_destination_currency("") is None


def test_success_usd_to_eur(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.provider_name == "frankfurter"

    rate = response.data
    assert rate.base_currency == "USD"
    assert rate.destination_currency == "EUR"
    assert rate.exchange_rate == 0.92
    assert str(rate.rate_date) == "2026-08-10"
    assert rate.source == "frankfurter"
    assert rate.data_status == DataStatus.LIVE

    assert fake_client.get_call_count == 1
    assert fake_client.last_params == {"from": "USD", "to": "EUR"}


def test_same_currency_returns_one_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data={}))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "New York, USA")

    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE

    rate = response.data
    assert rate.base_currency == "USD"
    assert rate.destination_currency == "USD"
    assert rate.exchange_rate == 1.0

    # No network call was needed for a same-currency identity result.
    assert fake_client.get_call_count == 0


def test_no_fake_fields_added(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}
    _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    dumped = response.data.model_dump()
    assert set(dumped.keys()) == {
        "base_currency",
        "destination_currency",
        "exchange_rate",
        "rate_date",
        "source",
        "data_status",
    }
    for forbidden_field in (
        "cost",
        "price",
        "budget",
        "fee",
        "tax",
        "total",
        "hotel",
        "restaurant",
        "attraction",
        "rating",
    ):
        assert forbidden_field not in dumped


def test_unknown_destination_currency_returns_unavailable_without_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data={}))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Testville, Testland")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data_status == DataStatus.UNAVAILABLE
    assert response.data is None
    assert "exchange_rate" in response.unavailable_fields
    assert fake_client.get_call_count == 0


def test_request_failure_returns_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(should_fail=True))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.FAILED
    assert response.data_status == DataStatus.FAILED
    assert response.data is None
    assert "exchange_rate" in response.unavailable_fields


def test_no_usable_rate_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {}}
    _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data is None


def test_malformed_payload_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(json_data={"error": "not usable"}))

    adapter = FrankfurterCurrencyAdapter()
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert response.data is None


# ---------------------------------------------------------------------------
# Provider cache wiring (Step 164D, docs/12_provider_architecture.md
# "Provider Cache Foundation" section). Every test below injects its own
# `ProviderCacheStore` via the constructor -- never the real repo-local
# `.data/provider_cache.sqlite3` file -- and never makes a real network
# call; only the existing `_FakeClient`/`_FakeResponse` doubles are used.
# ---------------------------------------------------------------------------


def test_cache_hit_returns_cached_response_without_http_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data={}))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set(
        "frankfurter",
        _query_hash(),
        {
            "base_currency": "USD",
            "destination_currency": "EUR",
            "exchange_rate": 0.92,
            "rate_date": "2026-08-10",
            "source": "frankfurter",
            "data_status": "live",
        },
        ttl_seconds=21600,
    )

    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert fake_client.get_call_count == 0
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.CACHED
    assert response.provider_name == "frankfurter"
    rate = response.data
    assert rate.base_currency == "USD"
    assert rate.destination_currency == "EUR"
    assert rate.exchange_rate == 0.92
    assert rate.data_status == DataStatus.CACHED


def test_cache_miss_calls_http_once_and_writes_normalized_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert fake_client.get_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE

    entry = store.get("frankfurter", _query_hash())
    assert entry is not None
    assert entry.payload == {
        "base_currency": "USD",
        "destination_currency": "EUR",
        "exchange_rate": 0.92,
        "rate_date": "2026-08-10",
        "source": "frankfurter",
        "data_status": "live",
    }


def test_second_identical_call_uses_cache_and_does_not_call_http_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    adapter = FrankfurterCurrencyAdapter(cache_store=store)

    first = adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    second = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert fake_client.get_call_count == 1
    assert first.data_status == DataStatus.LIVE
    assert second.data_status == DataStatus.CACHED
    assert second.data.exchange_rate == first.data.exchange_rate


def test_expired_cache_entry_behaves_like_miss_and_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timedelta, timezone

    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-11", "rates": {"EUR": 0.95}}
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    already_expired = datetime.now(timezone.utc) - timedelta(hours=1)
    store.set(
        "frankfurter",
        _query_hash(),
        {
            "base_currency": "USD",
            "destination_currency": "EUR",
            "exchange_rate": 0.10,
            "rate_date": "2020-01-01",
            "source": "frankfurter",
            "data_status": "live",
        },
        ttl_seconds=1,
        now=already_expired,
    )

    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert fake_client.get_call_count == 1
    assert response.data_status == DataStatus.LIVE
    assert response.data.exchange_rate == 0.95

    refreshed_entry = store.get("frankfurter", _query_hash())
    assert refreshed_entry is not None
    assert refreshed_entry.payload["exchange_rate"] == 0.95


def test_provider_cache_disabled_bypasses_cache_and_calls_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    disabled_settings = _settings(monkeypatch, provider_cache_enabled=False)
    monkeypatch.setattr(frankfurter_adapter, "get_settings", lambda: disabled_settings)
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    # Even with an explicit cache_store injected, provider_cache_enabled=False
    # must skip the cache completely.
    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    first = adapter.get_exchange_rate("USD", "Lisbon, Portugal")
    second = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert fake_client.get_call_count == 2
    assert first.data_status == DataStatus.LIVE
    assert second.data_status == DataStatus.LIVE
    assert store.get("frankfurter", _query_hash()) is None


def test_cache_read_failure_falls_back_to_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    class _RaisingGetCacheStore:
        def get(self, source: str, query_hash: str, now: Any = None) -> None:
            raise RuntimeError("cache backend unavailable")

        def set(self, *args: Any, **kwargs: Any) -> None:
            pass

    adapter = FrankfurterCurrencyAdapter(cache_store=_RaisingGetCacheStore())
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert fake_client.get_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.data.exchange_rate == 0.92


def test_cache_write_failure_still_returns_live_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))

    class _RaisingSetCacheStore:
        def get(self, source: str, query_hash: str, now: Any = None) -> None:
            return None

        def set(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("cache write failed")

    adapter = FrankfurterCurrencyAdapter(cache_store=_RaisingSetCacheStore())
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert fake_client.get_call_count == 1
    assert response.status == ProviderStatus.SUCCESS
    assert response.data_status == DataStatus.LIVE
    assert response.data.exchange_rate == 0.92


def test_cache_key_is_deterministic_for_equivalent_normalized_queries() -> None:
    query_a = {"base_currency": "USD", "destination_currency": "EUR", "query_type": "latest"}
    query_b = {"query_type": "latest", "destination_currency": "EUR", "base_currency": "USD"}

    assert make_query_hash(query_a) == make_query_hash(query_b)


def test_different_currencies_produce_different_query_hashes() -> None:
    base_hash = _query_hash()

    different_base_hash = _query_hash(base_currency="GBP")
    different_destination_hash = _query_hash(destination_currency="INR")

    assert base_hash != different_base_hash
    assert base_hash != different_destination_hash


def test_cache_entry_stores_no_secrets_in_payload_or_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {"EUR": 0.92}}
    _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    entry = store.get("frankfurter", _query_hash())
    assert entry is not None
    assert entry.metadata == {}

    dumped = str(entry.payload)
    for forbidden in ("api_key", "token", "secret", "authorization", "bearer"):
        assert forbidden not in dumped.lower()


def test_unknown_destination_currency_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(json_data={}))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    response = adapter.get_exchange_rate("USD", "Testville, Testland")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert store.get("frankfurter", _query_hash()) is None


def test_no_usable_rate_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"amount": 1.0, "base": "USD", "date": "2026-08-10", "rates": {}}
    _install_fake_client(monkeypatch, _FakeResponse(json_data=payload))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert store.get("frankfurter", _query_hash()) is None


def test_malformed_payload_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(json_data={"error": "not usable"}))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.UNAVAILABLE
    assert store.get("frankfurter", _query_hash()) is None


def test_request_failure_response_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_client(monkeypatch, _FakeResponse(should_fail=True))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    response = adapter.get_exchange_rate("USD", "Lisbon, Portugal")

    assert response.status == ProviderStatus.FAILED
    assert store.get("frankfurter", _query_hash()) is None


def test_same_currency_identity_result_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = _install_fake_client(monkeypatch, _FakeResponse(json_data={}))
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    adapter = FrankfurterCurrencyAdapter(cache_store=store)
    response = adapter.get_exchange_rate("USD", "New York, USA")

    assert response.data.exchange_rate == 1.0
    assert fake_client.get_call_count == 0
    assert store.get("frankfurter", _query_hash(base_currency="USD", destination_currency="USD")) is None
