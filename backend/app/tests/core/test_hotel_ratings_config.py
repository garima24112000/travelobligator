from __future__ import annotations

import pytest

from app.core.config import Settings

# Tests for the hotel ratings provider factory's config surface (Step
# 177B). See backend/app/tests/providers/test_hotel_ratings_provider.py
# and test_hotel_ratings_factory.py for the provider/factory behavior
# tests themselves.


def test_hotel_ratings_provider_default_is_not_connected() -> None:
    """As of Step 177B, the only implemented hotel ratings provider is the
    always-`not_connected` `NotConnectedHotelRatingsProvider` -- no real
    Google Places/Tripadvisor/Amadeus/Yelp integration exists yet."""
    field_info = Settings.model_fields["hotel_ratings_provider"]
    assert field_info.default == "not_connected"
    assert field_info.alias == "HOTEL_RATINGS_PROVIDER"


def test_settings_constructs_without_any_hotel_ratings_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API key, token, or credential is required for the hotel ratings
    config surface -- constructing `Settings()` with no `.env`/env-var
    input still yields a usable, safe default: not_connected, never a
    fabricated rating."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.hotel_ratings_provider == "not_connected"


def test_hotel_ratings_provider_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("HOTEL_RATINGS_PROVIDER", "some_future_provider")

    settings = Settings()

    assert settings.hotel_ratings_provider == "some_future_provider"
