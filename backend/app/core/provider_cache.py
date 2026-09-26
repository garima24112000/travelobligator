"""Provider-cache configuration and status (Section 200B).

Persistence and caching fail differently:
  * PostgreSQL is authoritative -> a problem stops startup (see persistence.py).
  * Redis is an optimisation -> a *malformed* REDIS_URL (operator typo) stops
    startup with a fixed message, but an *unreachable* Redis does NOT: the app
    starts, reports the cache as degraded, and provider calls run uncached.
    It never silently switches to SQLite.
"""

from __future__ import annotations

import logging

from app.core.config import Settings, get_settings
from app.storage.redis_provider_cache_store import (
    RedisConfigurationError,
    close_redis_provider_cache_store,
    get_redis_provider_cache_store,
    validate_redis_url,
)

logger = logging.getLogger("app.provider_cache")

__all__ = [
    "RedisConfigurationError",
    "provider_cache_status",
    "shutdown_provider_cache",
    "startup_provider_cache_check",
    "validate_provider_cache_configuration",
]


def validate_provider_cache_configuration(settings: Settings | None = None) -> None:
    """Config-only check (no network): redis backend needs a well-formed REDIS_URL."""
    resolved = settings or get_settings()
    if resolved.provider_cache_enabled and resolved.provider_cache_backend == "redis":
        validate_redis_url(resolved.redis_url)


def provider_cache_status(settings: Settings | None = None) -> dict[str, str]:
    """Honest cache status for logs now and `/ready` later (200D). Never raises,
    never contains the URL: {backend, state}. state: ok | degraded | disabled | local."""
    resolved = settings or get_settings()
    if not resolved.provider_cache_enabled:
        return {"backend": "none", "state": "disabled"}
    if resolved.provider_cache_backend == "sqlite":
        return {"backend": "sqlite", "state": "local"}
    try:
        state = get_redis_provider_cache_store().health()
    except Exception:
        state = "degraded"
    return {"backend": "redis", "state": state}


def startup_provider_cache_check(settings: Settings | None = None) -> dict[str, str]:
    resolved = settings or get_settings()
    validate_provider_cache_configuration(resolved)
    status = provider_cache_status(resolved)
    if status["state"] == "degraded":
        logger.warning(
            "Provider cache backend is configured but Redis is unreachable; provider calls will run uncached.",
            extra={"provider_cache_backend": "redis", "cache_state": "degraded"},
        )
    return status


def shutdown_provider_cache() -> None:
    close_redis_provider_cache_store()
