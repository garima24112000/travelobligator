from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.hotel_ratings import HotelRatingsRequest, HotelRatingsStatus
from app.providers.hotel_ratings import ScrapedLocalHotelRatingsProvider
from app.providers.hotel_ratings import scraped_adapter as scraped_adapter_module
from app.providers.hotel_ratings import scraped_parser as scraped_parser_module
from app.storage.provider_cache_store import ProviderCacheStore

# Step 185E: multi-source hotel-ratings ingestion tests --
# ScrapedLocalHotelRatingsProvider attempting several independent local
# files in one call, then conservatively matching parsed records against
# the incoming requests. Every test uses a real local temp file -- never
# a real website, never a network call.


def _rating_html(property_name: str, rating: str = "4.0", review_count: str | None = "50") -> str:
    review_count_span = (
        f'<span class="review-count">{review_count}</span>' if review_count is not None else ""
    )
    return f"""
<html><body>
<div class="hotel-rating">
  <span class="property-name">{property_name}</span>
  <span class="rating-value">{rating}</span>
  {review_count_span}
</div>
</body></html>
"""


def _request(offer_id: str, property_name: str | None) -> HotelRatingsRequest:
    return HotelRatingsRequest(offer_id=offer_id, property_name=property_name)


def _write_html(path: Path, content: str) -> str:
    path.write_text(content, encoding="utf-8")
    return str(path)


def _no_files_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Every source path points at a real, distinct tmp_path location (so
    nothing accidentally reads the real dev `.data/manual_scrapes/`
    directory during this test file), but none of the files are actually
    written -- the honest, fully-empty baseline every test builds on."""
    fields: dict[str, object] = {
        "scraping_enabled": True,
        "scraped_hotel_ratings_provider_enabled": True,
        "scraped_hotel_ratings_cache_enabled": False,
        "scraped_hotel_ratings_html_path": str(tmp_path / "generic.html"),
        "scraped_hotel_ratings_html_path_tripadvisor": str(tmp_path / "tripadvisor.html"),
        "scraped_hotel_ratings_html_path_google_places_ratings": str(
            tmp_path / "google_places_ratings.html"
        ),
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


# ---------------------------------------------------------------------------
# Backward compatibility: single generic file, no per-source files present.
# ---------------------------------------------------------------------------


def test_generic_only_behavior_is_unchanged_when_no_brand_files_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "generic.html", _rating_html("Generic Hotel"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request("offer_0", "Generic Hotel")]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert len(result.items) == 1
    assert result.items[0].matched is True
    assert result.items[0].rating.value == 4.0


def test_all_missing_files_returns_unavailable_with_clear_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings([_request("offer_0", "Any Hotel")])

    assert result.status == HotelRatingsStatus.UNAVAILABLE
    assert result.items == []
    assert result.message is not None
    assert len(result.warnings) == 3


# ---------------------------------------------------------------------------
# Multi-source: several source files present at once produce merged
# records, matched independently.
# ---------------------------------------------------------------------------


def test_multiple_source_files_produce_merged_distinct_ratings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Tripadvisor Hotel", rating="4.2"))
    _write_html(
        tmp_path / "google_places_ratings.html",
        _rating_html("Google Places Hotel", rating="3.8"),
    )
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [
            _request("offer_0", "Tripadvisor Hotel"),
            _request("offer_1", "Google Places Hotel"),
        ]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert len(result.items) == 2
    ratings_by_offer = {item.offer_id: item for item in result.items}
    assert ratings_by_offer["offer_0"].matched is True
    assert ratings_by_offer["offer_0"].rating.value == 4.2
    assert ratings_by_offer["offer_1"].matched is True
    assert ratings_by_offer["offer_1"].rating.value == 3.8

    for item in result.items:
        assert item.rating.data_status.value == "scraped_public_page"
        assert item.rating.provider.startswith("scraped:")


def test_missing_optional_source_file_does_not_block_successful_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Tripadvisor Hotel"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request("offer_0", "Tripadvisor Hotel")]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert result.items[0].matched is True
    assert len(result.warnings) >= 1
    assert any("google_places_ratings" in warning for warning in result.warnings)


def test_all_sources_plus_generic_can_coexist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "generic.html", _rating_html("Generic Hotel"))
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Tripadvisor Hotel"))
    _write_html(
        tmp_path / "google_places_ratings.html", _rating_html("Google Places Hotel")
    )
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [
            _request("offer_0", "Generic Hotel"),
            _request("offer_1", "Tripadvisor Hotel"),
            _request("offer_2", "Google Places Hotel"),
        ]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert all(item.matched for item in result.items)
    assert result.warnings == []


# ---------------------------------------------------------------------------
# Conservative matching: unmatched/ambiguous requests never attach a
# guessed rating.
# ---------------------------------------------------------------------------


def test_unmatched_property_does_not_attach_a_random_rating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Real Hotel"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request("offer_0", "A Completely Different Hotel")]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert result.items[0].matched is False
    assert result.items[0].rating is None


def test_ambiguous_name_across_two_sources_never_guesses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same property name appearing in two different source files
    with two different ratings is ambiguous -- never guessed between."""
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Shared Name Hotel", rating="4.0"))
    _write_html(
        tmp_path / "google_places_ratings.html",
        _rating_html("Shared Name Hotel", rating="2.0"),
    )
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request("offer_0", "Shared Name Hotel")]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert result.items[0].matched is False
    assert result.items[0].rating is None


# ---------------------------------------------------------------------------
# Malformed source file: failed, not fabricated.
# ---------------------------------------------------------------------------


def test_malformed_source_file_does_not_fabricate_a_fallback_rating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Tripadvisor Hotel"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    def _raise(*args: object, **kwargs: object) -> None:
        raise ValueError("simulated malformed HTML")

    monkeypatch.setattr(scraped_parser_module, "parse_scraped_hotel_ratings_html", _raise)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request("offer_0", "Tripadvisor Hotel")]
    )

    assert result.status == HotelRatingsStatus.FAILED
    assert result.items == []
    assert "tripadvisor" in (result.message or "")


def test_one_malformed_source_does_not_block_another_successful_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Tripadvisor Hotel"))
    _write_html(
        tmp_path / "google_places_ratings.html", _rating_html("Google Places Hotel")
    )
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    from app.providers.hotel_ratings.source_parsers import tripadvisor as tripadvisor_parser_module

    def _raise(*args: object, **kwargs: object) -> None:
        raise ValueError("simulated malformed tripadvisor HTML")

    monkeypatch.setattr(tripadvisor_parser_module, "parse", _raise)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [
            _request("offer_0", "Tripadvisor Hotel"),
            _request("offer_1", "Google Places Hotel"),
        ]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    ratings_by_offer = {item.offer_id: item for item in result.items}
    assert ratings_by_offer["offer_0"].matched is False
    assert ratings_by_offer["offer_1"].matched is True
    assert any("tripadvisor" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# Provenance stays manual/local and not official for every slot.
# ---------------------------------------------------------------------------


def test_ratings_are_labeled_manual_local_never_official(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Tripadvisor Hotel"))
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request("offer_0", "Tripadvisor Hotel")]
    )

    rating = result.items[0].rating
    assert rating.data_status.value == "scraped_public_page"
    assert "not official" in rating.source_name
    assert "not official-provider data" in (result.message or "")


# ---------------------------------------------------------------------------
# Cache: per-source path contributes to the query hash.
# ---------------------------------------------------------------------------


def test_cache_separates_tripadvisor_from_google_places_ratings_for_the_same_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Tripadvisor Hotel", rating="4.5"))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _no_files_settings(tmp_path, scraped_hotel_ratings_cache_enabled=True)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalHotelRatingsProvider(cache_store=cache_store)
    requests = [
        _request("offer_0", "Tripadvisor Hotel"),
        _request("offer_1", "Google Places Hotel"),
    ]
    first_result = provider.get_ratings(requests)
    assert first_result.items[0].matched is True
    assert first_result.items[1].matched is False

    _write_html(
        tmp_path / "google_places_ratings.html", _rating_html("Google Places Hotel", rating="3.5")
    )
    second_result = provider.get_ratings(requests)

    assert second_result.items[1].matched is True
    assert second_result.items[1].rating.value == 3.5


def test_cache_key_changes_when_a_brand_file_is_edited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    tripadvisor_path = tmp_path / "tripadvisor.html"
    _write_html(tripadvisor_path, _rating_html("Tripadvisor Hotel", rating="4.0"))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _no_files_settings(tmp_path, scraped_hotel_ratings_cache_enabled=True)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalHotelRatingsProvider(cache_store=cache_store)
    requests = [_request("offer_0", "Tripadvisor Hotel")]
    first_result = provider.get_ratings(requests)
    assert first_result.items[0].rating.value == 4.0

    _write_html(tripadvisor_path, _rating_html("Tripadvisor Hotel", rating="2.0"))
    second_result = provider.get_ratings(requests)

    assert second_result.items[0].rating.value == 2.0


def test_different_requests_are_not_served_a_stale_cached_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Tripadvisor Hotel"))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _no_files_settings(tmp_path, scraped_hotel_ratings_cache_enabled=True)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalHotelRatingsProvider(cache_store=cache_store)
    first_result = provider.get_ratings([_request("offer_0", "Tripadvisor Hotel")])
    assert first_result.items[0].matched is True

    second_result = provider.get_ratings([_request("offer_0", "Some Other Hotel")])
    assert second_result.items[0].matched is False


def test_clearing_the_shared_cache_source_still_works_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_html(tmp_path / "tripadvisor.html", _rating_html("Tripadvisor Hotel"))
    cache_store = ProviderCacheStore(tmp_path / "cache.sqlite3")
    settings = _no_files_settings(tmp_path, scraped_hotel_ratings_cache_enabled=True)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    provider = ScrapedLocalHotelRatingsProvider(cache_store=cache_store)
    provider.get_ratings([_request("offer_0", "Tripadvisor Hotel")])

    deleted = cache_store.clear_source("scraped_hotel_ratings")
    assert deleted >= 1


# ---------------------------------------------------------------------------
# No network call is attempted anywhere in multi-source mode.
# ---------------------------------------------------------------------------


def test_multi_source_flow_makes_no_network_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    def _fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("ScrapedLocalHotelRatingsProvider must not open a real httpx.Client")

    monkeypatch.setattr(httpx, "Client", _fail_if_called)

    _write_html(tmp_path / "tripadvisor.html", _rating_html("Tripadvisor Hotel"))
    _write_html(
        tmp_path / "google_places_ratings.html", _rating_html("Google Places Hotel")
    )
    settings = _no_files_settings(tmp_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [
            _request("offer_0", "Tripadvisor Hotel"),
            _request("offer_1", "Google Places Hotel"),
        ]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert all(item.matched for item in result.items)


# ---------------------------------------------------------------------------
# Deterministic precedence: a per-brand path identical to the legacy path
# is never double-counted (never produces two records for the same file).
# ---------------------------------------------------------------------------


def test_identical_legacy_and_brand_path_is_not_duplicated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shared_path = tmp_path / "shared.html"
    _write_html(shared_path, _rating_html("Shared Hotel"))

    settings = _no_files_settings(
        tmp_path,
        scraped_hotel_ratings_html_path=str(shared_path),
        hotel_ratings_manual_html_source="tripadvisor",
        scraped_hotel_ratings_html_path_tripadvisor=str(shared_path),
    )
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalHotelRatingsProvider().get_ratings(
        [_request("offer_0", "Shared Hotel")]
    )

    assert result.status == HotelRatingsStatus.SUCCESS
    assert result.items[0].matched is True
    # If the file were double-counted, "Shared Hotel" would appear twice
    # in the merged record list, making the match ambiguous.
    assert result.items[0].rating is not None
