from __future__ import annotations

from pathlib import Path

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


# ---------------------------------------------------------------------------
# Step 185B: config-only foundation for a future manual/local hotel-ratings
# ingestion adapter (Step 185E's job to actually build). Setting any of
# these today has zero runtime effect -- no adapter reads them yet, and
# `hotel_ratings_provider` above still only supports "not_connected".
# ---------------------------------------------------------------------------


def test_scraped_hotel_ratings_provider_enabled_default_is_true() -> None:
    field_info = Settings.model_fields["scraped_hotel_ratings_provider_enabled"]
    assert field_info.default is True
    assert field_info.alias == "SCRAPED_HOTEL_RATINGS_PROVIDER_ENABLED"


def test_hotel_ratings_manual_html_source_default_is_generic() -> None:
    field_info = Settings.model_fields["hotel_ratings_manual_html_source"]
    assert field_info.default == "generic"
    assert field_info.alias == "HOTEL_RATINGS_MANUAL_HTML_SOURCE"


def test_scraped_hotel_ratings_html_path_default() -> None:
    field_info = Settings.model_fields["scraped_hotel_ratings_html_path"]
    assert field_info.default == ".data/manual_scrapes/hotel_ratings.html"
    assert field_info.alias == "SCRAPED_HOTEL_RATINGS_HTML_PATH"


def test_settings_constructs_without_any_hotel_ratings_manual_ingestion_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.scraped_hotel_ratings_provider_enabled is True
    assert settings.hotel_ratings_manual_html_source == "generic"
    assert settings.scraped_hotel_ratings_html_path == ".data/manual_scrapes/hotel_ratings.html"
    # No adapter exists yet -- the real provider selection is untouched.
    assert settings.hotel_ratings_provider == "not_connected"


def test_scraped_hotel_ratings_provider_enabled_env_override_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SCRAPED_HOTEL_RATINGS_PROVIDER_ENABLED", "false")

    settings = Settings()

    assert settings.scraped_hotel_ratings_provider_enabled is False


@pytest.mark.parametrize(
    "value",
    ["generic", "tripadvisor", "google_places", "other"],
)
def test_hotel_ratings_manual_html_source_accepts_every_allowed_value(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("HOTEL_RATINGS_MANUAL_HTML_SOURCE", value)

    settings = Settings()

    assert settings.hotel_ratings_manual_html_source == value


def test_hotel_ratings_manual_html_source_unrecognized_value_falls_back_to_generic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("HOTEL_RATINGS_MANUAL_HTML_SOURCE", "not_a_real_source")

    settings = Settings()

    assert settings.hotel_ratings_manual_html_source == "generic"


def test_scraped_hotel_ratings_html_path_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SCRAPED_HOTEL_RATINGS_HTML_PATH", "/tmp/example-override.html")

    settings = Settings()

    assert settings.scraped_hotel_ratings_html_path == "/tmp/example-override.html"


def test_scraped_hotel_ratings_html_path_can_be_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings(scraped_hotel_ratings_html_path=None)

    assert settings.scraped_hotel_ratings_html_path is None
    assert settings.resolved_scraped_hotel_ratings_html_path() is None


def test_resolved_scraped_hotel_ratings_html_path_is_relative_to_backend_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    resolved = settings.resolved_scraped_hotel_ratings_html_path()

    assert resolved is not None
    assert resolved.is_absolute()
    assert resolved.parts[-3:] == (".data", "manual_scrapes", "hotel_ratings.html")


# ---------------------------------------------------------------------------
# Step 185E: hotel ratings provider now supports "scraped_local"/
# "manual_html", and per-source local file paths for multi-source
# ingestion, mirroring accommodation (185C) and flight (185D).
# ---------------------------------------------------------------------------


def test_scraped_hotel_ratings_source_id_and_name_defaults() -> None:
    source_id_field = Settings.model_fields["scraped_hotel_ratings_source_id"]
    source_name_field = Settings.model_fields["scraped_hotel_ratings_source_name"]
    assert source_id_field.default == "manual_local_scraped_hotel_ratings"
    assert source_id_field.alias == "SCRAPED_HOTEL_RATINGS_SOURCE_ID"
    assert source_name_field.default == "Manual local scraped hotel ratings source"
    assert source_name_field.alias == "SCRAPED_HOTEL_RATINGS_SOURCE_NAME"


def test_scraped_hotel_ratings_base_url_defaults_to_none() -> None:
    field_info = Settings.model_fields["scraped_hotel_ratings_base_url"]
    assert field_info.default is None
    assert field_info.alias == "SCRAPED_HOTEL_RATINGS_BASE_URL"


def test_scraped_hotel_ratings_cache_defaults() -> None:
    enabled_field = Settings.model_fields["scraped_hotel_ratings_cache_enabled"]
    ttl_field = Settings.model_fields["scraped_hotel_ratings_cache_ttl_seconds"]
    assert enabled_field.default is True
    assert enabled_field.alias == "SCRAPED_HOTEL_RATINGS_CACHE_ENABLED"
    assert ttl_field.default == 3600
    assert ttl_field.alias == "SCRAPED_HOTEL_RATINGS_CACHE_TTL_SECONDS"


@pytest.mark.parametrize(
    "field_name,alias,default_path",
    [
        (
            "scraped_hotel_ratings_html_path_tripadvisor",
            "SCRAPED_HOTEL_RATINGS_HTML_PATH_TRIPADVISOR",
            ".data/manual_scrapes/hotel_ratings_tripadvisor.html",
        ),
        (
            "scraped_hotel_ratings_html_path_google_places_ratings",
            "SCRAPED_HOTEL_RATINGS_HTML_PATH_GOOGLE_PLACES_RATINGS",
            ".data/manual_scrapes/hotel_ratings_google_places_ratings.html",
        ),
    ],
)
def test_per_source_hotel_ratings_html_path_default(
    field_name: str, alias: str, default_path: str
) -> None:
    field_info = Settings.model_fields[field_name]
    assert field_info.default == default_path
    assert field_info.alias == alias


def test_settings_constructs_without_any_per_source_hotel_ratings_path_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert (
        settings.scraped_hotel_ratings_html_path_tripadvisor
        == ".data/manual_scrapes/hotel_ratings_tripadvisor.html"
    )
    assert (
        settings.scraped_hotel_ratings_html_path_google_places_ratings
        == ".data/manual_scrapes/hotel_ratings_google_places_ratings.html"
    )


def test_per_source_hotel_ratings_html_path_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SCRAPED_HOTEL_RATINGS_HTML_PATH_TRIPADVISOR", "/tmp/tripadvisor-override.html")

    settings = Settings()

    assert settings.scraped_hotel_ratings_html_path_tripadvisor == "/tmp/tripadvisor-override.html"


def test_per_source_hotel_ratings_html_path_can_be_cleared() -> None:
    settings = Settings(
        _env_file=None,
        scraped_hotel_ratings_html_path_tripadvisor=None,
        scraped_hotel_ratings_html_path_google_places_ratings=None,
    )

    resolved = settings.resolved_scraped_hotel_ratings_html_paths_by_source()

    assert resolved == {"tripadvisor": None, "google_places_ratings": None}


def test_resolved_per_source_hotel_ratings_html_paths_are_relative_to_backend_root() -> None:
    settings = Settings(_env_file=None)

    resolved = settings.resolved_scraped_hotel_ratings_html_paths_by_source()

    assert resolved["tripadvisor"] is not None
    assert resolved["tripadvisor"].is_absolute()
    assert resolved["tripadvisor"].parts[-3:] == (
        ".data",
        "manual_scrapes",
        "hotel_ratings_tripadvisor.html",
    )
    assert resolved["google_places_ratings"].parts[-3:] == (
        ".data",
        "manual_scrapes",
        "hotel_ratings_google_places_ratings.html",
    )


def test_resolved_per_source_hotel_ratings_html_path_respects_absolute_override() -> None:
    settings = Settings(
        _env_file=None,
        scraped_hotel_ratings_html_path_tripadvisor="/tmp/absolute-tripadvisor.html",
    )

    resolved = settings.resolved_scraped_hotel_ratings_html_paths_by_source()

    assert resolved["tripadvisor"] == Path("/tmp/absolute-tripadvisor.html")


def test_per_source_hotel_ratings_paths_are_distinct_from_each_other_and_from_legacy_path() -> None:
    settings = Settings(_env_file=None)

    resolved = settings.resolved_scraped_hotel_ratings_html_paths_by_source()
    legacy = settings.resolved_scraped_hotel_ratings_html_path()

    all_paths = [legacy, resolved["tripadvisor"], resolved["google_places_ratings"]]
    assert len(all_paths) == len(set(all_paths))


def test_hotel_ratings_provider_accepts_scraped_local_and_manual_html_values() -> None:
    """Config itself never clamps `hotel_ratings_provider`'s value (like
    `accommodation_provider`/`flight_provider`) -- the factory is
    responsible for falling back to not_connected for anything it
    doesn't recognize."""
    assert Settings(_env_file=None, hotel_ratings_provider="scraped_local").hotel_ratings_provider == (
        "scraped_local"
    )
    assert Settings(_env_file=None, hotel_ratings_provider="manual_html").hotel_ratings_provider == (
        "manual_html"
    )
