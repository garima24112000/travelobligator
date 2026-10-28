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
    key) -- see `production_configuration_problems`; and (Final Batch 2) an integration the release was
    not evaluated with, unless `PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS` names it.

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
    problems.extend(_production_abuse_limit_problems(settings))
    problems.extend(_production_model_stage_problems(settings))
    problems.extend(_production_unevaluated_integration_problems(settings))
    return problems


# Final Batch 2: integrations outside the evaluated release configuration: (name used in
# PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS, the env names that switch it on, is it switched on).
_UNEVALUATED_INTEGRATIONS = (
    ("targeted_regeneration", "TARGETED_REGENERATION_ENABLED",
     lambda s: s.targeted_regeneration_enabled),
    ("feedback_interpreter", "AI_FEEDBACK_INTERPRETER_ENABLED",
     lambda s: s.ai_feedback_interpreter_enabled),
    ("kiwi_flights", "FLIGHT_PROVIDER=kiwi_mcp / KIWI_MCP_ENABLED",
     lambda s: (s.flight_provider or "").strip().lower() == "kiwi_mcp" or s.kiwi_mcp_enabled),
    ("airport_lookup", "FLIGHT_AIRPORT_LOOKUP_PROVIDER",
     lambda s: s.flight_airport_lookup_provider != _NOT_CONNECTED),
)
_UNEVALUATED_INTEGRATION_NAMES = tuple(name for name, _env, _on in _UNEVALUATED_INTEGRATIONS)


def allowed_unevaluated_integrations(settings: Settings) -> list[str]:
    """The names listed in PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS (lower-cased, de-duplicated, in order)."""
    raw = settings.production_allow_unevaluated_integrations or ""
    return list(dict.fromkeys(part.strip().lower() for part in raw.split(",") if part.strip()))


def _production_unevaluated_integration_problems(settings: Settings) -> list[str]:
    """Production must not run an integration the release was not evaluated with because a dashboard
    value happened to switch it on. Each one needs its own name in PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS;
    an unknown name is refused rather than ignored, so a typo can never look like an allowance."""
    problems: list[str] = []
    allowed = allowed_unevaluated_integrations(settings)
    if [name for name in allowed if name not in _UNEVALUATED_INTEGRATION_NAMES]:
        problems.append(
            "PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS may only name: "
            + ", ".join(_UNEVALUATED_INTEGRATION_NAMES) + "."
        )
    for name, env, is_on in _UNEVALUATED_INTEGRATIONS:
        if is_on(settings) and name not in allowed:
            problems.append(
                f"APP_ENV=production: {env} switches on the {name} integration, which the release was not "
                f"evaluated with. Switch it off, or allow it on purpose with "
                f"PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS={name}."
            )
    return problems


# P1-B. The generation-time model stages of the release: (name, enabled setting, its env name, the
# stage's provider selector, its env name). Itinerary repair has no selector of its own: it runs on the
# reasoning stage's provider.
_RELEASE_MODEL_STAGES = (
    ("anchor discovery", "ai_candidate_discovery_enabled", "AI_CANDIDATE_DISCOVERY_ENABLED",
     "ai_candidate_proposal_provider", "AI_CANDIDATE_PROPOSAL_PROVIDER"),
    ("itinerary reasoning", "ai_itinerary_reasoning_enabled", "AI_ITINERARY_REASONING_ENABLED",
     "ai_itinerary_reasoning_provider", "AI_ITINERARY_REASONING_PROVIDER"),
    ("itinerary repair", "ai_itinerary_repair_enabled", "AI_ITINERARY_REPAIR_ENABLED",
     "ai_itinerary_reasoning_provider", "AI_ITINERARY_REASONING_PROVIDER"),
    ("narrator", "itinerary_narrator_enabled", "ITINERARY_NARRATOR_ENABLED",
     "itinerary_narrator_provider", "ITINERARY_NARRATOR_PROVIDER"),
)
# A model stage outside the generation pipeline: off by default, never required, but if it is switched on
# it needs the same things.
_OPTIONAL_MODEL_STAGES = (
    ("feedback interpreter", "ai_feedback_interpreter_enabled", "AI_FEEDBACK_INTERPRETER_ENABLED",
     "ai_feedback_interpreter_provider", "AI_FEEDBACK_INTERPRETER_PROVIDER"),
)
_NOT_CONNECTED = "not_connected"


def _has(value: str | None) -> bool:
    return bool((value or "").strip())


def _production_model_stage_problems(settings: Settings) -> list[str]:
    """Production must not come up with an AI capability silently missing. Config-only (no network, no
    model call), fixed messages that name variables and never echo a value:

      * each release stage is switched on, unless `PRODUCTION_ALLOW_AI_STAGES_DISABLED=true` says a
        disabled stage is deliberate;
      * a stage that is on has a connected provider (`not_connected` would make it do nothing);
      * the credential of every provider a stage selects is present (Groq: `GROQ_API_KEY`; Gemini:
        `GEMINI_API_KEY` and `GEMINI_MODEL`; Anthropic: `ANTHROPIC_API_KEY`);
      * with `LLM_FAILOVER_ENABLED=true` and a Groq/Gemini stage on, BOTH providers are configured --
        otherwise the stage would quietly run without its failover. Running one provider on purpose is
        `LLM_FAILOVER_ENABLED=false`.

    This checks presence only. Whether a key is valid is only known when a provider answers; that is
    reported at run time by the existing provider-health and stage diagnostics, never at startup.
    """
    problems: list[str] = []
    selected: set[str] = set()

    for name, enabled_field, enabled_env, provider_field, provider_env in _RELEASE_MODEL_STAGES:
        if not getattr(settings, enabled_field):
            if not settings.production_allow_ai_stages_disabled:
                problems.append(
                    f"APP_ENV=production requires {enabled_env}=true (the {name} stage of the release). "
                    "To run production without it on purpose, set PRODUCTION_ALLOW_AI_STAGES_DISABLED=true."
                )
            continue
        selected.add(_stage_provider(settings, name, provider_field, provider_env, problems))

    for name, enabled_field, _enabled_env, provider_field, provider_env in _OPTIONAL_MODEL_STAGES:
        if getattr(settings, enabled_field):
            selected.add(_stage_provider(settings, name, provider_field, provider_env, problems))

    has_groq = _has(settings.groq_api_key)
    has_gemini = _has(settings.gemini_api_key) and _has(settings.gemini_model)
    if "groq" in selected and not has_groq:
        problems.append("APP_ENV=production with a stage on the Groq provider requires GROQ_API_KEY.")
    if "gemini" in selected and not has_gemini:
        problems.append(
            "APP_ENV=production with a stage on the Gemini provider requires GEMINI_API_KEY and GEMINI_MODEL."
        )
    if "anthropic" in selected and not _has(settings.anthropic_api_key):
        problems.append("APP_ENV=production with a stage on the Anthropic provider requires ANTHROPIC_API_KEY.")

    routed = selected & {"groq", "gemini"}
    if routed and settings.llm_failover_enabled:
        if "groq" not in selected and not has_groq:
            problems.append(
                "LLM_FAILOVER_ENABLED=true requires GROQ_API_KEY as well (the other provider of the failover "
                "pair). To run a single provider on purpose, set LLM_FAILOVER_ENABLED=false."
            )
        if "gemini" not in selected and not has_gemini:
            problems.append(
                "LLM_FAILOVER_ENABLED=true requires GEMINI_API_KEY and GEMINI_MODEL as well (the other "
                "provider of the failover pair). To run a single provider on purpose, set "
                "LLM_FAILOVER_ENABLED=false."
            )
    return list(dict.fromkeys(problems))


def _stage_provider(
    settings: Settings, name: str, provider_field: str, provider_env: str, problems: list[str]
) -> str:
    provider = (getattr(settings, provider_field) or "").strip().lower()
    if not provider or provider == _NOT_CONNECTED:
        problems.append(
            f"APP_ENV=production: the {name} stage is switched on but {provider_env} is not set to a "
            "connected provider, so it would do nothing."
        )
        return _NOT_CONNECTED
    return provider


# P0: limits that a public deployment may not switch off (0 = off). The two per-address limits are not
# listed: they apply only when a trusted client address exists (`CLIENT_IP_TRUSTED_PROXY_HOPS`).
_REQUIRED_PRODUCTION_LIMITS = (
    ("generation_max_jobs_per_user_per_window", "GENERATION_MAX_JOBS_PER_USER_PER_WINDOW"),
    ("generation_max_jobs_global_per_window", "GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW"),
    ("generation_max_concurrent_jobs", "GENERATION_MAX_CONCURRENT_JOBS"),
    ("max_trips_per_user", "MAX_TRIPS_PER_USER"),
    ("signup_max_per_window_global", "SIGNUP_MAX_PER_WINDOW_GLOBAL"),
    ("login_max_failures_per_account", "LOGIN_MAX_FAILURES_PER_ACCOUNT"),
)


def _production_abuse_limit_problems(settings: Settings) -> list[str]:
    """The generation limits are enforced where a JOB is created, so production must run generation as
    jobs; and none of the limits that need no client address may be disabled."""
    problems: list[str] = []
    if not settings.async_generation_enabled:
        problems.append(
            "APP_ENV=production requires ASYNC_GENERATION_ENABLED=true: the generation admission limits "
            "are enforced when a generation job is created, and the synchronous path creates none."
        )
    disabled = [env for field, env in _REQUIRED_PRODUCTION_LIMITS if getattr(settings, field) <= 0]
    if disabled:
        problems.append(
            "APP_ENV=production requires these limits to be greater than 0: " + ", ".join(disabled) + "."
        )
    if settings.generation_max_jobs_per_user_per_window > settings.generation_max_jobs_global_per_window > 0:
        problems.append(
            "GENERATION_MAX_JOBS_PER_USER_PER_WINDOW must not exceed GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW."
        )
    problems.extend(_production_registration_problems(settings))
    return problems


# The shortest acceptable shareable-link token: the same bar as `SESSION_SECRET_KEY`.
MIN_BETA_REGISTRATION_TOKEN_LENGTH = 32


def _production_registration_problems(settings: Settings) -> list[str]:
    """Limited beta: production never runs with open registration, and an invitation link needs a
    long, unguessable token. Names the variable, never its value."""
    if settings.signup_mode == "open":
        return [
            "APP_ENV=production does not allow SIGNUP_MODE=open: use invite_link (the shareable beta link) "
            "or closed."
        ]
    token = settings.beta_registration_token or ""
    if settings.signup_mode == "invite_link" and len(token) < MIN_BETA_REGISTRATION_TOKEN_LENGTH:
        return [
            f"SIGNUP_MODE=invite_link requires BETA_REGISTRATION_TOKEN of at least "
            f"{MIN_BETA_REGISTRATION_TOKEN_LENGTH} characters."
        ]
    if token and token == (settings.session_secret_key or ""):
        return ["BETA_REGISTRATION_TOKEN must not be the same value as SESSION_SECRET_KEY."]
    return []


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
        # Final Batch 2: fixed integration names only, so an allowance is never silent.
        "production_integration_overrides": ",".join(
            name for name in allowed_unevaluated_integrations(resolved) if name in _UNEVALUATED_INTEGRATION_NAMES
        ) or "none",
    }
