from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.config import Settings, get_settings
from app.models.planning_state import PlanningState, TravelGroupType, TripRequest
from app.providers.flights import KiwiMcpFlightProvider, ScrapedLocalFlightProvider
from app.providers.gateway import provider_gateway
from app.providers.routing import NotConnectedRoutingProvider
from app.providers.routing.osrm_adapter import OSRMRoutingAdapter
from app.services.itinerary_narrative_service import ItineraryNarrativeService

# Step 183B-FIX: proves the automated test suite is hermetic against a
# developer's real local `.env` -- specifically the exact scenario that
# caused 32 real test failures after Section 183B: a real `.env` with
# `FLIGHT_PROVIDER=kiwi_mcp`, `KIWI_MCP_ENABLED=true`,
# `ROUTING_PROVIDER=osrm`, `OSRM_BASE_URL=<real url>`,
# `ITINERARY_NARRATOR_ENABLED=true`, `ITINERARY_NARRATOR_PROVIDER=groq`,
# and a real `GROQ_API_KEY` present at the repo root while `pytest` runs.
# None of these tests move, delete, or read the repo's actual `.env` --
# they use a throwaway fake one under `tmp_path` instead, so they pass
# identically whether or not a real `.env` happens to exist.


def test_travelob_test_mode_flag_is_set_during_pytest() -> None:
    """conftest.py must set this before any other import for
    get_settings()'s hermetic branch (app/core/config.py) to ever take
    effect -- see that file's docstring."""
    assert os.environ.get("TRAVELOB_TEST_MODE") == "1"


def test_get_settings_ignores_a_present_dotenv_file_with_live_provider_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directly reproduces the Step 183B-FIX bug report: a `.env` in the
    current working directory with live kiwi_mcp/osrm/narrator settings
    must not leak into `get_settings()` during pytest."""
    fake_env = tmp_path / ".env"
    fake_env.write_text(
        "FLIGHT_PROVIDER=kiwi_mcp\n"
        "KIWI_MCP_ENABLED=true\n"
        "ROUTING_PROVIDER=osrm\n"
        "OSRM_BASE_URL=https://router.project-osrm.org\n"
        "ITINERARY_NARRATOR_ENABLED=true\n"
        "ITINERARY_NARRATOR_PROVIDER=groq\n"
        "GROQ_API_KEY=fake-not-a-real-key\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.flight_provider == "scraped_local"
    assert settings.kiwi_mcp_enabled is False
    assert settings.routing_provider == "not_connected"
    assert settings.itinerary_narrator_enabled is False
    assert settings.itinerary_narrator_provider == "not_connected"

    get_settings.cache_clear()


def test_direct_settings_construction_still_honors_a_real_dotenv_file(
    tmp_path: Path,
) -> None:
    """Control test: proves this fix only changes `get_settings()`'s test-mode
    branch, not `Settings`/pydantic-settings dotenv support itself --
    normal app/dev-server runtime (which never sets TRAVELOB_TEST_MODE and
    never passes `_env_file=None`) must still read a real `.env` file
    exactly as before."""
    fake_env = tmp_path / ".env"
    fake_env.write_text("FLIGHT_PROVIDER=kiwi_mcp\nROUTING_PROVIDER=osrm\n", encoding="utf-8")

    settings = Settings(_env_file=str(fake_env))

    assert settings.flight_provider == "kiwi_mcp"
    assert settings.routing_provider == "osrm"


def test_monkeypatch_opt_in_still_works_after_cache_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test can still deliberately opt into a specific provider config
    via monkeypatch.setenv + get_settings.cache_clear(), exactly as
    documented in get_settings()'s docstring."""
    monkeypatch.setenv("ROUTING_PROVIDER", "osrm")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.routing_provider == "osrm"


def test_module_level_provider_gateway_singleton_defaults_are_safe() -> None:
    """The real `provider_gateway` singleton used by every `/trips` API
    route (`app/providers/gateway.py`) is constructed once, at import
    time. If that first `get_settings()` call ever reads a real `.env`
    with live kiwi_mcp/osrm settings, this singleton bakes in a live
    provider for the rest of the whole pytest session -- no later
    monkeypatch/cache_clear can undo it. This proves it didn't."""
    assert isinstance(provider_gateway.flight_inventory, ScrapedLocalFlightProvider)
    assert not isinstance(provider_gateway.flight_inventory, KiwiMcpFlightProvider)
    assert isinstance(provider_gateway.routing, NotConnectedRoutingProvider)
    assert not isinstance(provider_gateway.routing, OSRMRoutingAdapter)


def test_itinerary_narrative_service_default_construction_never_calls_a_real_provider() -> None:
    """Unlike test_itinerary_narrative_service.py's tests (which all inject
    a fake provider), this constructs the service with its real,
    factory-resolved default -- proving that with a hermetic default
    config, the disabled short-circuit in
    ItineraryNarrativeService.generate is reached before any real
    provider is ever constructed or called."""
    trip_request = TripRequest(
        primary_destination="Lisbon, Portugal",
        start_date="2026-10-10",
        end_date="2026-10-11",
        travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE,
    )
    planning_state = PlanningState(trip_request=trip_request)

    service = ItineraryNarrativeService()
    result = service.generate(planning_state)

    assert result.itinerary_narrative_report is not None
    assert result.itinerary_narrative_report.status.value == "not_connected"
