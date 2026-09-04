from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings

# Tests for the flight provider factory's config surface (Step 169B,
# docs/12_provider_architecture.md). See
# backend/app/tests/providers/test_not_connected_flight_provider.py,
# test_scraped_flight_provider.py, and test_flight_factory.py for the
# provider/factory behavior tests themselves.


# ---------------------------------------------------------------------------
# 1. Config default flight_provider is "scraped_local".
# ---------------------------------------------------------------------------


def test_flight_provider_default_is_scraped_local() -> None:
    """Matching accommodation's Step 168F decision: flight scraping is on
    by default -- never a real Amadeus/Duffel/Kiwi/Google Flights
    integration, and never a live website fetch."""
    field_info = Settings.model_fields["flight_provider"]
    assert field_info.default == "scraped_local"
    assert field_info.alias == "FLIGHT_PROVIDER"


def test_settings_constructs_without_any_flight_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API key, token, or credential is required for the flight config
    surface -- constructing `Settings()` with no `.env`/env-var input
    still yields usable, safe defaults: the scraped_local provider is
    selected, but with no local HTML file present (and no parser yet), it
    never fabricates an offer."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.flight_provider == "scraped_local"


def test_flight_provider_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("FLIGHT_PROVIDER", "some_future_provider")

    settings = Settings()

    assert settings.flight_provider == "some_future_provider"


# ---------------------------------------------------------------------------
# 2. Config default scraped_flight_provider_enabled is True.
# ---------------------------------------------------------------------------


def test_scraped_flight_provider_enabled_default_is_true() -> None:
    field_info = Settings.model_fields["scraped_flight_provider_enabled"]
    assert field_info.default is True
    assert field_info.alias == "SCRAPED_FLIGHT_PROVIDER_ENABLED"


def test_scraped_flight_provider_enabled_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SCRAPED_FLIGHT_PROVIDER_ENABLED", "false")

    settings = Settings()

    assert settings.scraped_flight_provider_enabled is False


# ---------------------------------------------------------------------------
# 3. Config default scraped_flight_html_path is
#    ".data/manual_scrapes/flights.html".
# ---------------------------------------------------------------------------


def test_scraped_flight_html_path_default() -> None:
    field_info = Settings.model_fields["scraped_flight_html_path"]
    assert field_info.default == ".data/manual_scrapes/flights.html"
    assert field_info.alias == "SCRAPED_FLIGHT_HTML_PATH"


def test_scraped_flight_source_id_and_name_defaults() -> None:
    assert Settings.model_fields["scraped_flight_source_id"].default == (
        "manual_local_scraped_flight"
    )
    assert Settings.model_fields["scraped_flight_source_name"].default == (
        "Manual local scraped flight source"
    )
    assert Settings.model_fields["scraped_flight_base_url"].default is None


# ---------------------------------------------------------------------------
# 4. resolved_scraped_flight_html_path resolves relative path
#    consistently, mirroring resolved_scraped_accommodation_html_path.
# ---------------------------------------------------------------------------


def test_resolved_scraped_flight_html_path_resolves_default_against_backend_root() -> None:
    settings = Settings(_env_file=None)
    resolved = settings.resolved_scraped_flight_html_path()
    assert resolved is not None
    assert resolved.is_absolute()
    assert resolved.as_posix().endswith(".data/manual_scrapes/flights.html")


def test_resolved_scraped_flight_html_path_returns_none_when_unset() -> None:
    settings = Settings(_env_file=None, scraped_flight_html_path=None)
    assert settings.resolved_scraped_flight_html_path() is None


def test_resolved_scraped_flight_html_path_preserves_absolute_path() -> None:
    absolute_path = "/tmp/some/absolute/flights.html"
    settings = Settings(_env_file=None, scraped_flight_html_path=absolute_path)
    resolved = settings.resolved_scraped_flight_html_path()
    assert resolved == Path(absolute_path)


def test_resolved_scraped_flight_html_path_matches_accommodation_resolution_style() -> None:
    """The relative default resolves the same way regardless of the
    process's current working directory -- mirroring
    resolved_scraped_accommodation_html_path's own contract."""
    settings = Settings(_env_file=None)
    flight_resolved = settings.resolved_scraped_flight_html_path()
    accommodation_resolved = settings.resolved_scraped_accommodation_html_path()
    assert flight_resolved is not None
    assert accommodation_resolved is not None
    assert flight_resolved.parent == accommodation_resolved.parent
