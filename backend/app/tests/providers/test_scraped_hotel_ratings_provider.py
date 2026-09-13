from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.hotel_ratings import HotelRatingsRequest, HotelRatingsStatus
from app.providers.hotel_ratings import scraped_adapter as scraped_adapter_module
from app.providers.hotel_ratings.scraped_adapter import ScrapedLocalHotelRatingsProvider

# Step 185E: config-gated local/manual scraped hotel-ratings provider
# tests -- the not_connected/unavailable config-gate behavior, mirroring
# test_scraped_flight_provider.py/test_scraped_accommodation_adapter.py.
# See test_scraped_hotel_ratings_multi_source.py for multi-source
# merge/matching behavior and test_hotel_ratings_source_parsers.py for
# the parsers themselves. Never a real website, never a network call.


def _request(**overrides: object) -> HotelRatingsRequest:
    fields: dict[str, object] = {
        "offer_id": "offer_0",
        "property_name": "Test Hotel",
    }
    fields.update(overrides)
    return HotelRatingsRequest(**fields)


def _enabled_settings(html_path: str | None, **overrides: object) -> Settings:
    """The two independent per-brand ratings paths
    (`scraped_hotel_ratings_html_path_tripadvisor`/
    `_google_places_ratings`) default to `None` here -- i.e. disabled --
    so every test in this file exercises true single-slot (legacy-path-
    only) behavior in isolation. Tests that want multi-source behavior
    pass their own explicit per-brand path overrides (see
    test_scraped_hotel_ratings_multi_source.py)."""
    fields: dict[str, object] = {
        "scraping_enabled": True,
        "scraped_hotel_ratings_provider_enabled": True,
        "scraped_hotel_ratings_cache_enabled": False,
        "scraped_hotel_ratings_html_path": html_path,
        "scraped_hotel_ratings_html_path_tripadvisor": None,
        "scraped_hotel_ratings_html_path_google_places_ratings": None,
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _write_html(tmp_path: Path, content: str = "<html><body>irrelevant</body></html>") -> str:
    html_file = tmp_path / "scraped_hotel_ratings_fixture.html"
    html_file.write_text(content, encoding="utf-8")
    return str(html_file)


_ONE_RATING_HTML = """
<html><body>
<div class="hotel-rating">
  <span class="property-name">Test Hotel</span>
  <span class="rating-value">4.5</span>
  <span class="review-count">120</span>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Missing file / unset path -> unavailable, never a fabricated rating.
# ---------------------------------------------------------------------------


def test_returns_unavailable_with_default_settings_and_missing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings([_request()])

    assert result.status == HotelRatingsStatus.UNAVAILABLE
    assert result.items == []


def test_missing_file_message_is_honest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing_path = str(tmp_path / "does_not_exist.html")
    settings = _enabled_settings(missing_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings([_request()])

    assert result.status == HotelRatingsStatus.UNAVAILABLE
    assert result.items == []
    assert "does not exist" in (result.message or "")


def test_returns_unavailable_when_path_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _enabled_settings(None)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings([_request()])

    assert result.status == HotelRatingsStatus.UNAVAILABLE
    assert result.items == []


# ---------------------------------------------------------------------------
# Disabled flags -> not_connected, never a fabricated rating.
# ---------------------------------------------------------------------------


def test_returns_not_connected_when_scraping_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path)
    settings = _enabled_settings(html_path, scraping_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings([_request()])

    assert result.status == HotelRatingsStatus.NOT_CONNECTED
    assert result.items == []


def test_returns_not_connected_when_provider_flag_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path)
    settings = _enabled_settings(html_path, scraped_hotel_ratings_provider_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings([_request()])

    assert result.status == HotelRatingsStatus.NOT_CONNECTED
    assert result.items == []


# ---------------------------------------------------------------------------
# A file with no recognizable hotel-rating markup -> unavailable, honest
# reason, never a fabricated rating.
# ---------------------------------------------------------------------------


def test_returns_unavailable_when_file_has_no_rating_markup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, "<html><body>no ratings here</body></html>")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings([_request()])

    assert result.status == HotelRatingsStatus.UNAVAILABLE
    assert result.items == []
    assert "no valid hotel rating records" in (result.message or "").lower()


def test_accommodation_shaped_markup_never_produces_a_rating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file shaped like the accommodation parser's `property-card`
    micro-format has no `hotel-rating` element, so the ratings parser
    honestly finds nothing -- it never reinterprets unrelated markup as a
    rating record."""
    html_path = _write_html(
        tmp_path,
        """
        <html><body>
        <div class="property-card" data-property-id="fake-1">
          <h2 class="property-name">NOT_A_REAL_RATING</h2>
          <span class="rating">4.9</span>
        </div>
        </body></html>
        """,
    )
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings([_request()])

    assert result.status == HotelRatingsStatus.UNAVAILABLE
    assert result.items == []


# ---------------------------------------------------------------------------
# Happy path: a real local file with a matching property name attaches a
# real, non-fabricated rating.
# ---------------------------------------------------------------------------


def test_success_matches_by_exact_property_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _ONE_RATING_HTML)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request(offer_id="offer_0", property_name="Test Hotel")]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert len(result.items) == 1
    item = result.items[0]
    assert item.offer_id == "offer_0"
    assert item.matched is True
    assert item.rating is not None
    assert item.rating.value == 4.5
    assert item.rating.review_count == 120
    assert item.rating.data_status.value == "scraped_public_page"


def test_unmatched_property_name_stays_unmatched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _ONE_RATING_HTML)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request(offer_id="offer_0", property_name="Some Totally Different Hotel")]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert len(result.items) == 1
    assert result.items[0].matched is False
    assert result.items[0].rating is None


def test_no_property_name_stays_unmatched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _ONE_RATING_HTML)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request(offer_id="offer_0", property_name=None)]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert result.items[0].matched is False
    assert result.items[0].rating is None


def test_matching_is_case_and_whitespace_insensitive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _ONE_RATING_HTML)
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request(offer_id="offer_0", property_name="  test   HOTEL  ")]
    )

    assert result.items[0].matched is True
    assert result.items[0].rating is not None


def test_ambiguous_duplicate_property_name_never_guesses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(
        tmp_path,
        """
        <html><body>
        <div class="hotel-rating">
          <span class="property-name">Duplicate Hotel</span>
          <span class="rating-value">4.0</span>
        </div>
        <div class="hotel-rating">
          <span class="property-name">Duplicate Hotel</span>
          <span class="rating-value">2.0</span>
        </div>
        </body></html>
        """,
    )
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request(offer_id="offer_0", property_name="Duplicate Hotel")]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert result.items[0].matched is False
    assert result.items[0].rating is None


def test_never_returns_items_regardless_of_config_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, _ONE_RATING_HTML)

    for overrides in (
        {"scraping_enabled": False},
        {"scraped_hotel_ratings_provider_enabled": False},
        {"scraped_hotel_ratings_html_path": None},
    ):
        settings = _enabled_settings(html_path, **overrides)
        monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda s=settings: s)

        result = ScrapedLocalHotelRatingsProvider().get_ratings([_request()])

        assert result.items == []


# ---------------------------------------------------------------------------
# Provider creation/identity never calls the network.
# ---------------------------------------------------------------------------


def test_provider_name_is_stable() -> None:
    assert ScrapedLocalHotelRatingsProvider().provider_name == "scraped_hotel_ratings_provider"


def test_no_network_module_imported() -> None:
    import ast
    import inspect

    source = inspect.getsource(scraped_adapter_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    disallowed_substrings = ("requests", "httpx", "playwright", "selenium", "kiwi", "mcp")
    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"
