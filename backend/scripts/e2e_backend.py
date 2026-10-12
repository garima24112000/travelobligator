"""The backend for the browser end-to-end tests (P1-C): the REAL application, offline and disposable.

    python backend/scripts/e2e_backend.py            # serves on 127.0.0.1:8100 until stopped

What is real: every route, the session cookie, ownership, validation, the job lifecycle, the admission
limits, deletion, the planner and the validator. What is replaced, so that no test ever reaches a provider:

  * places      -> `E2EPlacesProvider` below: a small fixed pool of made-up places (never a real venue's
                   data), served under the OpenStreetMap adapter's provider name exactly as the hermetic
                   pytest suite does, so coverage is labelled as it would be for a real answer;
  * weather, holidays, currency -> the "not connected" base providers (no network at all);
  * routing, transit, hotel ratings, flights, accommodation inventory -> their defaults, which are
                   `not_connected` / `unavailable` without configuration;
  * every model stage -> off (the defaults), so no Groq / Gemini / Anthropic request can be made.

Storage is the explicit Local JSON development backend in a NEW temporary directory per start, removed on
exit: nothing here can read or write PostgreSQL, a deployed database, or the developer's own
`backend/.data`. The project `.env` is never loaded (`TRAVELOB_TEST_MODE=1`).

Three destinations change the stand-in provider's behaviour, so the browser tests can watch the real
application handle them (they are test controls, not product behaviour):

  * a destination containing "Slowville"  -> the places lookup takes `E2E_SLOW_SECONDS` (default 6): the
    job is genuinely `running` for that long (progress display, duplicate-submit and delete-while-running);
  * a destination containing "Failville"  -> the places lookup raises: the job ends `failed`;
  * anything else                         -> the fixed pool, immediately.

This module is test infrastructure. It is never imported by `backend/app`.
"""

from __future__ import annotations

import atexit
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_DATA_DIR = Path(tempfile.mkdtemp(prefix="travelobligator_e2e_"))
atexit.register(shutil.rmtree, _DATA_DIR, ignore_errors=True)

# Everything is set BEFORE the application is imported (its singletons read the settings at import).
# `os.environ[...] =`, not `setdefault`: an inherited DATABASE_URL or provider key must never apply here.
_ENVIRONMENT = {
    "TRAVELOB_TEST_MODE": "1",  # never load the project .env
    "APP_ENV": "development",
    "PERSISTENCE_BACKEND": "local_json",
    "LOCAL_STORAGE_PATH": str(_DATA_DIR / "state.json"),
    "PROVIDER_CACHE_BACKEND": "sqlite",
    "PROVIDER_CACHE_PATH": str(_DATA_DIR / "provider_cache.sqlite3"),
    "SESSION_SECRET_KEY": "e2e-only-session-secret-not-used-anywhere-else",
    "SESSION_COOKIE_SECURE": "false",  # the tests run over plain http on localhost
    "BACKEND_CORS_ORIGINS": os.environ.get("E2E_FRONTEND_ORIGIN", "http://127.0.0.1:3100"),
    "ASYNC_GENERATION_ENABLED": "true",  # the production path: 202 + a polled job
    # the same reason as the hermetic pytest suite: the stand-in pool is deliberately tiny
    "INVENTORY_SUFFICIENCY_GATE_ENABLED": "false",
    # no provider, no model, no fallback to one
    "PLACES_PROVIDER": "openstreetmap",
    "GEOCODING_PROVIDER": "nominatim",
    "ROUTING_PROVIDER": "not_connected",
    "AI_CANDIDATE_PROPOSAL_PROVIDER": "not_connected",
    "AI_CANDIDATE_DISCOVERY_ENABLED": "false",
    "AI_ITINERARY_REASONING_ENABLED": "false",
    "AI_ITINERARY_REPAIR_ENABLED": "false",
    "ITINERARY_NARRATOR_ENABLED": "false",
    "AI_FEEDBACK_INTERPRETER_ENABLED": "false",
    "TARGETED_REGENERATION_ENABLED": "false",
    "KIWI_MCP_ENABLED": "false",
    # Limits. Many tests sign up and generate in parallel, so the deployment-wide limits are off; the
    # per-user generation limit is LOW so one test can reach the real 429 quickly.
    "SIGNUP_MAX_PER_WINDOW_GLOBAL": "0",
    "GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW": "0",
    "GENERATION_MAX_CONCURRENT_JOBS": "0",
    "GENERATION_MAX_JOBS_PER_USER_PER_WINDOW": "3",
    "MAX_TRIPS_PER_USER": "20",
}
for _name in ("DATABASE_URL", "REDIS_URL", "GROQ_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "GEOAPIFY_API_KEY",
              "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(_name, None)
os.environ.update(_ENVIRONMENT)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.common import DataStatus, GeoPoint, ProviderStatus  # noqa: E402
from app.models.providers import NormalizedPlace, ProviderResponse  # noqa: E402
from app.providers.base import CurrencyProvider, HolidayProvider, PlacesProvider, WeatherProvider  # noqa: E402

SLOW_SECONDS = float(os.environ.get("E2E_SLOW_SECONDS", "6"))

# Made-up places around one point. Two names are there on purpose: a very long one (wrapping) and one in
# a non-Latin script (rendering). None of this describes a real venue.
_ATTRACTIONS = [
    ("e2e/attraction/1", "Fixture Old Town Square", 38.7139, -9.1394),
    ("e2e/attraction/2", "Fixture Riverside Promenade", 38.7071, -9.1365),
    (
        "e2e/attraction/3",
        "The Extraordinarily Long Fixture Museum of Maritime History, Decorative Arts and Regional Archaeology (East Wing)",
        38.7169,
        -9.1420,
    ),
    ("e2e/attraction/4", "フィクスチャ国立博物館 Fixture National Museum", 38.7205, -9.1450),
    ("e2e/attraction/5", "Fixture Hilltop Viewpoint", 38.7190, -9.1330),
    ("e2e/attraction/6", "Fixture Botanical Garden", 38.7240, -9.1500),
    ("e2e/attraction/7", "Fixture Cathedral", 38.7098, -9.1330),
    ("e2e/attraction/8", "Fixture Tile Workshop", 38.7120, -9.1280),
    ("e2e/attraction/9", "Fixture City Market Hall", 38.7068, -9.1458),
]
_RESTAURANTS = [
    ("e2e/restaurant/1", "Fixture Corner Bistro", 38.7135, -9.1380),
    ("e2e/restaurant/2", "Fixture Harbour Kitchen", 38.7075, -9.1372),
]
_ACCOMMODATION = [("e2e/accommodation/1", "Fixture Central Guesthouse", 38.7142, -9.1401)]


class E2EPlacesProvider(PlacesProvider):
    """Deterministic stand-in for the places provider (see the module docstring)."""

    provider_name = "openstreetmap_places"

    def search_attractions(self, destination: str, filters: dict[str, Any] | None = None) -> ProviderResponse[Any]:
        text = (destination or "").lower()
        if "failville" in text:
            raise RuntimeError("e2e: simulated provider failure")
        if "slowville" in text:
            time.sleep(SLOW_SECONDS)
        return self._response("attraction", _ATTRACTIONS)

    def search_restaurants(self, area: str, filters: dict[str, Any] | None = None) -> ProviderResponse[Any]:
        return self._response("restaurant", _RESTAURANTS)

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._response("hotel", _ACCOMMODATION)

    def _response(self, category: str, entries: list[tuple[str, str, float, float]]) -> ProviderResponse[Any]:
        places = [
            NormalizedPlace(
                place_id=place_id,
                name=name,
                category=category,
                coordinates=GeoPoint(lat=lat, lng=lng),
                source=self.provider_name,
                data_status=DataStatus.LIVE,
                confidence=0.6,
            )
            for place_id, name, lat, lng in entries
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.65,
            message="End-to-end test fixture data; not a real provider call.",
        )


def install_offline_providers() -> None:
    from app.providers.gateway import provider_gateway

    provider_gateway.places = E2EPlacesProvider()
    provider_gateway.weather = WeatherProvider()
    provider_gateway.holiday = HolidayProvider()
    provider_gateway.currency = CurrencyProvider()


def main() -> None:
    import uvicorn

    from app.core.config import get_settings
    from app.main import app

    settings = get_settings()
    # Refuse to run as anything but the disposable offline backend.
    assert settings.persistence_backend == "local_json" and settings.database_url is None
    assert Path(settings.resolved_local_storage_path()).parent == _DATA_DIR
    assert not settings.groq_api_key and not settings.gemini_api_key and not settings.geoapify_api_key
    assert settings.app_env == "development"
    install_offline_providers()
    # Uvicorn shuts down on SIGTERM/SIGINT and then re-raises the signal to the handler that was installed
    # before it; the default one would end the process without running `atexit`, leaving the store behind.
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: sys.exit(0))
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("E2E_BACKEND_PORT", "8100")), log_level="warning")


if __name__ == "__main__":
    main()
