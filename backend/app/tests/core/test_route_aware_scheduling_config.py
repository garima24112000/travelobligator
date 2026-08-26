from __future__ import annotations

import pytest

from app.core.config import Settings

# Tests for the route-aware scheduling application config surface (Step
# 166B, docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). See
# backend/app/tests/services/test_route_aware_sequencing_service.py for the
# apply_report behavior tests themselves.


def test_route_aware_scheduling_enabled_default_is_false() -> None:
    field_info = Settings.model_fields["route_aware_scheduling_enabled"]
    assert field_info.default is False
    assert field_info.alias == "ROUTE_AWARE_SCHEDULING_ENABLED"


def test_route_aware_scheduling_min_improvement_seconds_default() -> None:
    field_info = Settings.model_fields["route_aware_scheduling_min_improvement_seconds"]
    assert field_info.default == 0.0
    assert field_info.alias == "ROUTE_AWARE_SCHEDULING_MIN_IMPROVEMENT_SECONDS"


def test_settings_constructs_without_any_route_aware_scheduling_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No env var is required for this config surface -- constructing
    `Settings()` with no `.env`/env-var input still yields usable,
    conservative defaults (application disabled)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.route_aware_scheduling_enabled is False
    assert settings.route_aware_scheduling_min_improvement_seconds == 0.0


def test_route_aware_scheduling_enabled_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ROUTE_AWARE_SCHEDULING_ENABLED", "true")

    settings = Settings()

    assert settings.route_aware_scheduling_enabled is True


def test_route_aware_scheduling_min_improvement_seconds_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ROUTE_AWARE_SCHEDULING_MIN_IMPROVEMENT_SECONDS", "120")

    settings = Settings()

    assert settings.route_aware_scheduling_min_improvement_seconds == 120.0


def test_route_aware_scheduling_min_improvement_seconds_rejects_negative_value() -> None:
    with pytest.raises(ValueError):
        Settings(route_aware_scheduling_min_improvement_seconds=-1)
