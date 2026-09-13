from __future__ import annotations

from pathlib import Path

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


# ---------------------------------------------------------------------------
# Step 182E: ACCOMMODATION_MANUAL_HTML_SOURCE is a cosmetic provenance
# label only -- it never selects a provider and always normalizes to a
# safe, known value.
# ---------------------------------------------------------------------------


def test_accommodation_manual_html_source_default_is_generic() -> None:
    field_info = Settings.model_fields["accommodation_manual_html_source"]
    assert field_info.default == "generic"
    assert field_info.alias == "ACCOMMODATION_MANUAL_HTML_SOURCE"


@pytest.mark.parametrize(
    "value", ["generic", "booking", "expedia", "hotelbeds", "hostelworld", "vrbo", "airbnb"]
)
def test_accommodation_manual_html_source_accepts_every_documented_value(value: str) -> None:
    settings = Settings(_env_file=None, accommodation_manual_html_source=value)
    assert settings.accommodation_manual_html_source == value


@pytest.mark.parametrize("value", ["made_up", "BOOKING", "", "airbnb "])
def test_accommodation_manual_html_source_falls_back_to_generic_for_unrecognized_values(
    value: str,
) -> None:
    settings = Settings(_env_file=None, accommodation_manual_html_source=value)
    assert settings.accommodation_manual_html_source == "generic"


# ---------------------------------------------------------------------------
# Step 182E: partner/paid-access accommodation provider credential
# placeholders default to None and are not required for default tests.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    [
        "booking_demand_api_key",
        "booking_demand_api_base_url",
        "expedia_rapid_api_key",
        "expedia_rapid_api_secret",
        "expedia_rapid_api_base_url",
        "hotelbeds_api_key",
        "hotelbeds_secret",
        "hotelbeds_api_base_url",
        "hostelworld_api_key",
        "hostelworld_api_base_url",
        "vrbo_partner_api_key",
        "vrbo_partner_api_base_url",
        "airbnb_partner_api_key",
        "airbnb_partner_api_base_url",
    ]
)
def test_partner_accommodation_credential_placeholders_default_to_none(field_name: str) -> None:
    settings = Settings(_env_file=None)
    assert getattr(settings, field_name) is None


def test_settings_constructs_without_any_partner_accommodation_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None of the new partner-provider placeholders are required for
    default app/test behavior -- constructing `Settings()` with no
    `.env`/env-var input still yields the exact same scraped_local
    default as before this step."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.accommodation_provider == "scraped_local"
    assert settings.booking_demand_api_key is None
    assert settings.expedia_rapid_api_key is None
    assert settings.hotelbeds_api_key is None
    assert settings.hostelworld_api_key is None
    assert settings.vrbo_partner_api_key is None
    assert settings.airbnb_partner_api_key is None


# ---------------------------------------------------------------------------
# Step 185C: independent per-source local file paths -- multi-source
# accommodation ingestion. Each defaults to its own real path (never
# `None`), so every source is "known and eligible" out of the box, but
# with no file actually present at any of these paths (the fresh-clone
# state), the provider honestly reports each one as missing -- see
# test_scraped_accommodation_adapter.py for that behavior.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name,alias,expected_default",
    [
        (
            "scraped_accommodation_html_path_booking",
            "SCRAPED_ACCOMMODATION_HTML_PATH_BOOKING",
            ".data/manual_scrapes/accommodations_booking.html",
        ),
        (
            "scraped_accommodation_html_path_expedia",
            "SCRAPED_ACCOMMODATION_HTML_PATH_EXPEDIA",
            ".data/manual_scrapes/accommodations_expedia.html",
        ),
        (
            "scraped_accommodation_html_path_hotelbeds",
            "SCRAPED_ACCOMMODATION_HTML_PATH_HOTELBEDS",
            ".data/manual_scrapes/accommodations_hotelbeds.html",
        ),
        (
            "scraped_accommodation_html_path_hostelworld",
            "SCRAPED_ACCOMMODATION_HTML_PATH_HOSTELWORLD",
            ".data/manual_scrapes/accommodations_hostelworld.html",
        ),
        (
            "scraped_accommodation_html_path_vrbo",
            "SCRAPED_ACCOMMODATION_HTML_PATH_VRBO",
            ".data/manual_scrapes/accommodations_vrbo.html",
        ),
        (
            "scraped_accommodation_html_path_airbnb",
            "SCRAPED_ACCOMMODATION_HTML_PATH_AIRBNB",
            ".data/manual_scrapes/accommodations_airbnb.html",
        ),
    ],
)
def test_per_source_html_path_default(field_name: str, alias: str, expected_default: str) -> None:
    field_info = Settings.model_fields[field_name]
    assert field_info.default == expected_default
    assert field_info.alias == alias


def test_settings_constructs_without_any_per_source_path_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.scraped_accommodation_html_path_booking == (
        ".data/manual_scrapes/accommodations_booking.html"
    )
    assert settings.scraped_accommodation_html_path_expedia == (
        ".data/manual_scrapes/accommodations_expedia.html"
    )
    assert settings.scraped_accommodation_html_path_hotelbeds == (
        ".data/manual_scrapes/accommodations_hotelbeds.html"
    )
    assert settings.scraped_accommodation_html_path_hostelworld == (
        ".data/manual_scrapes/accommodations_hostelworld.html"
    )
    assert settings.scraped_accommodation_html_path_vrbo == (
        ".data/manual_scrapes/accommodations_vrbo.html"
    )
    assert settings.scraped_accommodation_html_path_airbnb == (
        ".data/manual_scrapes/accommodations_airbnb.html"
    )
    # The pre-185C single-file setting is completely untouched.
    assert settings.scraped_accommodation_html_path == ".data/manual_scrapes/accommodations.html"
    assert settings.accommodation_manual_html_source == "generic"


def test_per_source_html_path_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SCRAPED_ACCOMMODATION_HTML_PATH_BOOKING", "/tmp/example-booking.html")

    settings = Settings()

    assert settings.scraped_accommodation_html_path_booking == "/tmp/example-booking.html"
    # Other sources are unaffected.
    assert settings.scraped_accommodation_html_path_expedia == (
        ".data/manual_scrapes/accommodations_expedia.html"
    )


def test_per_source_html_path_can_be_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings(scraped_accommodation_html_path_booking=None)

    assert settings.scraped_accommodation_html_path_booking is None
    assert settings.resolved_scraped_accommodation_html_paths_by_source()["booking"] is None


def test_resolved_per_source_html_paths_are_relative_to_backend_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    resolved = settings.resolved_scraped_accommodation_html_paths_by_source()

    assert set(resolved.keys()) == {
        "booking",
        "expedia",
        "hotelbeds",
        "hostelworld",
        "vrbo",
        "airbnb",
    }
    for brand, path in resolved.items():
        assert path is not None
        assert path.is_absolute()
        assert path.name == f"accommodations_{brand}.html"
        assert path.parent.name == "manual_scrapes"


def test_resolved_per_source_html_path_respects_absolute_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    absolute_path = "/tmp/example-absolute-booking.html"
    settings = Settings(scraped_accommodation_html_path_booking=absolute_path)

    resolved = settings.resolved_scraped_accommodation_html_paths_by_source()

    assert resolved["booking"] == Path(absolute_path)


def test_per_source_paths_are_distinct_from_each_other_and_from_legacy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every default path is a distinct filename -- the out-of-the-box
    configuration never accidentally makes two sources (or a brand and
    the legacy single-file setting) collide on the same real file."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    resolved = settings.resolved_scraped_accommodation_html_paths_by_source()
    all_paths = list(resolved.values()) + [settings.resolved_scraped_accommodation_html_path()]

    assert len(all_paths) == len(set(all_paths))
