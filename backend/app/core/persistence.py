"""Persistence configuration validation (Section 200A).

Contract:
  * ``PERSISTENCE_BACKEND`` is the single selector. Default ``postgres``;
    ``local_json`` only when explicitly requested.
  * ``postgres`` REQUIRES a well-formed ``DATABASE_URL``. A missing/invalid
    value is a startup error -- never a silent SQLite/in-memory/Local-JSON
    substitute.
  * A configured-but-unreachable (or un-migrated) PostgreSQL is a startup
    error. The application NEVER switches backend because Postgres failed.
  * Every message here is a fixed string: no DATABASE_URL, username, host or
    driver text is ever included, and exceptions are raised ``from None`` so
    a driver's own message (which can echo connection details) is dropped.

Migrations are Alembic-managed and run as a deliberate release step
(``alembic upgrade head`` / ``scripts/run_migrations.py``) BEFORE the app
starts; startup only VERIFIES the schema is at the expected head, it never
migrates.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings, get_settings

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_MISSING_URL = (
    "PERSISTENCE_BACKEND=postgres (the default) requires DATABASE_URL, which is not set. Set "
    "DATABASE_URL, or explicitly set PERSISTENCE_BACKEND=local_json for local development/testing."
)
_INVALID_URL = (
    "DATABASE_URL is not a valid PostgreSQL connection URL (expected a postgresql:// or "
    "postgresql+psycopg:// URL with a database name). The value is intentionally not shown."
)
_UNREACHABLE = (
    "PostgreSQL persistence is selected but the database could not be reached or opened. "
    "Persistence was NOT switched to another backend. Check DATABASE_URL and that the database is running."
)
_SCHEMA = (
    "PostgreSQL is reachable but its schema is not at the expected migration head. Run "
    "`alembic upgrade head` (or scripts/run_migrations.py) as a release step before starting the app."
)


class PersistenceConfigurationError(RuntimeError):
    """Persistence is misconfigured (missing/invalid DATABASE_URL). Safe to display."""


class PersistenceUnavailableError(RuntimeError):
    """Postgres is selected and configured but cannot be used. Safe to display."""


def require_database_url(settings: Settings | None = None) -> str:
    """The configured DATABASE_URL, structurally validated. Raises
    ``PersistenceConfigurationError`` with a fixed message otherwise."""
    resolved = settings or get_settings()
    raw = (resolved.database_url or "").strip()
    if not raw:
        raise PersistenceConfigurationError(_MISSING_URL)
    try:
        from sqlalchemy.engine import make_url

        url = make_url(raw)
        driver_ok = url.drivername in {"postgresql", "postgres"} or url.drivername.startswith("postgresql+")
        has_target = bool(url.host) or bool(url.query.get("host"))
        if not (driver_ok and has_target and url.database):
            raise ValueError
    except Exception:
        raise PersistenceConfigurationError(_INVALID_URL) from None
    return raw


def validate_persistence_configuration(settings: Settings | None = None) -> None:
    """Config-only check (no network). ``local_json`` needs nothing; ``postgres``
    needs a valid DATABASE_URL."""
    resolved = settings or get_settings()
    if resolved.persistence_backend == "postgres":
        require_database_url(resolved)


def alembic_head_revisions() -> set[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    return set(ScriptDirectory.from_config(config).get_heads())


def check_postgres_ready(settings: Settings | None = None) -> None:
    """Connects, runs ``SELECT 1`` and verifies the Alembic revision equals the
    code's single head. Raises ``PersistenceUnavailableError`` (fixed text)
    on ANY failure; never falls back to another backend."""
    resolved = settings or get_settings()
    require_database_url(resolved)
    from sqlalchemy import text

    from app.db.session import build_engine

    engine = None
    try:
        engine = build_engine(resolved)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            try:
                current = {
                    row[0] for row in connection.execute(text("SELECT version_num FROM alembic_version"))
                }
            except Exception:
                raise PersistenceUnavailableError(_SCHEMA) from None
    except PersistenceUnavailableError:
        raise
    except Exception:
        raise PersistenceUnavailableError(_UNREACHABLE) from None
    finally:
        if engine is not None:
            engine.dispose()
    if current != alembic_head_revisions():
        raise PersistenceUnavailableError(_SCHEMA)


def startup_persistence_check(settings: Settings | None = None) -> None:
    """Run once at application startup. ``local_json``: nothing to verify.
    ``postgres``: configuration + connectivity + schema head. Emits ONE safe operational event
    (Section 200D: persistence.ready / persistence.unavailable / persistence.schema_mismatch) and
    re-raises the same fixed-message error -- no URL, host or driver text is ever logged."""
    import logging

    from app.core import ops_events

    resolved = settings or get_settings()
    log = logging.getLogger("app.persistence")
    try:
        validate_persistence_configuration(resolved)
        if resolved.persistence_backend == "postgres":
            check_postgres_ready(resolved)
    except PersistenceUnavailableError as exc:
        mismatch = str(exc) == _SCHEMA
        ops_events.log_event(
            log,
            logging.ERROR,
            ops_events.PERSISTENCE_SCHEMA_MISMATCH if mismatch else ops_events.PERSISTENCE_UNAVAILABLE,
            "Startup persistence check failed.",
            backend=resolved.persistence_backend,
            error_kind="schema_mismatch" if mismatch else "unreachable",
        )
        raise
    except PersistenceConfigurationError:
        ops_events.log_event(
            log, logging.ERROR, ops_events.PERSISTENCE_UNAVAILABLE, "Startup persistence check failed.",
            backend=resolved.persistence_backend, error_kind="configuration",
        )
        raise
    ops_events.log_event(
        log, logging.INFO, ops_events.PERSISTENCE_READY, "Startup persistence check passed.",
        backend=resolved.persistence_backend,
    )
