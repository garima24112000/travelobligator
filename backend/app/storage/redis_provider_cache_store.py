"""Redis-backed provider-response cache (Section 200B).

Redis is a CACHE, never a source of truth. It only ever holds already
normalised, validated *provider* responses (places, geocodes, routes, weather,
holidays, exchange rates) that adapters serialise themselves -- never AI
prompts/outputs, sessions, auth tokens, user free text or persistence.

Contract:
  * Same duck-typed interface as the SQLite `ProviderCacheStore`
    (`get`/`set`/`delete`), so every adapter is unchanged; the choice of
    backend is made once in `provider_cache_store.get_provider_cache_store`.
  * Redis failures NEVER raise into a provider call and NEVER switch to
    another backend: a failing GET is a miss (the adapter calls the provider),
    a failing SET is dropped (the live result is still returned). After a
    connection failure the store bypasses Redis for a short cooldown so an
    outage costs one timeout, not one per call.
  * Keys: ``<prefix>:provider-cache:v1:<source>:<digest>``. `source` is the
    adapter's provider/operation label; `digest` is the SHA-256 of the
    canonical (key-order independent) normalised request from
    `make_query_hash` -- so keys contain no API key, no raw request text and
    no secret. Bump `CACHE_NAMESPACE_VERSION` to invalidate every old entry
    after a schema change.
  * Values: a small JSON envelope (`schema`, `source`, `query_hash`,
    `stored_at`, `ttl_seconds`, `payload`, `metadata`). Redis' own TTL is the
    only expiry authority. A malformed/mismatched envelope is a miss and is
    deleted. A hit is NOT "verified": adapters still re-validate the payload
    into their models and mark such results `data_status=cached`.
  * Logging/metrics vocabulary: HIT / MISS / BYPASS / ERROR (`CacheStatus`);
    the Redis URL, credentials, keys and payloads are never logged.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from app.storage.provider_cache_store import ProviderCacheEntry, ProviderCacheValueError

logger = logging.getLogger("app.provider_cache")

CACHE_NAMESPACE_VERSION = "v1"
ENVELOPE_SCHEMA = 1
DEFAULT_MAX_TTL_SECONDS = 24 * 3600  # applied when a caller passes no TTL: Redis entries always expire
MAX_VALUE_BYTES = 1_000_000
CONNECTION_COOLDOWN_SECONDS = 15.0

_SOURCE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_DIGEST_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_INVALID_REDIS_URL = (
    "REDIS_URL is not a valid Redis connection URL (expected redis://, rediss:// or unix://). "
    "The value is intentionally not shown."
)


class CacheStatus(str, Enum):
    HIT = "HIT"
    MISS = "MISS"
    BYPASS = "BYPASS"
    ERROR = "ERROR"


class RedisConfigurationError(RuntimeError):
    """REDIS_URL is structurally invalid (an operator mistake). Fixed, safe message."""


_stats: Counter[tuple[str, str]] = Counter()
_stats_lock = RLock()


def cache_stats_snapshot() -> dict[str, int]:
    """`"<source>:<STATUS>" -> count` since process start (input for later metrics)."""
    with _stats_lock:
        return {f"{source}:{status}": count for (source, status), count in sorted(_stats.items())}


def reset_cache_stats() -> None:
    with _stats_lock:
        _stats.clear()


def validate_redis_url(url: str | None) -> str:
    """Structural validation only (no network). Returns the URL; raises
    `RedisConfigurationError` with a fixed message that never echoes it."""
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
        if parts.scheme in {"redis", "rediss"}:
            if not parts.hostname:
                raise ValueError
            _ = parts.port  # raises ValueError on a non-numeric/out-of-range port
            path = parts.path.lstrip("/")
            if path and not path.isdigit():
                raise ValueError
        elif parts.scheme == "unix":
            if not parts.path:
                raise ValueError
        else:
            raise ValueError
    except Exception:
        raise RedisConfigurationError(_INVALID_REDIS_URL) from None
    return raw


_EVENT_BY_STATUS = {
    "HIT": "cache.hit",
    "MISS": "cache.miss",
    "BYPASS": "cache.bypass",
    "ERROR": "cache.error",
}


def _record(source: str, operation: str, status: CacheStatus, *, reason: str | None = None,
            started: float | None = None, ttl_seconds: int | None = None) -> None:
    with _stats_lock:
        _stats[(source, status.value)] += 1
    # Section 200D: measurable without any log parsing. `provider` is the fixed cache-source label
    # (openstreetmap_poi, open_meteo, ...), never a query.
    from app.core.metrics import registry

    registry.inc("travelobligator_provider_cache_total", {"provider": source, "status": status.value})
    level = logging.WARNING if status == CacheStatus.ERROR else logging.DEBUG
    if not logger.isEnabledFor(level):
        return
    extra: dict[str, Any] = {
        "event": _EVENT_BY_STATUS.get(status.value, "cache.event"),
        "provider_cache_backend": "redis",
        "provider": source,
        "cache_source": source,
        "cache_operation": operation,
        "cache_status": status.value,
    }
    if reason:
        extra["cache_reason"] = reason
    if ttl_seconds is not None:
        extra["cache_ttl_seconds"] = ttl_seconds
    if started is not None:
        extra["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    logger.log(level, "Provider cache %s %s.", operation, status.value, extra=extra)


def _is_connection_failure(exc: BaseException) -> bool:
    import redis

    return isinstance(exc, (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, OSError))


class RedisProviderCacheStore:
    def __init__(
        self,
        client: Any,
        key_prefix: str = "travelobligator",
        clock: Callable[[], float] = time.monotonic,
        cooldown_seconds: float = CONNECTION_COOLDOWN_SECONDS,
    ) -> None:
        self._client = client
        self._prefix = key_prefix
        self._clock = clock
        self._cooldown_seconds = cooldown_seconds
        self._unavailable_until = 0.0
        self._last_failure_at: float | None = None
        self._lock = RLock()

    # -- keys -----------------------------------------------------------------------------

    def make_key(self, source: str, query_hash: str) -> str:
        if not _SOURCE_RE.match(source or ""):
            raise ProviderCacheValueError("source must be a short [A-Za-z0-9_.-] label.")
        if not _DIGEST_RE.match(query_hash or ""):
            raise ProviderCacheValueError("query_hash must be an opaque digest (see make_query_hash).")
        return f"{self._prefix}:provider-cache:{CACHE_NAMESPACE_VERSION}:{source}:{query_hash}"

    # -- availability --------------------------------------------------------------------------

    def _in_cooldown(self) -> bool:
        return self._clock() < self._unavailable_until

    def _failed(self, source: str, operation: str, exc: BaseException, started: float) -> None:
        if _is_connection_failure(exc):
            with self._lock:
                self._unavailable_until = self._clock() + self._cooldown_seconds
                self._last_failure_at = self._clock()
        # Only the exception CLASS is recorded: driver messages can echo host/credentials.
        _record(source, operation, CacheStatus.ERROR, reason=type(exc).__name__, started=started)

    def health(self) -> str:
        """`"ok"` if Redis answers a PING, else `"degraded"` (never raises)."""
        return "degraded" if self.probe() == "degraded" else "ok"

    def probe(self) -> str:
        """Live status for readiness/observability (one bounded PING; never raises, never exposes keys):

          * ``healthy``    -- PING answers and there has been no connection failure within the
                              recovery window;
          * ``recovering`` -- PING answers again but a connection failure happened within the last
                              cooldown window (Redis is back; normal caching resumes immediately);
          * ``degraded``   -- PING fails: provider calls run uncached.
        A successful PING clears the bypass cooldown, so recovery needs no application restart."""
        try:
            self._client.ping()
        except Exception as exc:
            with self._lock:
                if _is_connection_failure(exc):
                    self._unavailable_until = self._clock() + self._cooldown_seconds
                self._last_failure_at = self._clock()
            return "degraded"
        with self._lock:
            self._unavailable_until = 0.0
            recent = (
                self._last_failure_at is not None
                and self._clock() - self._last_failure_at < self._cooldown_seconds
            )
        return "recovering" if recent else "healthy"

    # -- interface shared with the SQLite store -------------------------------------------------

    def get(self, source: str, query_hash: str, now: datetime | None = None) -> ProviderCacheEntry | None:
        key = self.make_key(source, query_hash)
        started = time.monotonic()
        if self._in_cooldown():
            _record(source, "get", CacheStatus.BYPASS, reason="redis_unavailable_cooldown")
            return None
        try:
            raw = self._client.get(key)
        except Exception as exc:
            self._failed(source, "get", exc, started)
            return None
        if raw is None:
            _record(source, "get", CacheStatus.MISS, started=started)
            return None
        entry = self._decode(raw, source, query_hash)
        if entry is None:
            try:
                self._client.delete(key)  # corrupt entry: drop it, the caller repopulates
            except Exception:
                pass
            _record(source, "get", CacheStatus.MISS, reason="corrupt_entry", started=started)
            return None
        _record(source, "get", CacheStatus.HIT, started=started)
        return entry

    def set(
        self,
        source: str,
        query_hash: str,
        payload: Any,
        ttl_seconds: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ProviderCacheEntry:
        key = self.make_key(source, query_hash)
        stored_at = now if now is not None else datetime.now(timezone.utc)
        ttl = DEFAULT_MAX_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
        metadata_dict = dict(metadata) if metadata is not None else {}
        try:
            data = json.dumps(
                {
                    "schema": ENVELOPE_SCHEMA,
                    "source": source,
                    "query_hash": query_hash,
                    "stored_at": stored_at.isoformat(),
                    "ttl_seconds": ttl,
                    "payload": payload,
                    "metadata": metadata_dict,
                },
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ProviderCacheValueError(
                f"Cache payload for source={source!r} is not JSON-serializable."
            ) from exc
        entry = ProviderCacheEntry(
            source=source,
            query_hash=query_hash,
            payload=payload,
            fetched_at=stored_at,
            expires_at=stored_at + timedelta(seconds=max(ttl, 0)),
            metadata=metadata_dict,
            schema_version=ENVELOPE_SCHEMA,
            status="cached",
        )
        started = time.monotonic()
        if ttl <= 0:
            _record(source, "set", CacheStatus.BYPASS, reason="ttl_not_positive")
            return entry
        if len(data.encode("utf-8")) > MAX_VALUE_BYTES:
            _record(source, "set", CacheStatus.BYPASS, reason="value_too_large")
            return entry
        if self._in_cooldown():
            _record(source, "set", CacheStatus.BYPASS, reason="redis_unavailable_cooldown")
            return entry
        try:
            self._client.set(key, data, ex=ttl)
        except Exception as exc:
            self._failed(source, "set", exc, started)  # the live provider result is still returned
            return entry
        if logger.isEnabledFor(logging.DEBUG):  # a successful SET is not a HIT/MISS/BYPASS/ERROR lookup outcome
            logger.debug(
                "Provider cache set stored.",
                extra={
                    "provider_cache_backend": "redis",
                    "cache_source": source,
                    "cache_operation": "set",
                    "cache_ttl_seconds": ttl,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                },
            )
        return entry

    def delete(self, source: str, query_hash: str) -> None:
        key = self.make_key(source, query_hash)
        if self._in_cooldown():
            return
        try:
            self._client.delete(key)
        except Exception as exc:
            self._failed(source, "delete", exc, time.monotonic())

    def prune_expired(self, now: datetime | None = None) -> int:
        return 0  # Redis expires keys itself

    # -- decoding -------------------------------------------------------------------------------

    @staticmethod
    def _decode(raw: Any, source: str, query_hash: str) -> ProviderCacheEntry | None:
        try:
            text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
            envelope = json.loads(text)
            if not isinstance(envelope, dict):
                return None
            if envelope.get("schema") != ENVELOPE_SCHEMA:
                return None
            if envelope.get("source") != source or envelope.get("query_hash") != query_hash:
                return None
            if "payload" not in envelope:
                return None
            metadata = envelope.get("metadata")
            if not isinstance(metadata, dict):
                return None
            ttl = envelope.get("ttl_seconds")
            if not isinstance(ttl, int) or isinstance(ttl, bool):
                return None
            stored_at = datetime.fromisoformat(envelope["stored_at"])
            if stored_at.tzinfo is None:
                stored_at = stored_at.replace(tzinfo=timezone.utc)
            return ProviderCacheEntry(
                source=source,
                query_hash=query_hash,
                payload=envelope["payload"],
                fetched_at=stored_at,
                expires_at=stored_at + timedelta(seconds=max(ttl, 0)),  # informational; Redis TTL decides
                metadata=metadata,
                schema_version=ENVELOPE_SCHEMA,
                status="cached",
            )
        except Exception:
            return None


# -- process-level client / store ---------------------------------------------------------------

_store_lock = RLock()
_store: RedisProviderCacheStore | None = None
_store_key: tuple[Any, ...] | None = None
_client: Any = None


def redis_client_options(url: str, settings: Any) -> dict[str, Any]:
    """Keyword arguments for the one process-level client. A TLS URL (`rediss://`, e.g. a managed Redis
    reached over the public internet) also verifies the server certificate's HOSTNAME -- redis-py 5 verifies
    the certificate chain by default but not the hostname."""
    options: dict[str, Any] = {
        "socket_connect_timeout": settings.redis_connect_timeout_seconds,
        "socket_timeout": settings.redis_socket_timeout_seconds,
        "health_check_interval": 30,
    }
    if urlsplit(url).scheme == "rediss":
        options["ssl_check_hostname"] = True
    return options


def get_redis_provider_cache_store() -> RedisProviderCacheStore:
    """One process-level client (redis-py connection pool) and store, rebuilt
    only if the relevant settings change. No connection is opened here; the
    first command connects lazily."""
    global _store, _store_key, _client
    from app.core.config import get_settings

    settings = get_settings()
    url = validate_redis_url(settings.redis_url)
    key = (url, settings.redis_key_prefix, settings.redis_connect_timeout_seconds, settings.redis_socket_timeout_seconds)
    with _store_lock:
        if _store is not None and _store_key == key:
            return _store
        _close_client_locked()
        import redis

        _client = redis.Redis.from_url(url, **redis_client_options(url, settings))
        _store = RedisProviderCacheStore(_client, key_prefix=settings.redis_key_prefix)
        _store_key = key
        return _store


def _close_client_locked() -> None:
    global _store, _store_key, _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    _client = None
    _store = None
    _store_key = None


def close_redis_provider_cache_store() -> None:
    """Application shutdown / test reset: close the pool and forget the store."""
    with _store_lock:
        _close_client_locked()
