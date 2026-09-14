from __future__ import annotations

import pytest

from app.core.config import Settings

# Tests for the async job foundation config surface (Step 186B,
# docs/14_backend_architecture.md section 116). None of these fields
# change any runtime behavior yet -- POST /trips/{trip_id}/generate and
# .../regenerate remain fully synchronous regardless of these values (see
# backend/app/tests/api/test_generation_progress.py and
# test_regenerate_guardrails.py for the still-unchanged behavior).


def test_async_generation_enabled_default_is_false() -> None:
    field_info = Settings.model_fields["async_generation_enabled"]
    assert field_info.default is False
    assert field_info.alias == "ASYNC_GENERATION_ENABLED"


def test_generation_job_ttl_seconds_default() -> None:
    field_info = Settings.model_fields["generation_job_ttl_seconds"]
    assert field_info.default == 86400
    assert field_info.alias == "GENERATION_JOB_TTL_SECONDS"


def test_generation_job_max_running_per_trip_default() -> None:
    field_info = Settings.model_fields["generation_job_max_running_per_trip"]
    assert field_info.default == 1
    assert field_info.alias == "GENERATION_JOB_MAX_RUNNING_PER_TRIP"


def test_generation_job_stale_after_seconds_default() -> None:
    field_info = Settings.model_fields["generation_job_stale_after_seconds"]
    assert field_info.default == 3600
    assert field_info.alias == "GENERATION_JOB_STALE_AFTER_SECONDS"


def test_settings_constructs_without_any_async_job_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No new env var is required for normal startup -- constructing
    `Settings()` with no `.env`/env-var input still yields the safe,
    current-behavior defaults."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.async_generation_enabled is False
    assert settings.generation_job_ttl_seconds == 86400
    assert settings.generation_job_max_running_per_trip == 1
    assert settings.generation_job_stale_after_seconds == 3600


def test_async_generation_enabled_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")

    settings = Settings()

    assert settings.async_generation_enabled is True


def test_generation_job_ttl_seconds_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GENERATION_JOB_TTL_SECONDS", "3600")

    settings = Settings()

    assert settings.generation_job_ttl_seconds == 3600


def test_generation_job_max_running_per_trip_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GENERATION_JOB_MAX_RUNNING_PER_TRIP", "3")

    settings = Settings()

    assert settings.generation_job_max_running_per_trip == 3


def test_generation_job_ttl_seconds_rejects_zero_and_negative() -> None:
    with pytest.raises(ValueError):
        Settings(generation_job_ttl_seconds=0)
    with pytest.raises(ValueError):
        Settings(generation_job_ttl_seconds=-1)


def test_generation_job_max_running_per_trip_rejects_zero_and_negative() -> None:
    with pytest.raises(ValueError):
        Settings(generation_job_max_running_per_trip=0)
    with pytest.raises(ValueError):
        Settings(generation_job_max_running_per_trip=-1)


def test_generation_job_stale_after_seconds_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("GENERATION_JOB_STALE_AFTER_SECONDS", "120")

    settings = Settings()

    assert settings.generation_job_stale_after_seconds == 120


def test_generation_job_stale_after_seconds_rejects_zero_and_negative() -> None:
    with pytest.raises(ValueError):
        Settings(generation_job_stale_after_seconds=0)
    with pytest.raises(ValueError):
        Settings(generation_job_stale_after_seconds=-1)


def test_redis_url_does_not_imply_async_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting REDIS_URL to a real-looking value must never itself enable
    async generation or imply Redis is actually used -- REDIS_URL remains
    unread by any code path in backend/app/ today (see
    test_generation_job_repository_does_not_require_redis for the
    matching repository-level proof, and README.md's "Current Status"
    section for the documented invariant)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("REDIS_URL", "redis://some-real-host:6379/0")

    settings = Settings()

    assert settings.redis_url == "redis://some-real-host:6379/0"
    assert settings.async_generation_enabled is False
