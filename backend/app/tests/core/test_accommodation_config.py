from __future__ import annotations

import pytest

from app.core.config import Settings

# Tests for the accommodation provider factory's config surface (Step
# 167B, docs/12_provider_architecture.md). See
# backend/app/tests/providers/test_not_connected_accommodation_provider.py
# and test_accommodation_factory.py for the provider/factory behavior
# tests themselves.


def test_accommodation_provider_default_is_not_connected() -> None:
    field_info = Settings.model_fields["accommodation_provider"]
    assert field_info.default == "not_connected"
    assert field_info.alias == "ACCOMMODATION_PROVIDER"


def test_settings_constructs_without_any_accommodation_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API key, token, or credential is required for the accommodation
    config surface -- constructing `Settings()` with no `.env`/env-var
    input still yields usable, conservative defaults (lodging inventory
    not connected)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.accommodation_provider == "not_connected"


def test_accommodation_provider_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ACCOMMODATION_PROVIDER", "some_future_provider")

    settings = Settings()

    assert settings.accommodation_provider == "some_future_provider"
