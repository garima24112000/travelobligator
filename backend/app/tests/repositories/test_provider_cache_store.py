from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.storage.provider_cache_store import (
    ProviderCacheStore,
    ProviderCacheValueError,
    get_provider_cache_store,
    make_query_hash,
)

# Tests for the provider cache foundation (Step 164A,
# docs/12_provider_architecture.md "Provider Cache Foundation" section).
# This store is not wired into any provider adapter yet -- every test here
# exercises ProviderCacheStore directly with in-file payloads, never a real
# HTTP/provider/network call, and never a Groq/Anthropic/Kiwi/MCP call.


def _fixed_now() -> datetime:
    return datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. Cache store creates sqlite file/table.
# ---------------------------------------------------------------------------


def test_store_creates_sqlite_file(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    assert not path.exists()

    ProviderCacheStore(path)

    assert path.exists()


def test_store_creates_provider_cache_table(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    ProviderCacheStore(path)

    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='provider_cache'"
        ).fetchone()

    assert row is not None


def test_store_table_has_expected_columns(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    ProviderCacheStore(path)

    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(provider_cache)")}

    assert columns == {
        "source",
        "query_hash",
        "payload_json",
        "fetched_at",
        "expires_at",
        "metadata_json",
        "schema_version",
        "status",
    }


# ---------------------------------------------------------------------------
# 2. make_query_hash is deterministic regardless of dict key order.
# ---------------------------------------------------------------------------


def test_make_query_hash_is_order_independent() -> None:
    hash_a = make_query_hash({"destination": "Lisbon", "start_date": "2026-08-10"})
    hash_b = make_query_hash({"start_date": "2026-08-10", "destination": "Lisbon"})

    assert hash_a == hash_b


def test_make_query_hash_differs_for_different_queries() -> None:
    hash_a = make_query_hash({"destination": "Lisbon"})
    hash_b = make_query_hash({"destination": "Paris"})

    assert hash_a != hash_b


def test_make_query_hash_accepts_list_and_string_queries() -> None:
    list_hash = make_query_hash(["Lisbon", "Portugal"])
    string_hash = make_query_hash("Lisbon, Portugal")

    assert isinstance(list_hash, str)
    assert isinstance(string_hash, str)
    assert list_hash != string_hash


def test_make_query_hash_returns_a_sha256_hex_digest() -> None:
    result = make_query_hash({"a": 1})

    assert len(result) == 64
    assert all(char in "0123456789abcdef" for char in result)


# ---------------------------------------------------------------------------
# 3, 6. set then get returns payload (unexpired entry returns payload).
# ---------------------------------------------------------------------------


def test_set_then_get_returns_payload(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    query_hash = make_query_hash({"destination": "Lisbon"})

    store.set("open_meteo", query_hash, {"temperature_max_c": 28.0}, ttl_seconds=3600)
    entry = store.get("open_meteo", query_hash)

    assert entry is not None
    assert entry.payload == {"temperature_max_c": 28.0}
    assert entry.source == "open_meteo"
    assert entry.query_hash == query_hash
    assert entry.status == "cached"
    assert entry.schema_version == 1


def test_set_supports_list_and_scalar_payloads(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    store.set("nager_date", "list-hash", [{"name": "Holiday A"}])
    store.set("frankfurter", "scalar-hash", 1.23)

    assert store.get("nager_date", "list-hash").payload == [{"name": "Holiday A"}]
    assert store.get("frankfurter", "scalar-hash").payload == 1.23


def test_set_stores_metadata(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    store.set(
        "openstreetmap_places",
        "hash-1",
        {"place_id": "way/1"},
        metadata={"provider_status": "success"},
    )
    entry = store.get("openstreetmap_places", "hash-1")

    assert entry is not None
    assert entry.metadata == {"provider_status": "success"}


def test_set_overwrites_existing_row_for_same_key(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    store.set("open_meteo", "hash-1", {"temp": 20})
    store.set("open_meteo", "hash-1", {"temp": 25})

    entry = store.get("open_meteo", "hash-1")
    assert entry is not None
    assert entry.payload == {"temp": 25}


# ---------------------------------------------------------------------------
# 4. get missing returns None.
# ---------------------------------------------------------------------------


def test_get_missing_entry_returns_none(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    assert store.get("open_meteo", "does-not-exist") is None


def test_get_missing_source_returns_none_even_if_other_source_has_data(
    tmp_path: Path,
) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set("open_meteo", "hash-1", {"temp": 20})

    assert store.get("nager_date", "hash-1") is None


# ---------------------------------------------------------------------------
# 5, 8. Expired entry returns None; prune_expired deletes only expired rows.
# ---------------------------------------------------------------------------


def test_expired_entry_returns_none(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    now = _fixed_now()

    store.set("open_meteo", "hash-1", {"temp": 20}, ttl_seconds=60, now=now)

    assert store.get("open_meteo", "hash-1", now=now + timedelta(seconds=61)) is None


def test_entry_expires_at_exact_ttl_boundary(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    now = _fixed_now()

    store.set("open_meteo", "hash-1", {"temp": 20}, ttl_seconds=60, now=now)

    # At (or past) the exact expiry instant, the entry is already a miss.
    assert store.get("open_meteo", "hash-1", now=now + timedelta(seconds=60)) is None


def test_get_does_not_delete_expired_row(tmp_path: Path) -> None:
    """An expired row must stay in place until `prune_expired` is called
    explicitly -- `get`'s miss-on-expiry behavior never depends on
    background cleanup having run.
    """
    path = tmp_path / "cache.sqlite3"
    store = ProviderCacheStore(path)
    now = _fixed_now()
    store.set("open_meteo", "hash-1", {"temp": 20}, ttl_seconds=60, now=now)

    assert store.get("open_meteo", "hash-1", now=now + timedelta(seconds=120)) is None

    with sqlite3.connect(path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM provider_cache").fetchone()[0]
    assert count == 1


def test_prune_expired_deletes_only_expired_rows(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    now = _fixed_now()

    store.set("open_meteo", "expired-1", {"temp": 20}, ttl_seconds=60, now=now)
    store.set("open_meteo", "expired-2", {"temp": 21}, ttl_seconds=60, now=now)
    store.set("open_meteo", "still-fresh", {"temp": 22}, ttl_seconds=3600, now=now)
    store.set("frankfurter", "no-ttl", {"rate": 1.1}, ttl_seconds=None, now=now)

    deleted_count = store.prune_expired(now=now + timedelta(seconds=120))

    assert deleted_count == 2
    assert store.get("open_meteo", "expired-1", now=now) is None
    assert store.get("open_meteo", "expired-2", now=now) is None
    assert store.get("open_meteo", "still-fresh", now=now + timedelta(seconds=120)) is not None
    assert store.get("frankfurter", "no-ttl", now=now + timedelta(days=365)) is not None


def test_prune_expired_returns_zero_when_nothing_to_prune(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set("open_meteo", "hash-1", {"temp": 20}, ttl_seconds=3600)

    assert store.prune_expired() == 0


# ---------------------------------------------------------------------------
# 7. ttl_seconds=None gives no expires_at and does not expire.
# ---------------------------------------------------------------------------


def test_no_ttl_has_no_expires_at(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    store.set("nager_date", "hash-1", {"holidays": []}, ttl_seconds=None)
    entry = store.get("nager_date", "hash-1")

    assert entry is not None
    assert entry.expires_at is None


def test_no_ttl_entry_never_expires(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    now = _fixed_now()

    store.set("nager_date", "hash-1", {"holidays": []}, ttl_seconds=None, now=now)

    far_future = now + timedelta(days=3650)
    entry = store.get("nager_date", "hash-1", now=far_future)
    assert entry is not None
    assert entry.payload == {"holidays": []}


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------


def test_delete_removes_entry(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set("open_meteo", "hash-1", {"temp": 20})

    store.delete("open_meteo", "hash-1")

    assert store.get("open_meteo", "hash-1") is None


def test_delete_missing_entry_does_not_raise(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    store.delete("open_meteo", "does-not-exist")  # must not raise


# ---------------------------------------------------------------------------
# 9. clear_source deletes only that source.
# ---------------------------------------------------------------------------


def test_clear_source_deletes_only_that_source(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set("open_meteo", "hash-1", {"temp": 20})
    store.set("open_meteo", "hash-2", {"temp": 21})
    store.set("nager_date", "hash-1", {"holidays": []})

    deleted_count = store.clear_source("open_meteo")

    assert deleted_count == 2
    assert store.get("open_meteo", "hash-1") is None
    assert store.get("open_meteo", "hash-2") is None
    assert store.get("nager_date", "hash-1") is not None


def test_clear_source_returns_zero_for_unknown_source(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    store.set("open_meteo", "hash-1", {"temp": 20})

    assert store.clear_source("frankfurter") == 0
    assert store.get("open_meteo", "hash-1") is not None


# ---------------------------------------------------------------------------
# 10. Empty source/query_hash rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method_name", ["get", "delete"])
def test_empty_source_rejected(tmp_path: Path, method_name: str) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    method = getattr(store, method_name)

    with pytest.raises(ProviderCacheValueError):
        method("", "some-hash")


@pytest.mark.parametrize("method_name", ["get", "delete"])
def test_empty_query_hash_rejected(tmp_path: Path, method_name: str) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    method = getattr(store, method_name)

    with pytest.raises(ProviderCacheValueError):
        method("open_meteo", "")


def test_set_rejects_empty_source(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    with pytest.raises(ProviderCacheValueError):
        store.set("", "some-hash", {"a": 1})


def test_set_rejects_empty_query_hash(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    with pytest.raises(ProviderCacheValueError):
        store.set("open_meteo", "", {"a": 1})


def test_clear_source_rejects_empty_source(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    with pytest.raises(ProviderCacheValueError):
        store.clear_source("")


def test_whitespace_only_source_rejected(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    with pytest.raises(ProviderCacheValueError):
        store.get("   ", "some-hash")


# ---------------------------------------------------------------------------
# 11. Non-JSON-serializable payload rejected.
# ---------------------------------------------------------------------------


class _NotJsonSerializable:
    pass


def test_non_json_serializable_payload_rejected(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    with pytest.raises(ProviderCacheValueError):
        store.set("open_meteo", "hash-1", _NotJsonSerializable())


def test_non_json_serializable_metadata_rejected(tmp_path: Path) -> None:
    store = ProviderCacheStore(tmp_path / "cache.sqlite3")

    with pytest.raises(ProviderCacheValueError):
        store.set(
            "open_meteo",
            "hash-1",
            {"temp": 20},
            metadata={"bad": _NotJsonSerializable()},
        )


def test_rejected_set_does_not_write_a_row(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    store = ProviderCacheStore(path)

    with pytest.raises(ProviderCacheValueError):
        store.set("open_meteo", "hash-1", _NotJsonSerializable())

    with sqlite3.connect(path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM provider_cache").fetchone()[0]
    assert count == 0


# ---------------------------------------------------------------------------
# 12. Parent .data/cache directory is created automatically.
# ---------------------------------------------------------------------------


def test_parent_directory_created_automatically(tmp_path: Path) -> None:
    nested_path = tmp_path / "nested" / "does" / "not" / "exist" / "cache.sqlite3"
    assert not nested_path.parent.exists()

    ProviderCacheStore(nested_path)

    assert nested_path.parent.exists()
    assert nested_path.exists()


# ---------------------------------------------------------------------------
# 13. No raw query text is stored by make_query_hash.
# ---------------------------------------------------------------------------


def test_make_query_hash_output_never_contains_raw_query_text() -> None:
    query = {"destination": "a-very-distinctive-destination-string-xyz123"}

    result = make_query_hash(query)

    assert "a-very-distinctive-destination-string-xyz123" not in result
    assert "destination" not in result


def test_cache_row_never_stores_raw_query_only_its_hash(tmp_path: Path) -> None:
    """set()/get() only ever take a pre-computed query_hash -- there is no
    parameter through which the raw query dict/string could be written to
    the database at all.
    """
    path = tmp_path / "cache.sqlite3"
    store = ProviderCacheStore(path)
    raw_query = {"destination": "a-very-distinctive-destination-string-xyz123"}
    query_hash = make_query_hash(raw_query)

    store.set("openstreetmap_places", query_hash, {"place_id": "way/1"})

    with sqlite3.connect(path) as conn:
        raw_rows = conn.execute("SELECT * FROM provider_cache").fetchall()
    dumped = str(raw_rows)
    assert "a-very-distinctive-destination-string-xyz123" not in dumped


# ---------------------------------------------------------------------------
# get_provider_cache_store: process-wide sharing, mirroring
# get_local_json_store.
# ---------------------------------------------------------------------------


def test_get_provider_cache_store_returns_same_instance_for_same_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cache.sqlite3"

    first = get_provider_cache_store(path)
    second = get_provider_cache_store(path)

    assert first is second


def test_get_provider_cache_store_returns_different_instances_for_different_paths(
    tmp_path: Path,
) -> None:
    first = get_provider_cache_store(tmp_path / "cache_a.sqlite3")
    second = get_provider_cache_store(tmp_path / "cache_b.sqlite3")

    assert first is not second


# ---------------------------------------------------------------------------
# Commit durability: a fresh store instance pointed at the same file sees
# writes from a prior instance (writes are actually committed to disk).
# ---------------------------------------------------------------------------


def test_writes_are_committed_and_visible_to_a_fresh_store_instance(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cache.sqlite3"
    first_store = ProviderCacheStore(path)
    first_store.set("open_meteo", "hash-1", {"temp": 20}, ttl_seconds=3600)

    second_store = ProviderCacheStore(path)
    entry = second_store.get("open_meteo", "hash-1")

    assert entry is not None
    assert entry.payload == {"temp": 20}
