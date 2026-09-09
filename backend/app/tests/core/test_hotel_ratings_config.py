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


# ---------------------------------------------------------------------------
# Step 182E: Tripadvisor Content API credential placeholders default to
# None and are not required for default tests. No automated Tripadvisor
# scraping exists or is implemented anywhere in this codebase; these
# fields exist only so a real, credentialed Tripadvisor adapter would
# have a typed place to read a key from, if one is ever implemented.
# ---------------------------------------------------------------------------


def test_tripadvisor_credential_placeholders_default_to_none() -> None:
    settings = Settings(_env_file=None)
    assert settings.tripadvisor_api_key is None
    assert settings.tripadvisor_api_base_url is None


def test_settings_constructs_without_any_tripadvisor_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.hotel_ratings_provider == "not_connected"
    assert settings.tripadvisor_api_key is None


def test_setting_tripadvisor_credentials_does_not_change_hotel_ratings_provider_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting a Tripadvisor credential alone must never select a
    Tripadvisor provider -- no such adapter exists in
    `app.providers.hotel_ratings.factory._SUPPORTED_PROVIDERS`, so
    `hotel_ratings_provider` still defaults to (and stays) not_connected
    unless explicitly changed."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("TRIPADVISOR_API_KEY", "placeholder-only-value")

    settings = Settings()

    assert settings.hotel_ratings_provider == "not_connected"
