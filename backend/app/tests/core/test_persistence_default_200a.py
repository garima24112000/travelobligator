from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core import persistence
from app.core.config import Settings, get_settings
from app.core.persistence import (
    PersistenceConfigurationError,
    PersistenceUnavailableError,
    check_postgres_ready,
    require_database_url,
    startup_persistence_check,
    validate_persistence_configuration,
)
from app.repositories import factory as factory_module

# Section 200A acceptance tests A-G (+ no local-state creation in postgres mode).

_SECRET = "s3cretpw"
_URL = f"postgresql://someone:{_SECRET}@127.0.0.1:1/somedb"  # port 1: connection refused immediately


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)


@pytest.fixture()
def postgres_selected_but_unreachable(monkeypatch: pytest.MonkeyPatch):
    """PERSISTENCE_BACKEND=postgres + a refused-connection DATABASE_URL, applied the way a real
    process sees it (environment), with every cache reset around the test."""
    from app.db.session import get_engine

    def _reset() -> None:
        get_settings.cache_clear()
        get_engine.cache_clear()
        for cache in (
            factory_module._postgres_trip_repository,
            factory_module._postgres_planning_state_repository,
            factory_module._postgres_user_repository,
            factory_module._postgres_job_repository,
            factory_module._postgres_lineage_repository,
        ):
            cache.cache_clear()

    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", _URL)
    _reset()
    yield get_settings()
    _reset()


# -- F: tests request their backend explicitly; production default differs ----------------


def test_F_ordinary_hermetic_tests_explicitly_run_on_local_json() -> None:
    assert os.environ["PERSISTENCE_BACKEND"] == "local_json"  # set by conftest's explicit fixture
    assert get_settings().persistence_backend == "local_json"
    assert "DATABASE_URL" not in os.environ


def test_A_production_default_with_the_test_selection_removed_is_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSISTENCE_BACKEND")
    get_settings.cache_clear()
    assert get_settings().persistence_backend == "postgres"
    get_settings.cache_clear()


# -- B: explicit local_json still works --------------------------------------------------------


def test_B_explicit_local_json_selects_the_local_repositories_and_needs_no_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory_module, "get_settings", lambda: _settings(PERSISTENCE_BACKEND="local_json"))
    assert factory_module.get_trip_repository() is factory_module._local_trip_repository
    assert factory_module.get_planning_state_repository() is factory_module._local_planning_state_repository
    assert factory_module.get_user_repository() is factory_module._local_user_repository
    assert factory_module.get_job_repository() is factory_module._local_job_repository
    assert factory_module.get_lineage_repository() is factory_module._local_lineage_repository
    validate_persistence_configuration(_settings(PERSISTENCE_BACKEND="local_json"))  # nothing to verify


# -- C / D: postgres + missing / malformed DATABASE_URL fails safely ---------------------------


def test_C_postgres_without_database_url_fails_with_a_fixed_message() -> None:
    with pytest.raises(PersistenceConfigurationError) as excinfo:
        validate_persistence_configuration(_settings(PERSISTENCE_BACKEND="postgres"))
    text = str(excinfo.value)
    assert "requires DATABASE_URL" in text and "PERSISTENCE_BACKEND=local_json" in text
    with pytest.raises(PersistenceConfigurationError):
        startup_persistence_check(_settings(PERSISTENCE_BACKEND="postgres"))


@pytest.mark.parametrize(
    "bad",
    [
        "not a url",
        "sqlite:///:memory:",
        "sqlite:///tmp/x.db",
        "mysql://u:%s@h/db" % _SECRET,
        f"postgresql://someone:{_SECRET}@/",  # no host, no database
        f"postgresql://someone:{_SECRET}@host",  # no database name
        "   ",
    ],
)
def test_D_postgres_with_a_malformed_or_non_postgres_url_fails_safely_without_echoing_it(bad: str) -> None:
    with pytest.raises(PersistenceConfigurationError) as excinfo:
        require_database_url(_settings(PERSISTENCE_BACKEND="postgres", DATABASE_URL=bad))
    assert _SECRET not in str(excinfo.value)
    assert bad.strip() == "" or bad not in str(excinfo.value)


def test_engine_construction_never_creates_a_default_or_sqlite_database() -> None:
    from app.db.session import build_engine

    with pytest.raises(PersistenceConfigurationError):
        build_engine(_settings(PERSISTENCE_BACKEND="postgres"))
    with pytest.raises(PersistenceConfigurationError):
        build_engine(_settings(PERSISTENCE_BACKEND="postgres", DATABASE_URL="sqlite:///:memory:"))


# -- E: connection failure never selects local_json ---------------------------------------------


def test_E_an_unreachable_postgres_raises_a_safe_error_and_never_switches_backend(
    postgres_selected_but_unreachable: Settings,
) -> None:
    settings = postgres_selected_but_unreachable
    with pytest.raises(PersistenceUnavailableError) as excinfo:
        check_postgres_ready(settings)
    text = str(excinfo.value)
    assert "NOT switched" in text and "local_json" not in text.replace("NOT switched to another backend", "")
    for leaked in (_SECRET, "someone", "127.0.0.1", "somedb", "postgresql://"):
        assert leaked not in text
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__  # driver text dropped

    with pytest.raises(PersistenceUnavailableError):
        startup_persistence_check(settings)

    # ...and the factory still resolves POSTGRES repositories for that same configuration.
    from app.repositories.postgres_trip_repository import PostgresTripRepository

    assert isinstance(factory_module.get_trip_repository(), PostgresTripRepository)
    assert factory_module.get_trip_repository() is not factory_module._local_trip_repository


def test_E_a_failing_postgres_call_at_runtime_raises_and_never_touches_local_storage(
    postgres_selected_but_unreachable: Settings,
) -> None:
    from sqlalchemy.exc import OperationalError

    repo = factory_module.get_trip_repository()
    local_before = factory_module._local_trip_repository._trips.copy()

    with pytest.raises(OperationalError):
        repo.list_by_owner_id("user_x")

    assert factory_module._local_trip_repository._trips == local_before  # nothing written locally


# -- G: DATABASE_URL by itself cannot create ambiguous mixed behaviour ---------------------------


def test_G_database_url_alone_means_postgres_for_every_repository_never_a_mix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSISTENCE_BACKEND", raising=False)
    monkeypatch.setenv("DATABASE_URL", _URL)
    get_settings.cache_clear()
    monkeypatch.setattr(factory_module, "get_settings", get_settings)
    for cache in (
        factory_module._postgres_trip_repository,
        factory_module._postgres_planning_state_repository,
        factory_module._postgres_user_repository,
        factory_module._postgres_job_repository,
        factory_module._postgres_lineage_repository,
    ):
        cache.cache_clear()

    repos = [
        factory_module.get_trip_repository(),
        factory_module.get_planning_state_repository(),
        factory_module.get_user_repository(),
        factory_module.get_job_repository(),
        factory_module.get_lineage_repository(),
    ]

    assert all(type(r).__name__.startswith("Postgres") for r in repos)
    get_settings.cache_clear()
    for cache in (
        factory_module._postgres_trip_repository,
        factory_module._postgres_planning_state_repository,
        factory_module._postgres_user_repository,
        factory_module._postgres_job_repository,
        factory_module._postgres_lineage_repository,
    ):
        cache.cache_clear()


def test_every_repository_kind_switches_together_on_the_single_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    getters = [n for n, f in inspect.getmembers(factory_module, inspect.isfunction) if n.startswith("get_") and n.endswith("_repository")]
    assert sorted(getters) == [
        "get_job_repository",
        "get_lineage_repository",
        "get_planning_state_repository",
        "get_trip_repository",
        "get_user_repository",
    ]
    for name in getters:
        assert "persistence_backend" in inspect.getsource(getattr(factory_module, name))


def test_no_service_binds_a_local_json_repository_singleton_directly() -> None:
    """Regression guard: a module-level service that binds the Local JSON singleton at
    import time writes to Local JSON even in Postgres mode (200A fixed one such binding
    in TargetedRegenerationApplicationService)."""
    root = Path(persistence.__file__).resolve().parents[1]
    allowed = {"repositories", "tests", "storage"}
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).parts
        if rel[0] in allowed:
            continue
        text = path.read_text()
        for needle in (
            "from app.repositories.planning_state_repository import planning_state_repository",
            "from app.repositories.trip_repository import trip_repository",
            "from app.repositories.user_repository import user_repository",
            "from app.repositories.job_repository import job_repository",
            "from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository",
        ):
            if needle in text:
                offenders.append((str(path.relative_to(root)), needle))
    assert offenders == []


def test_targeted_regeneration_service_follows_the_factory_not_a_bound_local_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.targeted_regeneration_application_service import (
        TargetedRegenerationApplicationService,
    )

    sentinel = object()
    monkeypatch.setattr(
        "app.services.targeted_regeneration_application_service.get_planning_state_repository", lambda: sentinel
    )
    assert TargetedRegenerationApplicationService().planning_state_repository is sentinel
    assert TargetedRegenerationApplicationService(planning_state_repository="explicit").planning_state_repository == "explicit"


# -- migration head and startup policy -----------------------------------------------------------


def test_there_is_exactly_one_alembic_head() -> None:
    assert len(persistence.alembic_head_revisions()) == 1


def test_startup_check_is_wired_into_the_application_lifespan() -> None:
    import inspect

    from app import main

    assert "startup_persistence_check" in inspect.getsource(main.lifespan)


def test_no_request_time_or_startup_code_runs_migrations() -> None:
    import ast

    root = Path(persistence.__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name == "run_migrations.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "create_all", path
                assert not (node.func.attr == "upgrade" and getattr(node.func.value, "id", "") == "command"), path


# -- no local artifact in postgres mode ----------------------------------------------------------


def test_postgres_selection_and_failure_create_no_local_json_state_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, postgres_selected_but_unreachable: Settings
) -> None:
    state_file = tmp_path / "must_not_exist.json"
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(state_file))
    get_settings.cache_clear()
    with pytest.raises(PersistenceUnavailableError):
        startup_persistence_check(get_settings())
    factory_module.get_trip_repository()
    factory_module.get_user_repository()
    assert not state_file.exists() and list(tmp_path.iterdir()) == []


# -- runtime database failures reach the client safely ---------------------------------------------


def test_a_database_error_during_a_request_returns_a_fixed_503_without_connection_details(
    postgres_selected_but_unreachable: Settings,
) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/auth/login", json={"email": "someone@example.com", "password": "password123"})

    assert response.status_code == 503
    body = response.text
    assert "PERSISTENCE_UNAVAILABLE" in body and "temporarily unavailable" in body
    for leaked in (_SECRET, "someone", "127.0.0.1", "somedb", "postgresql", "OperationalError", "psycopg"):
        assert leaked not in body.replace("someone@example.com", "")


def test_the_api_never_exposes_the_database_url(postgres_selected_but_unreachable: Settings) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    for path in ("/health", "/openapi.json"):
        text = client.get(path).text
        assert _SECRET not in text and "127.0.0.1" not in text, path
        if path == "/health":
            assert "DATABASE_URL" not in text and "database_url" not in text
