"""Startup configuration diagnostics and contradiction checks (Section 200D).

`validate_runtime_configuration` fails EARLY (fixed, secret-free messages) for combinations that
cannot work, instead of letting them surprise an operator later:

  * PostgreSQL enforces at most ONE active (queued/running) job per trip with a unique index, so
    `GENERATION_JOB_MAX_RUNNING_PER_TRIP > 1` with `PERSISTENCE_BACKEND=postgres` is REJECTED --
    the value would silently not be honoured. (Local JSON still honours it; it is dev-only.)
  * a job that was queued but never claimed must not be declared stale before a lease could even
    have expired: `GENERATION_JOB_STALE_AFTER_SECONDS >= GENERATION_JOB_LEASE_SECONDS`;
  * the heartbeat interval must be shorter than the lease it renews;
  * the readiness timeout must cover the Redis socket timeout it waits on.

Per-field bounds (persistence URL shape, REDIS_URL shape, lease >= 3 s, timeouts > 0) are enforced by
`Settings` / `core.persistence` / `core.provider_cache`. `startup_config_summary` is the ONLY thing
logged about configuration: a fixed set of safe scalars, never the settings object, a URL or a key.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings


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
    if problems:
        raise OperationalConfigurationError(" ".join(problems))


def startup_config_summary(settings: Settings | None = None, migration_head: str | None = None) -> dict[str, object]:
    """The safe, fixed set of configuration facts logged at startup (Task 22)."""
    resolved = settings or get_settings()
    return {
        "persistence_backend": resolved.persistence_backend,
        "provider_cache_backend": (
            "none" if not resolved.provider_cache_enabled else resolved.provider_cache_backend
        ),
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
