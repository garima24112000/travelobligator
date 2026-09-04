from __future__ import annotations

import pytest

from app.core.config import Settings

# Tests for the accommodation provider factory's config surface (Step
# 167B, docs/12_provider_architecture.md). See
# backend/app/tests/providers/test_not_connected_accommodation_provider.py
# and test_accommodation_factory.py for the provider/factory behavior
# tests themselves.


def test_accommodation_provider_default_is_scraped_local() -> None:
    """As of Step 168F, the default accommodation provider is the
    disabled-nowhere-but-local-file-only `scraped_local` (Step 168C's
    `ScrapedAccommodationProvider`) -- never a real Booking/Expedia/
    Hotelbeds/Hostelworld/Amadeus/Vrbo/Airbnb integration, and never a
    live website fetch."""
    field_info = Settings.model_fields["accommodation_provider"]
    assert field_info.default == "scraped_local"
    assert field_info.alias == "ACCOMMODATION_PROVIDER"


def test_settings_constructs_without_any_accommodation_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API key, token, or credential is required for the accommodation
    config surface -- constructing `Settings()` with no `.env`/env-var
    input still yields usable, safe defaults: the scraped_local provider
    is selected, but with no local HTML file present it never fabricates
    an offer (see test_scraped_accommodation_adapter.py)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.accommodation_provider == "scraped_local"


def test_accommodation_provider_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ACCOMMODATION_PROVIDER", "some_future_provider")

    settings = Settings()

    assert settings.accommodation_provider == "some_future_provider"
