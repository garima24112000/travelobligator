from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings

# Tests for the Step 168A scraping policy config surface, with defaults
# flipped to on in Step 168F (docs/12_provider_architecture.md). See
# backend/app/tests/models/test_scraping_models.py for the
# policy/registry/provenance model behavior tests themselves, and
# backend/app/tests/providers/test_scraped_accommodation_adapter.py for
# the provider's own missing-file-stays-honest behavior.


def test_scraping_enabled_default_is_true() -> None:
    field_info = Settings.model_fields["scraping_enabled"]
    assert field_info.default is True
    assert field_info.alias == "SCRAPING_ENABLED"


def test_scraped_accommodation_provider_enabled_default_is_true() -> None:
    field_info = Settings.model_fields["scraped_accommodation_provider_enabled"]
    assert field_info.default is True
    assert field_info.alias == "SCRAPED_ACCOMMODATION_PROVIDER_ENABLED"


def test_scraping_default_rate_limit_seconds_default() -> None:
    field_info = Settings.model_fields["scraping_default_rate_limit_seconds"]
    assert field_info.default == 10
    assert field_info.alias == "SCRAPING_DEFAULT_RATE_LIMIT_SECONDS"


def test_scraped_accommodation_html_path_default() -> None:
    field_info = Settings.model_fields["scraped_accommodation_html_path"]
    assert field_info.default == ".data/manual_scrapes/accommodations.html"
    assert field_info.alias == "SCRAPED_ACCOMMODATION_HTML_PATH"


def test_settings_constructs_without_any_scraping_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No credential or explicit opt-in is required for the scraping
    config surface -- constructing `Settings()` with no `.env`/env-var
    input still yields the new default (scraped_local enabled), but that
    default never fabricates data: with no file at the default local
    path, the provider itself reports `unavailable` rather than a
    fabricated offer (see test_scraped_accommodation_adapter.py)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.scraping_enabled is True
    assert settings.scraped_accommodation_provider_enabled is True
    assert settings.scraping_default_rate_limit_seconds == 10
    assert settings.scraped_accommodation_html_path == ".data/manual_scrapes/accommodations.html"


def test_scraping_enabled_env_override_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirms opting back out is still possible, even though the
    default flipped to enabled in Step 168F."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SCRAPING_ENABLED", "false")

    settings = Settings()

    assert settings.scraping_enabled is False


def test_scraped_accommodation_provider_enabled_env_override_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SCRAPED_ACCOMMODATION_PROVIDER_ENABLED", "false")

    settings = Settings()

    assert settings.scraped_accommodation_provider_enabled is False


def test_scraping_default_rate_limit_seconds_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SCRAPING_DEFAULT_RATE_LIMIT_SECONDS", "30")

    settings = Settings()

    assert settings.scraping_default_rate_limit_seconds == 30


def test_scraping_default_rate_limit_seconds_rejects_negative_value() -> None:
    with pytest.raises(ValueError):
        Settings(scraping_default_rate_limit_seconds=-1)


def test_scraped_accommodation_html_path_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SCRAPED_ACCOMMODATION_HTML_PATH", "/tmp/example-override.html")

    settings = Settings()

    assert settings.scraped_accommodation_html_path == "/tmp/example-override.html"


def test_scraped_accommodation_html_path_can_be_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings(scraped_accommodation_html_path=None)

    assert settings.scraped_accommodation_html_path is None
    assert settings.resolved_scraped_accommodation_html_path() is None


def test_resolved_scraped_accommodation_html_path_is_relative_to_backend_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors `resolved_local_storage_path`/`resolved_provider_cache_path`
    -- the default relative path resolves against the backend project
    root, not the process's current working directory, so the same
    default file is found regardless of where the app was started from.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    resolved = settings.resolved_scraped_accommodation_html_path()

    assert resolved is not None
    assert resolved.is_absolute()
    assert resolved.parts[-3:] == (".data", "manual_scrapes", "accommodations.html")


def test_resolved_scraped_accommodation_html_path_respects_absolute_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    absolute_path = "/tmp/example-absolute-override.html"
    settings = Settings(scraped_accommodation_html_path=absolute_path)

    resolved = settings.resolved_scraped_accommodation_html_path()

    assert resolved == Path(absolute_path)
