from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

# Provider cache foundation (Step 164A, docs/12_provider_architecture.md
# "Provider Cache Foundation" section, docs/14_backend_architecture.md
# storage/repository layer). This module is intentionally not imported by
# any provider adapter, `ProviderGateway`, or `PlanningOrchestrator` yet --
# it exists so a later step can wire OSM/Open-Meteo/Nager/Frankfurter (and
# future providers) to cache their responses without first having to build
# this layer under time pressure. Nothing in this module changes any
# current provider result or planning behavior.


class ProviderCacheValueError(ValueError):
    """Raised for invalid cache inputs: an empty `source`/`query_hash`, or a
    `payload`/`metadata` that cannot be serialized to JSON. Deliberately
    fails loudly rather than silently storing a corrupt/ambiguous row.
    """


@dataclass(frozen=True)
class ProviderCacheEntry:
    """One cached provider response, read back from `ProviderCacheStore.get`.

    `payload` is whatever JSON-serializable value the caller stored via
    `ProviderCacheStore.set` -- this module never inspects, validates, or
    fabricates its shape; a cache miss (row absent or expired) always
    returns `None` from `get`, never a guessed/synthesized entry.
    """

    source: str
    query_hash: str
    payload: Any = field(repr=False)
    fetched_at: datetime
    expires_at: datetime | None
    metadata: dict[str, Any]
    schema_version: int
    status: str


def make_query_hash(query: Mapping[str, Any] | list[Any] | str) -> str:
    """Deterministic SHA-256 hash of a normalized query, independent of
    dict key order (`sort_keys=True`).

    This is the only supported way to derive a `query_hash` for
    `ProviderCacheStore` lookups. It intentionally returns an opaque hex
    digest only -- the raw `query` is never stored, logged, or returned by
    this function or by `ProviderCacheStore` itself, matching the "do not
    store raw query text by default" rule.
    """
    canonical = json.dumps(query, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime) -> datetime:
    """Treats a caller-supplied naive `datetime` as UTC rather than raising
    or silently comparing incompatible aware/naive values.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class ProviderCacheStore:
    """Small local SQLite cache store for provider responses, keyed by
    `(source, query_hash)` (Step 164A).

    Foundation only: no provider adapter reads or writes through this
    store yet -- see `docs/12_provider_architecture.md` and
    `docs/14_backend_architecture.md` for the intended future wiring and
    per-source TTL guidance. A cache miss (no row, or an expired row)
    always means "miss" -- `get` returns `None` and this store never
    fabricates, guesses, or backfills a payload. Expired rows are left in
    place until `prune_expired` is called explicitly, so `get`'s
    miss-on-expiry behavior never depends on background cleanup having run.

    Never stores raw query text (only `query_hash`, see `make_query_hash`),
    an API key/token, a prompt, or a raw LLM response -- callers are
    responsible for only ever passing already-normalized, non-secret
    provider response payloads and metadata.

    Python standard library only (`sqlite3`), matching `LocalJsonStore`'s
    local-development-only scope: single file, in-process locking only, no
    cross-process/multi-worker coordination, no migrations.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_cache (
                    source TEXT NOT NULL,
                    query_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'cached',
                    PRIMARY KEY (source, query_hash)
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    @staticmethod
    def _require_non_empty(value: str, field_name: str) -> str:
        if not value or not value.strip():
            raise ProviderCacheValueError(f"{field_name} must not be empty.")
        return value

    def get(
        self,
        source: str,
        query_hash: str,
        now: datetime | None = None,
    ) -> ProviderCacheEntry | None:
        """Returns the cached entry, or `None` on a missing or expired row.

        An expired row is never returned, but is also never deleted here --
        call `prune_expired` explicitly to reclaim that space.
        """
        self._require_non_empty(source, "source")
        self._require_non_empty(query_hash, "query_hash")
        current_time = _ensure_aware(now) if now is not None else _utc_now()

        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json, fetched_at, expires_at, metadata_json, "
                "schema_version, status FROM provider_cache "
                "WHERE source = ? AND query_hash = ?",
                (source, query_hash),
            ).fetchone()

        if row is None:
            return None

        payload_json, fetched_at, expires_at, metadata_json, schema_version, status = row

        if expires_at is not None and current_time >= datetime.fromisoformat(expires_at):
            return None

        return ProviderCacheEntry(
            source=source,
            query_hash=query_hash,
            payload=json.loads(payload_json),
            fetched_at=datetime.fromisoformat(fetched_at),
            expires_at=datetime.fromisoformat(expires_at) if expires_at is not None else None,
            metadata=json.loads(metadata_json),
            schema_version=schema_version,
            status=status,
        )

    def set(
        self,
        source: str,
        query_hash: str,
        payload: Any,
        ttl_seconds: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ProviderCacheEntry:
        """Stores (or overwrites) one cache row.

        `ttl_seconds=None` means no expiry (`expires_at` stays `None` --
        cache forever, until an explicit `delete`/`clear_source`), not "use
        a default TTL" -- this module deliberately has no source-specific
        TTL policy yet (see the TTL guidance in
        docs/12_provider_architecture.md).
        """
        self._require_non_empty(source, "source")
        self._require_non_empty(query_hash, "query_hash")

        try:
            payload_json = json.dumps(payload, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ProviderCacheValueError(
                f"Cache payload for source={source!r} is not JSON-serializable: {exc}"
            ) from exc

        metadata_dict = dict(metadata) if metadata is not None else {}
        try:
            metadata_json = json.dumps(metadata_dict, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ProviderCacheValueError(
                f"Cache metadata for source={source!r} is not JSON-serializable: {exc}"
            ) from exc

        fetched_at = _ensure_aware(now) if now is not None else _utc_now()
        expires_at = (
            fetched_at + timedelta(seconds=ttl_seconds) if ttl_seconds is not None else None
        )

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO provider_cache
                    (source, query_hash, payload_json, fetched_at, expires_at,
                     metadata_json, schema_version, status)
                VALUES (?, ?, ?, ?, ?, ?, 1, 'cached')
                ON CONFLICT (source, query_hash) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at,
                    metadata_json = excluded.metadata_json,
                    schema_version = excluded.schema_version,
                    status = excluded.status
                """,
                (
                    source,
                    query_hash,
                    payload_json,
                    fetched_at.isoformat(),
                    expires_at.isoformat() if expires_at is not None else None,
                    metadata_json,
                ),
            )
            conn.commit()

        return ProviderCacheEntry(
            source=source,
            query_hash=query_hash,
            payload=payload,
            fetched_at=fetched_at,
            expires_at=expires_at,
            metadata=metadata_dict,
            schema_version=1,
            status="cached",
        )

    def delete(self, source: str, query_hash: str) -> None:
        self._require_non_empty(source, "source")
        self._require_non_empty(query_hash, "query_hash")
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM provider_cache WHERE source = ? AND query_hash = ?",
                (source, query_hash),
            )
            conn.commit()

    def prune_expired(self, now: datetime | None = None) -> int:
        """Deletes every row whose `expires_at` is set and has passed.
        Rows with `expires_at IS NULL` (no TTL) are never touched. Returns
        the number of rows deleted.
        """
        current_time = _ensure_aware(now) if now is not None else _utc_now()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM provider_cache WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (current_time.isoformat(),),
            )
            conn.commit()
            return cursor.rowcount

    def clear_source(self, source: str) -> int:
        """Deletes every row for `source`, expired or not. Returns the
        number of rows deleted.
        """
        self._require_non_empty(source, "source")
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM provider_cache WHERE source = ?", (source,)
            )
            conn.commit()
            return cursor.rowcount


_stores: dict[str, ProviderCacheStore] = {}
_stores_lock = RLock()


def get_provider_cache_store(path: Path) -> ProviderCacheStore:
    """Returns a process-wide `ProviderCacheStore` shared by every caller
    that resolves to the same file path, mirroring
    `app.storage.local_json_store.get_local_json_store`. Not called by any
    provider adapter yet (Step 164A is foundation only).
    """
    key = str(path.resolve())
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = ProviderCacheStore(path)
            _stores[key] = store
        return store
