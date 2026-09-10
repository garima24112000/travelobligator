"""Sync SQLAlchemy engine/session foundation (Step 183B).

Nothing in this repository imports this module outside of its own tests
yet. `LocalJsonStore` (`app/storage/local_json_store.py`) remains the only
persistence layer actually used by routes/services/repositories today,
regardless of `Settings.persistence_backend` -- see
`docs/14_backend_architecture.md`.

Design constraints (deliberate, do not relax without re-reading Step
183B/183C docs):
  * Sync SQLAlchemy only. No `asyncpg`, no async engine/session.
  * `psycopg` (already a project dependency) is the driver, via the
    `postgresql+psycopg://` dialect.
  * No engine or session is created at import time -- `get_engine()` and
    `get_session_factory()` build lazily on first call and cache the
    result, so simply importing this module (e.g. from a test) never
    opens a network connection or requires a running database.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings

# Bare `postgres://`/`postgresql://` URLs (the docker-compose/.env.example
# default, and the historical Heroku-style scheme) don't select a driver.
# SQLAlchemy's psycopg (v3) dialect needs the explicit `+psycopg` suffix,
# so this maps both bare schemes onto it. Any URL that already names a
# driver (e.g. `postgresql+psycopg://`, `postgresql+asyncpg://`) or uses a
# non-Postgres scheme (e.g. `sqlite://`) is returned byte-for-byte
# unchanged -- this only fills in a missing driver, it never overrides
# one. Deliberately implemented as plain string-prefix matching rather
# than `urllib.parse.urlsplit`/`urlunsplit`: that round-trip silently
# drops the `//` on schemes urllib doesn't recognize as using a netloc
# (e.g. `sqlite:///:memory:` -> `sqlite:/:memory:`), which would make this
# "normalizer" corrupt URLs it was supposed to leave alone.
_BARE_POSTGRES_PREFIXES = ("postgres://", "postgresql://")


def normalize_database_url(url: str) -> str:
    """Return `url` with an explicit `+psycopg` driver on a bare Postgres scheme."""
    for prefix in _BARE_POSTGRES_PREFIXES:
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def build_engine(settings: Settings | None = None) -> Engine:
    """Construct a new sync SQLAlchemy engine from `Settings.database_url`.

    Does not connect -- `create_engine` only opens a connection lazily, on
    first actual use (a query, `.connect()`, etc.), which matches the
    "no live database required at import/startup time" requirement.
    """
    resolved_settings = settings or get_settings()
    url = normalize_database_url(resolved_settings.database_url)
    return create_engine(url, pool_pre_ping=True)


@lru_cache
def get_engine() -> Engine:
    """Process-wide cached engine, built lazily on first call."""
    return build_engine()


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Build a `sessionmaker` bound to `engine` (default: `get_engine()`)."""
    return sessionmaker(bind=engine or get_engine(), autoflush=False, expire_on_commit=False)
