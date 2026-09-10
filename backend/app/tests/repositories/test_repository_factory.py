from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.repositories import factory as factory_module
from app.repositories.planning_state_repository import (
    planning_state_repository as local_planning_state_repository,
)
from app.repositories.trip_repository import trip_repository as local_trip_repository

# Tests for the Step 183D repository factory
# (backend/app/repositories/factory.py). None of these require a live
# Postgres: constructing a Postgres repository never connects (SQLAlchemy
# `create_engine`/`Session` are both lazy, see app/db/session.py) -- only
# an actual query would, and no test here issues one.


def test_factory_module_import_does_not_connect() -> None:
    """Merely importing this module must never construct an engine or
    open a connection -- it has no module-level Settings()/engine call."""
    import importlib
    import sys

    sys.modules.pop("app.repositories.factory", None)
    module = importlib.import_module("app.repositories.factory")

    assert hasattr(module, "get_trip_repository")
    assert hasattr(module, "get_planning_state_repository")


def test_get_trip_repository_returns_local_singleton_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    assert factory_module.get_trip_repository() is local_trip_repository


def test_get_planning_state_repository_returns_local_singleton_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    assert factory_module.get_planning_state_repository() is local_planning_state_repository


def test_database_url_alone_does_not_select_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matches Settings.persistence_backend's own contract (Step 183B):
    a real-looking DATABASE_URL with no explicit PERSISTENCE_BACKEND=postgres
    must never flip the factory over to the Postgres repositories."""
    monkeypatch.setattr(
        factory_module,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            database_url="postgresql://real_user:real_pass@real-host:5432/real_db",
        ),
    )

    assert factory_module.get_trip_repository() is local_trip_repository
    assert factory_module.get_planning_state_repository() is local_planning_state_repository


def test_factory_resolution_ignores_a_present_dotenv_file_selecting_postgres(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the exact Step 183B-FIX scenario for this new factory:
    a developer's local `.env` selecting Postgres must not leak into the
    factory's decision during pytest -- get_settings() (app/core/config.py)
    already skips dotenv under TRAVELOB_TEST_MODE, and this factory always
    calls the real get_settings() (not a cached, import-time value), so
    this should hold without any factory-specific isolation code."""
    fake_env = tmp_path / ".env"
    fake_env.write_text(
        "PERSISTENCE_BACKEND=postgres\n"
        "DATABASE_URL=postgresql://someone:secret@example.invalid:5432/somedb\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    from app.core.config import get_settings

    get_settings.cache_clear()

    assert factory_module.get_trip_repository() is local_trip_repository
    assert factory_module.get_planning_state_repository() is local_planning_state_repository

    get_settings.cache_clear()


def test_factory_returns_postgres_trip_repository_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting postgres never connects on its own -- constructing
    PostgresTripRepository only builds a lazy engine/session factory."""
    from app.repositories.postgres_trip_repository import PostgresTripRepository

    monkeypatch.setattr(
        factory_module,
        "get_settings",
        lambda: Settings(_env_file=None, persistence_backend="postgres"),
    )
    factory_module._postgres_trip_repository.cache_clear()

    repo = factory_module.get_trip_repository()

    assert isinstance(repo, PostgresTripRepository)
    # Cached: a second call while still selected as postgres returns the
    # exact same instance, matching get_engine()'s established pattern.
    assert factory_module.get_trip_repository() is repo

    factory_module._postgres_trip_repository.cache_clear()


def test_factory_returns_postgres_planning_state_repository_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.repositories.postgres_planning_state_repository import (
        PostgresPlanningStateRepository,
    )

    monkeypatch.setattr(
        factory_module,
        "get_settings",
        lambda: Settings(_env_file=None, persistence_backend="postgres"),
    )
    factory_module._postgres_planning_state_repository.cache_clear()

    repo = factory_module.get_planning_state_repository()

    assert isinstance(repo, PostgresPlanningStateRepository)
    assert factory_module.get_planning_state_repository() is repo

    factory_module._postgres_planning_state_repository.cache_clear()
