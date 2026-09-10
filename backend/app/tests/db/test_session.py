from __future__ import annotations

import sys

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings

# Tests for the sync SQLAlchemy engine/session foundation (Step 183B,
# docs/14_backend_architecture.md). None of these tests require a running
# Postgres -- `create_engine`/`sessionmaker` never connect on their own,
# they only connect lazily on first actual query/`.connect()` call, which
# nothing here triggers.


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        (
            "postgresql://user:pass@localhost:5432/db",
            "postgresql+psycopg://user:pass@localhost:5432/db",
        ),
        (
            "postgres://user:pass@localhost:5432/db",
            "postgresql+psycopg://user:pass@localhost:5432/db",
        ),
        (
            "postgresql+psycopg://user:pass@localhost:5432/db",
            "postgresql+psycopg://user:pass@localhost:5432/db",
        ),
        (
            "postgresql+psycopg2://user:pass@localhost:5432/db",
            "postgresql+psycopg2://user:pass@localhost:5432/db",
        ),
        ("sqlite:///:memory:", "sqlite:///:memory:"),
    ],
)
def test_normalize_database_url(raw_url: str, expected: str) -> None:
    from app.db.session import normalize_database_url

    assert normalize_database_url(raw_url) == expected


def test_build_engine_does_not_connect() -> None:
    from app.db.session import build_engine

    settings = Settings(
        _env_file=None,
        database_url="postgresql://user:pass@240.0.0.1:1/db",
    )
    engine = build_engine(settings)

    assert isinstance(engine, Engine)
    assert engine.dialect.driver == "psycopg"


def test_get_engine_is_cached() -> None:
    from app.db.session import get_engine

    first = get_engine()
    second = get_engine()

    assert first is second


def test_get_session_factory_does_not_connect() -> None:
    from app.db.session import build_engine, get_session_factory

    settings = Settings(
        _env_file=None,
        database_url="postgresql://user:pass@240.0.0.1:1/db",
    )
    engine = build_engine(settings)
    factory = get_session_factory(engine)

    assert isinstance(factory, sessionmaker)

    session = factory()
    try:
        assert isinstance(session, Session)
    finally:
        session.close()


def test_importing_session_module_does_not_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing app.db.session must be side-effect-free: no engine/session
    is built at import time, only inside the lazy factory functions below.

    Points DATABASE_URL at an unreachable host/port before a fresh import,
    so if the module ever grew an eager `create_engine(...).connect()` at
    import time, this test would hang or raise instead of passing quickly.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@240.0.0.1:1/db")
    sys.modules.pop("app.db.session", None)

    module = __import__("app.db.session", fromlist=["session"])

    assert not hasattr(module, "engine")
    assert not hasattr(module, "SessionLocal")
    assert not hasattr(module, "settings")
