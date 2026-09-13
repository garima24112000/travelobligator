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


# ---------------------------------------------------------------------------
# Step 182E: FLIGHT_MANUAL_HTML_SOURCE is a cosmetic provenance label
# only -- it never selects a provider, is completely distinct from the
# real kiwi_mcp adapter, and always normalizes to a safe, known value.
# ---------------------------------------------------------------------------


def test_flight_manual_html_source_default_is_generic() -> None:
    field_info = Settings.model_fields["flight_manual_html_source"]
    assert field_info.default == "generic"
    assert field_info.alias == "FLIGHT_MANUAL_HTML_SOURCE"


@pytest.mark.parametrize("value", ["generic", "skyscanner", "google_flights", "kiwi", "other"])
def test_flight_manual_html_source_accepts_every_documented_value(value: str) -> None:
    settings = Settings(_env_file=None, flight_manual_html_source=value)
    assert settings.flight_manual_html_source == value


@pytest.mark.parametrize("value", ["made_up", "SKYSCANNER", "", "kiwi_mcp"])
def test_flight_manual_html_source_falls_back_to_generic_for_unrecognized_values(
    value: str,
) -> None:
    settings = Settings(_env_file=None, flight_manual_html_source=value)
    assert settings.flight_manual_html_source == "generic"


def test_flight_manual_html_source_kiwi_label_is_independent_of_kiwi_mcp_flag() -> None:
    """Setting the manual/local HTML source label to "kiwi" must never
    itself enable, resemble, or be confused with the real, live
    flight_provider="kiwi_mcp"/kiwi_mcp_enabled adapter -- these are two
    completely independent config surfaces."""
    settings = Settings(_env_file=None, flight_manual_html_source="kiwi")
    assert settings.flight_provider == "scraped_local"
    assert settings.kiwi_mcp_enabled is False


# ---------------------------------------------------------------------------
# Step 182E: partner/paid-access flight provider credential placeholders
# default to None and are not required for default tests.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_name", ["skyscanner_api_key", "skyscanner_api_base_url"])
def test_partner_flight_credential_placeholders_default_to_none(field_name: str) -> None:
    settings = Settings(_env_file=None)
    assert getattr(settings, field_name) is None


def test_settings_constructs_without_any_partner_flight_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.flight_provider == "scraped_local"
    assert settings.skyscanner_api_key is None


# ---------------------------------------------------------------------------
# Step 185D: independent per-source local file paths -- multi-source
# flight ingestion. Each defaults to its own real path (never `None`), so
# every source is "known and eligible" out of the box, but with no file
# actually present at any of these paths (the fresh-clone state), the
# provider honestly reports each one as missing -- see
# test_scraped_flight_provider.py/test_scraped_flight_multi_source.py for
# that behavior.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name,alias,expected_default",
    [
        (
            "scraped_flight_html_path_skyscanner",
            "SCRAPED_FLIGHT_HTML_PATH_SKYSCANNER",
            ".data/manual_scrapes/flights_skyscanner.html",
        ),
        (
            "scraped_flight_html_path_google_flights",
            "SCRAPED_FLIGHT_HTML_PATH_GOOGLE_FLIGHTS",
            ".data/manual_scrapes/flights_google_flights.html",
        ),
        (
            "scraped_flight_html_path_kiwi_manual",
            "SCRAPED_FLIGHT_HTML_PATH_KIWI_MANUAL",
            ".data/manual_scrapes/flights_kiwi_manual.html",
        ),
    ],
)
def test_per_source_flight_html_path_default(
    field_name: str, alias: str, expected_default: str
) -> None:
    field_info = Settings.model_fields[field_name]
    assert field_info.default == expected_default
    assert field_info.alias == alias


def test_settings_constructs_without_any_per_source_flight_path_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.scraped_flight_html_path_skyscanner == (
        ".data/manual_scrapes/flights_skyscanner.html"
    )
    assert settings.scraped_flight_html_path_google_flights == (
        ".data/manual_scrapes/flights_google_flights.html"
    )
    assert settings.scraped_flight_html_path_kiwi_manual == (
        ".data/manual_scrapes/flights_kiwi_manual.html"
    )
    # The pre-185D single-file setting is completely untouched.
    assert settings.scraped_flight_html_path == ".data/manual_scrapes/flights.html"
    assert settings.flight_manual_html_source == "generic"


def test_per_source_flight_html_path_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("SCRAPED_FLIGHT_HTML_PATH_SKYSCANNER", "/tmp/example-skyscanner.html")

    settings = Settings()

    assert settings.scraped_flight_html_path_skyscanner == "/tmp/example-skyscanner.html"
    # Other sources are unaffected.
    assert settings.scraped_flight_html_path_google_flights == (
        ".data/manual_scrapes/flights_google_flights.html"
    )


def test_per_source_flight_html_path_can_be_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings(scraped_flight_html_path_skyscanner=None)

    assert settings.scraped_flight_html_path_skyscanner is None
    assert settings.resolved_scraped_flight_html_paths_by_source()["skyscanner"] is None


def test_resolved_per_source_flight_html_paths_are_relative_to_backend_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    resolved = settings.resolved_scraped_flight_html_paths_by_source()

    assert set(resolved.keys()) == {"skyscanner", "google_flights", "kiwi_manual"}
    for brand, path in resolved.items():
        assert path is not None
        assert path.is_absolute()
        assert path.name == f"flights_{brand}.html"
        assert path.parent.name == "manual_scrapes"


def test_resolved_per_source_flight_html_path_respects_absolute_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    absolute_path = "/tmp/example-absolute-skyscanner.html"
    settings = Settings(scraped_flight_html_path_skyscanner=absolute_path)

    resolved = settings.resolved_scraped_flight_html_paths_by_source()

    assert resolved["skyscanner"] == Path(absolute_path)


def test_per_source_flight_paths_are_distinct_from_each_other_and_from_legacy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every default path is a distinct filename -- the out-of-the-box
    configuration never accidentally makes two sources (or a brand and
    the legacy single-file setting) collide on the same real file."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    resolved = settings.resolved_scraped_flight_html_paths_by_source()
    all_paths = list(resolved.values()) + [settings.resolved_scraped_flight_html_path()]

    assert len(all_paths) == len(set(all_paths))
