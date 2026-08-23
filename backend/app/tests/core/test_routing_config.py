from __future__ import annotations

import pytest

from app.core.config import Settings

# Tests for the routing provider skeleton's config surface (Step 165A,
# docs/12_provider_architecture.md section 31). See
# backend/app/tests/providers/test_routing_provider.py and
# test_osrm_adapter.py for the provider/adapter behavior tests themselves.


def test_routing_provider_default_is_not_connected() -> None:
    field_info = Settings.model_fields["routing_provider"]
    assert field_info.default == "not_connected"
    assert field_info.alias == "ROUTING_PROVIDER"


def test_osrm_base_url_default_is_unset() -> None:
    field_info = Settings.model_fields["osrm_base_url"]
    assert field_info.default is None
    assert field_info.alias == "OSRM_BASE_URL"


def test_osrm_timeout_seconds_default() -> None:
    field_info = Settings.model_fields["osrm_timeout_seconds"]
    assert field_info.default == 15.0
    assert field_info.alias == "OSRM_TIMEOUT_SECONDS"


def test_osrm_profile_default_is_driving() -> None:
    field_info = Settings.model_fields["osrm_profile"]
    assert field_info.default == "driving"
    assert field_info.alias == "OSRM_PROFILE"


def test_settings_constructs_without_any_routing_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """No API key, token, or credential is required for the routing config
    surface -- constructing `Settings()` with no `.env`/env-var input still
    yields usable, conservative defaults (routing disabled)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.routing_provider == "not_connected"
    assert settings.osrm_base_url is None
    assert settings.osrm_timeout_seconds == 15.0
    assert settings.osrm_profile == "driving"


def test_osrm_base_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("OSRM_BASE_URL", "https://osrm.example.test")

    settings = Settings()

    assert settings.osrm_base_url == "https://osrm.example.test"


def test_osrm_timeout_seconds_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("OSRM_TIMEOUT_SECONDS", "5")

    settings = Settings()

    assert settings.osrm_timeout_seconds == 5.0


def test_osrm_timeout_seconds_rejects_negative_value() -> None:
    with pytest.raises(ValueError):
        Settings(osrm_timeout_seconds=-1)


def test_osrm_profile_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("OSRM_PROFILE", "walking")

    settings = Settings()

    assert settings.osrm_profile == "walking"


def test_routing_provider_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ROUTING_PROVIDER", "osrm")

    settings = Settings()

    assert settings.routing_provider == "osrm"


# OSRM route cache TTL config (Step 165C, docs/12_provider_architecture.md
# "Provider Cache Foundation" section). See test_osrm_adapter.py for the
# behavioral cache tests themselves.


def test_osrm_route_cache_ttl_seconds_default() -> None:
    field_info = Settings.model_fields["osrm_route_cache_ttl_seconds"]
    assert field_info.default == 86400
    assert field_info.alias == "OSRM_ROUTE_CACHE_TTL_SECONDS"


def test_osrm_route_cache_ttl_seconds_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("OSRM_ROUTE_CACHE_TTL_SECONDS", "60")

    settings = Settings()

    assert settings.osrm_route_cache_ttl_seconds == 60


def test_osrm_route_cache_ttl_seconds_rejects_negative_value() -> None:
    with pytest.raises(ValueError):
        Settings(osrm_route_cache_ttl_seconds=-1)
