from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

# Section 200A: the persistence selector contract. Production default is
# PostgreSQL; local_json is an explicit development/test fallback; an unknown
# value is an error (never a silent substitute); DATABASE_URL alone selects
# nothing. (Ordinary tests run with PERSISTENCE_BACKEND=local_json chosen
# explicitly by conftest -- see tests/core/test_persistence_default_200a.py.)


def test_persistence_backend_default_is_postgres() -> None:
    field_info = Settings.model_fields["persistence_backend"]
    assert field_info.default == "postgres"
    assert field_info.alias == "PERSISTENCE_BACKEND"


def test_settings_constructs_without_any_persistence_env_var_and_defaults_to_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PERSISTENCE_BACKEND", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.persistence_backend == "postgres"
    assert settings.database_url is None  # no built-in credentials/default URL


@pytest.mark.parametrize("value", ["local_json", "postgres"])
def test_persistence_backend_accepts_exactly_the_two_documented_values(value: str) -> None:
    assert Settings(_env_file=None, PERSISTENCE_BACKEND=value).persistence_backend == value


@pytest.mark.parametrize("value,expected", [("POSTGRES", "postgres"), (" local_json ", "local_json")])
def test_persistence_backend_is_case_and_whitespace_normalised(value: str, expected: str) -> None:
    assert Settings(_env_file=None, PERSISTENCE_BACKEND=value).persistence_backend == expected


@pytest.mark.parametrize("value", ["made_up", "", "sqlite", "memory", "postgresql"])
def test_unrecognised_persistence_backend_is_a_configuration_error_not_a_silent_fallback(value: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, PERSISTENCE_BACKEND=value)
    assert "PERSISTENCE_BACKEND must be" in str(excinfo.value)


def test_database_url_alone_selects_nothing_the_default_stays_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PERSISTENCE_BACKEND", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://someone:secret@example.com:5432/somedb")

    settings = Settings(_env_file=None)

    assert settings.persistence_backend == "postgres"


def test_database_url_never_appears_in_a_settings_repr() -> None:
    settings = Settings(_env_file=None, DATABASE_URL="postgresql://someone:s3cretpw@example.com:5432/somedb")
    assert "s3cretpw" not in repr(settings) and "s3cretpw" not in str(settings)
