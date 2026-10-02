"""Startup configuration diagnostics and contradiction checks (Section 200D).

`validate_runtime_configuration` fails EARLY (fixed, secret-free messages) for combinations that
cannot work, instead of letting them surprise an operator later:

  * PostgreSQL enforces at most ONE active (queued/running) job per trip with a unique index, so
    `GENERATION_JOB_MAX_RUNNING_PER_TRIP > 1` with `PERSISTENCE_BACKEND=postgres` is REJECTED --
    the value would silently not be honoured. (Local JSON still honours it; it is dev-only.)
  * a job that was queued but never claimed must not be declared stale before a lease could even
    have expired: `GENERATION_JOB_STALE_AFTER_SECONDS >= GENERATION_JOB_LEASE_SECONDS`;
  * the heartbeat interval must be shorter than the lease it renews;
  * the readiness timeout must cover the Redis socket timeout it waits on;
  * credentialed CORS never accepts a wildcard, credentials or a non-origin value (every environment);
  * Section 203B: with `APP_ENV=production` the development-only choices are rejected outright
    (Local JSON, SQLite provider cache, debug mode, insecure/cross-site session cookie, missing or
    short session secret, non-https CORS origin, the public Nominatim geocoder, Geoapify without its
    key) -- see `production_configuration_problems`.

Per-field bounds (persistence URL shape, REDIS_URL shape, lease >= 3 s, timeouts > 0) are enforced by
`Settings` / `core.persistence` / `core.provider_cache`. `startup_config_summary` is the ONLY thing
logged about configuration: a fixed set of safe scalars, never the settings object, a URL or a key.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.core.config import Settings, get_settings

MIN_PRODUCTION_SESSION_SECRET_LENGTH = 32
MIN_OPS_TOKEN_LENGTH = 24


class OperationalConfigurationError(RuntimeError):
    """Contradictory/impossible configuration. Safe to display (fixed text, no values)."""


def heartbeat_interval_seconds(lease_seconds: int) -> float:
    """Heartbeat cadence: a third of the lease, at least 1 s (single definition; see job service)."""
    return max(1.0, lease_seconds / 3)


def validate_runtime_configuration(settings: Settings | None = None) -> None:
    resolved = settings or get_settings()
    problems: list[str] = []

    if resolved.persistence_backend == "postgres" and resolved.generation_job_max_running_per_trip > 1:
        problems.append(
            "GENERATION_JOB_MAX_RUNNING_PER_TRIP > 1 is not supported with PERSISTENCE_BACKEND=postgres: "
            "the database enforces at most one active job per trip. Set it to 1."
        )
    if resolved.generation_job_stale_after_seconds < resolved.generation_job_lease_seconds:
        problems.append(
            "GENERATION_JOB_STALE_AFTER_SECONDS must be >= GENERATION_JOB_LEASE_SECONDS "
            "(a queued job cannot be stale before a lease could expire)."
        )
    if heartbeat_interval_seconds(resolved.generation_job_lease_seconds) >= resolved.generation_job_lease_seconds:
        problems.append("The job heartbeat interval must be shorter than GENERATION_JOB_LEASE_SECONDS.")
    if (
        resolved.provider_cache_enabled
        and resolved.provider_cache_backend == "redis"
        and resolved.readiness_timeout_seconds <= resolved.redis_socket_timeout_seconds
    ):
        problems.append(
            "READINESS_TIMEOUT_SECONDS must be greater than REDIS_SOCKET_TIMEOUT_SECONDS, otherwise /ready "
            "could time out before the Redis probe can answer."
        )
    problems.extend(_cors_problems(resolved, require_https=is_production(resolved)))
    if is_production(resolved):
        problems.extend(production_configuration_problems(resolved))
    if problems:
        raise OperationalConfigurationError(" ".join(problems))


def is_production(settings: Settings) -> bool:
    return (settings.app_env or "").strip().lower() == "production"


def cors_origins(settings: Settings) -> list[str]:
    """The configured browser origins (`BACKEND_CORS_ORIGINS`, comma-separated)."""
    return [origin.strip() for origin in settings.backend_cors_origins.split(",") if origin.strip()]


def _cors_problems(settings: Settings, *, require_https: bool) -> list[str]:
    """CORS is always credentialed (session cookie), so a wildcard is never acceptable, and an origin is
    exactly `scheme://host[:port]` -- no credentials, path, query or fragment. Values are never echoed."""
    problems: list[str] = []
    for origin in cors_origins(settings):
        if "*" in origin:
            problems.append(
                "BACKEND_CORS_ORIGINS must list explicit origins: a wildcard is not allowed with credentialed CORS."
            )
            continue
        try:
            parts = urlsplit(origin)
            _ = parts.port  # raises ValueError on a malformed port
            well_formed = parts.scheme in {"http", "https"} and bool(parts.hostname)
        except ValueError:
            parts, well_formed = None, False
        if not well_formed or parts is None:
            problems.append("BACKEND_CORS_ORIGINS contains an entry that is not an http(s) origin.")
        elif parts.username is not None or parts.password is not None or "@" in parts.netloc:
            problems.append("BACKEND_CORS_ORIGINS must not contain credentials.")
        elif parts.path not in ("", "/") or parts.query or parts.fragment:
            problems.append("BACKEND_CORS_ORIGINS entries must be bare origins (no path, query or fragment).")
        elif require_https and parts.scheme != "https":
            problems.append("APP_ENV=production requires every BACKEND_CORS_ORIGINS entry to be an https origin.")
    return list(dict.fromkeys(problems))


def production_configuration_problems(settings: Settings) -> list[str]:
    """Section 203B: combinations that must never run with APP_ENV=production. Config-only (no network),
    fixed secret-free messages. PostgreSQL stays the only authoritative store, Redis the only (optional,
    disposable) provider cache -- there is no SQLite or Local JSON production fallback."""
    from app.core.persistence import PersistenceConfigurationError, require_database_url

    problems: list[str] = []
    if settings.persistence_backend != "postgres":
        problems.append("APP_ENV=production requires PERSISTENCE_BACKEND=postgres (Local JSON is development-only).")
    else:
        try:
            require_database_url(settings)
        except PersistenceConfigurationError as exc:
            problems.append(str(exc))
    if settings.provider_cache_enabled and settings.provider_cache_backend != "redis":
        problems.append(
            "APP_ENV=production requires PROVIDER_CACHE_BACKEND=redis (or PROVIDER_CACHE_ENABLED=false); "
            "the SQLite provider cache is development-only."
        )
    else:
        from app.core.provider_cache import RedisConfigurationError, validate_provider_cache_configuration

        try:
            validate_provider_cache_configuration(settings)
        except RedisConfigurationError as exc:
            problems.append(str(exc))
    problems.extend(_production_geocoder_problems(settings))
    problems.extend(_production_places_and_routing_problems(settings))
    if settings.app_debug:
        problems.append("APP_ENV=production requires APP_DEBUG=false.")
    if len(settings.session_secret_key or "") < MIN_PRODUCTION_SESSION_SECRET_LENGTH:
        problems.append(
            "APP_ENV=production requires SESSION_SECRET_KEY to be set to a generated secret of at least "
            f"{MIN_PRODUCTION_SESSION_SECRET_LENGTH} characters."
        )
    if not settings.session_cookie_secure:
        problems.append("APP_ENV=production requires SESSION_COOKIE_SECURE=true.")
    if not settings.session_cookie_httponly:
        problems.append("APP_ENV=production requires SESSION_COOKIE_HTTPONLY=true.")
    if settings.session_cookie_samesite == "none":
        problems.append(
            "APP_ENV=production requires SESSION_COOKIE_SAMESITE=lax or strict (browser API traffic is same-origin)."
        )
    if settings.ops_token is not None and len(settings.ops_token) < MIN_OPS_TOKEN_LENGTH:
        problems.append(f"OPS_TOKEN must be at least {MIN_OPS_TOKEN_LENGTH} characters when set.")
    return problems


_PUBLIC_NOMINATIM_HOST = "nominatim.openstreetmap.org"


def _production_geocoder_problems(settings: Settings) -> list[str]:
    """Section 203C.1: production place grounding must not depend on the public Nominatim endpoint (it
    rate-limits shared hosting egress), and a keyed geocoder without its key is a misconfiguration, not a
    reason to fall back."""
    if settings.geocoding_provider == "geoapify":
        if not (settings.geoapify_api_key or "").strip():
            return ["APP_ENV=production with GEOCODING_PROVIDER=geoapify requires GEOAPIFY_API_KEY."]
        return []
    try:
        host = (urlsplit(settings.nominatim_api_url).hostname or "").lower()
    except ValueError:
        host = ""
    if host == _PUBLIC_NOMINATIM_HOST and not settings.allow_public_nominatim_in_production:
        return [
            "APP_ENV=production must not use the public Nominatim endpoint as its geocoder: set "
            "GEOCODING_PROVIDER=geoapify (with GEOAPIFY_API_KEY), point NOMINATIM_API_URL at your own "
            "instance, or set ALLOW_PUBLIC_NOMINATIM_IN_PRODUCTION=true to accept its rate limit."
        ]
    return []


def _production_places_and_routing_problems(settings: Settings) -> list[str]:
    """Section 203C.2B: production POI discovery is Geoapify Places (Overpass is development/experimental
    only -- it is not release-critical and there is no fallback to it), a keyed provider needs its key, and
    the inventory sufficiency gate / usefulness contract cannot be switched off."""
    problems: list[str] = []
    has_key = bool((settings.geoapify_api_key or "").strip())
    if settings.places_provider != "geoapify":
        problems.append(
            "APP_ENV=production requires PLACES_PROVIDER=geoapify (the OpenStreetMap/Overpass places provider "
            "is development-only)."
        )
    elif not has_key:
        problems.append("APP_ENV=production with PLACES_PROVIDER=geoapify requires GEOAPIFY_API_KEY.")
    if settings.routing_provider == "geoapify" and not has_key:
        problems.append("APP_ENV=production with ROUTING_PROVIDER=geoapify requires GEOAPIFY_API_KEY.")
    if not settings.inventory_sufficiency_gate_enabled:
        problems.append("APP_ENV=production requires INVENTORY_SUFFICIENCY_GATE_ENABLED=true.")
    return list(dict.fromkeys(problems))


def startup_config_summary(settings: Settings | None = None, migration_head: str | None = None) -> dict[str, object]:
    """The safe, fixed set of configuration facts logged at startup (Task 22)."""
    resolved = settings or get_settings()
    return {
        "persistence_backend": resolved.persistence_backend,
        "provider_cache_backend": (
            "none" if not resolved.provider_cache_enabled else resolved.provider_cache_backend
        ),
        "geocoding_provider": resolved.geocoding_provider,
        "places_provider": resolved.places_provider,
        "routing_provider": resolved.routing_provider,
        "async_generation_enabled": resolved.async_generation_enabled,
        "metrics_enabled": resolved.metrics_enabled,
        "migration_head": migration_head,
        "lease_seconds": resolved.generation_job_lease_seconds,
        # Section 200E: safe non-secret scalars only. connections/process = pool_size + max_overflow.
        "db_pool_size": resolved.db_pool_size,
        "db_max_overflow": resolved.db_max_overflow,
        "db_max_connections": resolved.db_pool_size + resolved.db_max_overflow,
        "db_statement_timeout_ms": resolved.db_statement_timeout_ms,
        "db_lock_timeout_ms": resolved.db_lock_timeout_ms,
    }
