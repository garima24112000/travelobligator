from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.flight import FlightSearchRequest, FlightSearchStatus
from app.providers.flights import scraped_adapter as scraped_adapter_module
from app.providers.flights.scraped_adapter import ScrapedLocalFlightProvider

# Step 169B: config-gated local/manual scraped flight provider tests --
# the not_connected/unavailable config-gate behavior (missing path,
# disabled scraping) that predates the Step 169C/169D parser+cache
# wiring. See test_scraped_flight_cache.py for parser/cache-wiring
# behavior tests (Step 169D) and test_scraped_flight_parser.py for the
# parser itself (Step 169C). Never a real website, never a network call.


def _request(**overrides: object) -> FlightSearchRequest:
    fields: dict[str, object] = {
        "origin": "JFK",
        "destination": "LIS",
        "departure_date": date(2026, 10, 10),
    }
    fields.update(overrides)
    return FlightSearchRequest(**fields)


def _enabled_settings(html_path: str | None, **overrides: object) -> Settings:
    fields: dict[str, object] = {
        "scraping_enabled": True,
        "scraped_flight_provider_enabled": True,
        "scraped_flight_html_path": html_path,
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _write_html(tmp_path: Path, content: str = "<html><body>irrelevant</body></html>") -> str:
    html_file = tmp_path / "scraped_flight_fixture.html"
    html_file.write_text(content, encoding="utf-8")
    return str(html_file)


# ---------------------------------------------------------------------------
# 7. ScrapedLocalFlightProvider returns unavailable by default when
#    default local HTML file is missing.
# ---------------------------------------------------------------------------


def test_returns_unavailable_with_default_settings_and_missing_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


def test_missing_file_message_is_honest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing_path = str(tmp_path / "does_not_exist.html")
    settings = _enabled_settings(missing_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []
    assert "does not exist" in (result.message or "")


def test_returns_unavailable_when_path_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _enabled_settings(None)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


# ---------------------------------------------------------------------------
# 8. ScrapedLocalFlightProvider returns not_connected when
#    scraping_enabled=False.
# ---------------------------------------------------------------------------


def test_returns_not_connected_when_scraping_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path)
    settings = _enabled_settings(html_path, scraping_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 9. ScrapedLocalFlightProvider returns not_connected when
#    scraped_flight_provider_enabled=False.
# ---------------------------------------------------------------------------


def test_returns_not_connected_when_provider_flag_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path)
    settings = _enabled_settings(html_path, scraped_flight_provider_enabled=False)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.NOT_CONNECTED
    assert result.offers == []


# ---------------------------------------------------------------------------
# 10 (superseded by Step 169D). ScrapedLocalFlightProvider now actually
# parses an existing local file -- a file with no recognizable
# flight-offer markup still returns unavailable, but for the honest
# reason that no valid offer was found, not because parsing is
# unimplemented (see test_scraped_flight_cache.py for the real-parsing
# success path).
# ---------------------------------------------------------------------------


def test_returns_unavailable_when_file_has_no_flight_offer_markup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    html_path = _write_html(tmp_path, "<html><body>no flight offers here</body></html>")
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []
    assert "no valid flight offers" in (result.message or "").lower()


# ---------------------------------------------------------------------------
# 11. ScrapedLocalFlightProvider never creates fallback offers.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "settings_overrides",
    [
        {"scraping_enabled": False},
        {"scraped_flight_provider_enabled": False},
        {"scraped_flight_html_path": None},
    ],
)
def test_never_returns_offers_regardless_of_config_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings_overrides: dict[str, object]
) -> None:
    html_path = _write_html(tmp_path)
    settings = _enabled_settings(html_path, **settings_overrides)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.offers == []


def test_accommodation_shaped_markup_never_produces_a_flight_offer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file shaped like the accommodation parser's `property-card`
    micro-format has no `flight-offer` element, so the flight parser
    (Step 169C, wired in Step 169D) honestly finds nothing -- it never
    reinterprets unrelated markup as a flight offer."""
    html_path = _write_html(
        tmp_path,
        """
        <html><body>
        <div class="property-card" data-property-id="fake-flight-1">
          <h2 class="property-name">NOT_A_REAL_FLIGHT</h2>
          <span class="price" data-currency="USD">199.00</span>
        </div>
        </body></html>
        """,
    )
    settings = _enabled_settings(html_path)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(_request())

    assert result.status == FlightSearchStatus.UNAVAILABLE
    assert result.offers == []


# ---------------------------------------------------------------------------
# 16/23. Provider creation never calls the network.
# ---------------------------------------------------------------------------


def test_provider_name_is_stable() -> None:
    assert ScrapedLocalFlightProvider().provider_name == "scraped_flight_provider"


def test_echoes_request_context_without_inventing_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)
    monkeypatch.setattr(scraped_adapter_module, "get_settings", lambda: settings)

    result = ScrapedLocalFlightProvider().search_flights(
        _request(
            origin="JFK",
            destination="LIS",
            departure_date=date(2026, 10, 10),
            return_date=date(2026, 10, 17),
            adults=2,
            children=1,
            currency="EUR",
        )
    )
    assert result.origin == "JFK"
    assert result.destination == "LIS"
    assert result.return_date == date(2026, 10, 17)
    assert result.adults == 2
    assert result.children == 1
    assert result.currency == "EUR"
