from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.core.config import Settings

# Tests for the provider cache foundation's config surface (Step 164A) and
# the "not wired into any provider adapter yet" boundary. See
# backend/app/tests/repositories/test_provider_cache_store.py for the
# ProviderCacheStore behavior tests themselves.


# ---------------------------------------------------------------------------
# Config defaults, and no API key/token is required to construct or use
# either the store or the settings surface.
# ---------------------------------------------------------------------------


def test_provider_cache_path_default() -> None:
    field_info = Settings.model_fields["provider_cache_path"]
    assert field_info.default == ".data/provider_cache.sqlite3"
    assert field_info.alias == "PROVIDER_CACHE_PATH"


def test_provider_cache_enabled_default_is_true() -> None:
    field_info = Settings.model_fields["provider_cache_enabled"]
    assert field_info.default is True
    assert field_info.alias == "PROVIDER_CACHE_ENABLED"


def test_settings_constructs_without_any_provider_cache_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API key, token, or credential is required for the provider cache
    config surface -- constructing `Settings()` with no `.env`/env-var
    input still yields usable defaults.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.provider_cache_path == ".data/provider_cache.sqlite3"
    assert settings.provider_cache_enabled is True


def test_resolved_provider_cache_path_is_absolute_and_under_backend_root() -> None:
    settings = Settings(provider_cache_path=".data/provider_cache.sqlite3")
    resolved = settings.resolved_provider_cache_path()

    assert resolved.is_absolute()
    assert resolved.name == "provider_cache.sqlite3"


def test_resolved_provider_cache_path_respects_absolute_override(
    tmp_path: Path,
) -> None:
    absolute_path = tmp_path / "custom_cache.sqlite3"
    settings = Settings(provider_cache_path=str(absolute_path))

    assert settings.resolved_provider_cache_path() == absolute_path


# ---------------------------------------------------------------------------
# open_meteo_cache_ttl_seconds (Step 164B): default, env override, and
# negative-value rejection.
# ---------------------------------------------------------------------------


def test_open_meteo_cache_ttl_seconds_default() -> None:
    field_info = Settings.model_fields["open_meteo_cache_ttl_seconds"]
    assert field_info.default == 3600
    assert field_info.alias == "OPEN_METEO_CACHE_TTL_SECONDS"


def test_open_meteo_cache_ttl_seconds_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("OPEN_METEO_CACHE_TTL_SECONDS", "120")

    settings = Settings()

    assert settings.open_meteo_cache_ttl_seconds == 120


def test_open_meteo_cache_ttl_seconds_rejects_negative_value() -> None:
    with pytest.raises(ValueError):
        Settings(open_meteo_cache_ttl_seconds=-1)


# ---------------------------------------------------------------------------
# nager_date_cache_ttl_seconds (Step 164C): default, env override, and
# negative-value rejection.
# ---------------------------------------------------------------------------


def test_nager_date_cache_ttl_seconds_default() -> None:
    field_info = Settings.model_fields["nager_date_cache_ttl_seconds"]
    assert field_info.default == 2592000
    assert field_info.alias == "NAGER_DATE_CACHE_TTL_SECONDS"


def test_nager_date_cache_ttl_seconds_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("NAGER_DATE_CACHE_TTL_SECONDS", "86400")

    settings = Settings()

    assert settings.nager_date_cache_ttl_seconds == 86400


def test_nager_date_cache_ttl_seconds_rejects_negative_value() -> None:
    with pytest.raises(ValueError):
        Settings(nager_date_cache_ttl_seconds=-1)


# ---------------------------------------------------------------------------
# 15. Open-Meteo (Step 164B) and Nager.Date (Step 164C) are wired to the
# provider cache. OSM and Frankfurter remain unwired -- Step 164A's
# foundation-only boundary still holds for every other provider.
# ---------------------------------------------------------------------------


def _unwired_adapter_modules():
    import app.providers.currency.frankfurter_adapter as frankfurter_module
    import app.providers.places.openstreetmap_adapter as osm_module

    return [osm_module, frankfurter_module]


def _wired_adapter_modules():
    import app.providers.holidays.nager_date_adapter as nager_module
    import app.providers.weather.open_meteo_adapter as open_meteo_module

    return [open_meteo_module, nager_module]


def test_no_non_weather_non_holiday_provider_adapter_imports_provider_cache_store() -> None:
    for module in _unwired_adapter_modules():
        source = inspect.getsource(module)
        assert "provider_cache_store" not in source
        assert "ProviderCacheStore" not in source
        assert "get_provider_cache_store" not in source


def test_open_meteo_and_nager_date_adapters_import_provider_cache_store() -> None:
    """Step 164B wired Open-Meteo, Step 164C wired Nager.Date -- both are
    now provider cache consumers."""
    for module in _wired_adapter_modules():
        source = inspect.getsource(module)
        assert "provider_cache_store" in source
        assert "ProviderCacheStore" in source
        assert "get_provider_cache_store" in source


def test_provider_gateway_does_not_import_provider_cache_store() -> None:
    import app.providers.gateway as gateway_module

    source = inspect.getsource(gateway_module)
    assert "provider_cache_store" not in source
    assert "ProviderCacheStore" not in source


def test_planning_orchestrator_does_not_import_provider_cache_store() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "provider_cache_store" not in source
    assert "ProviderCacheStore" not in source


def test_provider_cache_store_module_has_no_disallowed_vendor_or_network_imports() -> (
    None
):
    """The cache foundation itself never calls a real network/AI/scraping
    dependency -- it's Python stdlib (sqlite3/json/hashlib) only.
    """
    import ast

    import app.storage.provider_cache_store as provider_cache_module

    source = inspect.getsource(provider_cache_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = (
        "httpx",
        "requests",
        "groq",
        "anthropic",
        "openai",
        "gemini",
        "langgraph",
        "langsmith",
        "kiwi",
        "mcp",
        "beautifulsoup",
        "scrapy",
        "selenium",
        "playwright",
    )
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"
