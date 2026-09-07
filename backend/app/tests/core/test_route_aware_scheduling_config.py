from __future__ import annotations

import pytest

from app.core.config import Settings

# Tests for the route-aware scheduling application config surface (Step
# 166B, docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). See
# backend/app/tests/services/test_route_aware_sequencing_service.py for the
# apply_report behavior tests themselves.


def test_route_aware_scheduling_enabled_default_is_true() -> None:
    """As of Step 172A, route-aware scheduling application is the default
    -- but apply_report's own safety contract (unchanged since Step 166B)
    is still the only thing that decides whether anything actually
    happens. With the default routing_provider="not_connected", no
    suggestion ever reaches status=success, so this default alone never
    changes a schedule in an environment with no routing provider
    configured."""
    field_info = Settings.model_fields["route_aware_scheduling_enabled"]
    assert field_info.default is True
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
    safe defaults (application enabled, but gated entirely by
    apply_report's own unchanged safety contract -- see
    test_route_aware_scheduling_enabled_default_is_true)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.route_aware_scheduling_enabled is True
    assert settings.route_aware_scheduling_min_improvement_seconds == 0.0


def test_route_aware_scheduling_can_be_explicitly_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit opt-out back to Step 166A's original shadow/report-only-
    forever behavior remains available, matching every other config
    flag's fallback convention in this codebase."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ROUTE_AWARE_SCHEDULING_ENABLED", "false")

    settings = Settings()

    assert settings.route_aware_scheduling_enabled is False


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
