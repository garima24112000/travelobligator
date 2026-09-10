from __future__ import annotations

import pytest

from app.core.config import Settings

# Tests for the persistence backend config gate (Step 183B,
# docs/14_backend_architecture.md). This field currently has NO effect on
# runtime behavior -- see backend/app/tests/repositories/test_persistence.py
# for proof the local-JSON repositories still work unchanged regardless of
# this value.


def test_persistence_backend_default_is_local_json() -> None:
    field_info = Settings.model_fields["persistence_backend"]
    assert field_info.default == "local_json"
    assert field_info.alias == "PERSISTENCE_BACKEND"


def test_settings_constructs_without_any_persistence_backend_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.persistence_backend == "local_json"


def test_persistence_backend_accepts_postgres_as_opt_in_value() -> None:
    settings = Settings(_env_file=None, persistence_backend="postgres")
    assert settings.persistence_backend == "postgres"


@pytest.mark.parametrize("value", ["made_up", "POSTGRES", "", "postgres "])
def test_persistence_backend_falls_back_to_local_json_for_unrecognized_values(
    value: str,
) -> None:
    settings = Settings(_env_file=None, persistence_backend=value)
    assert settings.persistence_backend == "local_json"


def test_database_url_alone_does_not_change_persistence_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting DATABASE_URL must never silently switch persistence to
    Postgres -- only an explicit PERSISTENCE_BACKEND=postgres does."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://someone:secret@example.com:5432/somedb")

    settings = Settings()

    assert settings.persistence_backend == "local_json"
