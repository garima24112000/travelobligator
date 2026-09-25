from __future__ import annotations

import os
import uuid

# Step 183B-FIX: this MUST be the first thing this module does, before any
# `app.*` import below (including this file's own `from app.core.config
# import ...`) -- `app.providers.gateway` constructs its module-level
# `provider_gateway` singleton at import time, resolving the routing/
# flight-inventory providers from `get_settings()` right then. If that
# first `get_settings()` call ever reads a developer's real local `.env`
# (e.g. `FLIGHT_PROVIDER=kiwi_mcp`, `ROUTING_PROVIDER=osrm`,
# `ITINERARY_NARRATOR_ENABLED=true`), the singleton bakes in a live
# provider for the rest of the whole pytest process -- no per-test
# `monkeypatch.setenv(...)` or `get_settings.cache_clear()` afterwards can
# undo that, because the wrong adapter instance is already constructed
# and stored on the singleton. Setting this flag here, before that first
# import happens, is what makes `get_settings()` (see its docstring in
# `app/core/config.py`) skip the dotenv file entirely for the whole test
# session by default. `setdefault` (not `[...] =`) so a caller who
# explicitly unsets/overrides it before invoking pytest still wins.
os.environ.setdefault("TRAVELOB_TEST_MODE", "1")

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app
from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.providers import NormalizedPlace, ProviderResponse
from app.providers.base import PlacesProvider
from app.providers.gateway import provider_gateway
from app.repositories.itinerary_lineage_repository import itinerary_lineage_repository
from app.repositories.job_repository import job_repository
from app.repositories.planning_state_repository import planning_state_repository
from app.repositories.trip_repository import trip_repository
from app.repositories.user_repository import user_repository
from app.storage.local_json_store import LocalJsonStore
from app.storage.provider_cache_store import ProviderCacheStore


@pytest.fixture()
def synthetic_legacy_regeneration_support(monkeypatch: pytest.MonkeyPatch) -> None:
    """EXPLICIT, test-only scaffolding: makes legacy `POST /regenerate`
    succeed so a test can exercise the machinery AROUND a regeneration --
    versioning, revisions/branches, async jobs, persistence, locks.

    Production registers NO deterministic legacy operation
    (`regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS` is empty)
    because legacy regeneration cannot interpret free text, so by default a
    legacy request is refused with `REGENERATION_FEEDBACK_NOT_INTERPRETABLE`
    -- which is what every test gets unless it requests THIS fixture. A
    test that uses it is NOT testing feedback semantics and must never
    assert that a user's request was honored.

    The synthetic operation is registered under every classified
    `feedback_type`, including unclassified (no keyword guessing -- it never inspects the feedback
    text) and has a deterministic postcondition: the rerun must leave a
    generated experience plan on the resulting state. `monkeypatch.setitem`
    restores the (empty) production registry after the test, so nothing
    leaks into other tests regardless of ordering.
    """
    from app.services import regeneration_mutation_service
    from app.services.feedback_service import _FEEDBACK_TYPE_RULES

    def _synthetic_postcondition(before: Any, after: Any, event: Any) -> bool:
        return after.experience_plan is not None

    # `None` covers hand-built FeedbackEvents that were never classified.
    for feedback_type in (*_FEEDBACK_TYPE_RULES, "general_feedback", None):
        monkeypatch.setitem(
            regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS,
            feedback_type,
            _synthetic_postcondition,
        )


@pytest.fixture(autouse=True)
def _legacy_registry_starts_as_production() -> None:
    """Section 202B.1.1 leak guard (start half). Production registers NO
    deterministic legacy operation, so EVERY test must start with an empty
    registry. Only asserts -- never installs anything."""
    from app.services import regeneration_mutation_service

    assert dict(regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS) == {}, (
        "legacy regeneration registry is not empty at test start (leaked state)"
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Any:
    """Leak guard (end half). Runs AFTER every function-scoped fixture
    finalizer (including `monkeypatch` undoing the synthetic fixture's
    registration), so anything still registered is a genuine leak: it is
    cleared and the offending test is failed at its own teardown, so test
    ordering can never quietly change what a later test exercises."""
    result = yield
    from app.services import regeneration_mutation_service

    leaked = sorted(map(str, regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS))
    if leaked:
        regeneration_mutation_service.LEGACY_SUPPORTED_OPERATIONS.clear()
        raise AssertionError(f"test leaked legacy regeneration operations: {leaked}")
    return result


@pytest.fixture(autouse=True)
def _isolate_ai_candidate_proposal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolates the automated test suite from whatever a developer's local
    `.env` happens to set for AI candidate proposal config (Step 162A fix,
    extended in Step 163B). `Settings.model_config` still reads `.env`
    normally for real app/dev-server startup -- this fixture only forces
    the documented, safe defaults as real OS environment variables for the
    lifetime of each test.

    Every var below is set (never `monkeypatch.delenv`-ed). This matters
    because pydantic-settings' dotenv source reads the physical `.env` file
    independently of `os.environ` -- `monkeypatch.delenv("ANTHROPIC_API_KEY")`
    only removes the OS env var, it does not stop `Settings()` from still
    picking up a real `ANTHROPIC_API_KEY`/`GROQ_API_KEY` value sitting in
    `.env` (Step 163B: this is exactly what let a local `.env` placeholder
    key leak into `AnthropicAICandidateProposalProvider` and trigger a real
    outbound API call during `pytest`). Explicitly setting the OS env var
    instead -- even to `""` -- makes the higher-priority env source own
    that key so it always overrides `.env`, regardless of how a test
    constructs `Settings(...)` (via `get_settings()`, `Settings()`, or
    `Settings(<field_name>=None)` -- the last of which does NOT reliably
    override a `.env` value on its own, since pydantic-settings merges the
    dotenv source under the field's alias while an init kwarg passed by
    field name is stored under a different dict key).

    A test that wants to exercise non-default config still can, either by
    calling `monkeypatch.setenv(...)` itself (this fixture's overrides
    apply first in the same test, so a later call in the test body wins),
    or by monkeypatching `get_settings` / constructing `Settings(...)`
    directly, which bypasses environment resolution entirely.
    """
    monkeypatch.setenv("AI_CANDIDATE_PROPOSAL_PROVIDER", "not_connected")
    monkeypatch.setenv("AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_MODEL", Settings.model_fields["anthropic_model"].default)
    monkeypatch.setenv("GROQ_MODEL", Settings.model_fields["groq_model"].default)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class DeterministicTestPlacesProvider(PlacesProvider):
    """Deterministic test double standing in for `OpenStreetMapPlacesAdapter`.

    Lets API smoke tests exercise the full pipeline without depending on
    real Overpass/Nominatim network availability. This data is test-only
    and is never wired into application runtime code; the `provider_name`
    matches the real OSM adapter so ProviderCoverageService labels coverage
    exactly as it would for a real OSM response (e.g. `accommodations:
    open_poi_available`).
    """

    provider_name = "openstreetmap_places"

    def search_attractions(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._test_provider_response(
            "attraction",
            [
                ("test/attraction/1", "Test Fixture Attraction One"),
                ("test/attraction/2", "Test Fixture Attraction Two"),
            ],
        )

    def search_restaurants(
        self, area: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._test_provider_response(
            "restaurant", [("test/restaurant/1", "Test Fixture Restaurant One")]
        )

    def search_accommodation_pois(
        self, destination: str, filters: dict[str, Any] | None = None
    ) -> ProviderResponse[Any]:
        return self._test_provider_response(
            "hotel", [("test/accommodation/1", "Test Fixture Accommodation One")]
        )

    def _test_provider_response(
        self, category: str, entries: list[tuple[str, str]]
    ) -> ProviderResponse[Any]:
        places = [
            NormalizedPlace(
                place_id=place_id,
                name=name,
                category=category,
                coordinates=GeoPoint(lat=0.0, lng=0.0),
                source=self.provider_name,
                data_status=DataStatus.LIVE,
                confidence=0.6,
            )
            for place_id, name in entries
        ]
        return ProviderResponse[list[NormalizedPlace]](
            provider_name=self.provider_name,
            provider_type=self.provider_type,
            status=ProviderStatus.SUCCESS,
            data_status=DataStatus.LIVE,
            data=places,
            confidence=0.65,
            message="Test fixture data; not a real provider call.",
        )


@pytest.fixture(autouse=True)
def _deterministic_places_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real OSM adapter with a deterministic test double for every test."""
    monkeypatch.setattr(provider_gateway, "places", DeterministicTestPlacesProvider())


@pytest.fixture(autouse=True)
def _reset_in_memory_repositories(tmp_path: Path) -> None:
    """Isolate the trip/planning-state/user repositories between test
    functions.

    Points all three module-level repository singletons at a fresh
    temporary JSON file (shared between them, matching how they share the
    real storage file in app.core.config.Settings.local_storage_path)
    instead of the real local development storage file, so test runs
    never read or write persistent project data under backend/.data/.
    """
    test_store = LocalJsonStore(tmp_path / "test_travelobligator_state.json")
    trip_repository._store = test_store
    trip_repository._trips = {}
    planning_state_repository._store = test_store
    planning_state_repository._states = {}
    user_repository._store = test_store
    user_repository._users = {}
    # Step 186B: job_repository (backend/app/repositories/job_repository.py)
    # shares the same underlying file as the other three, under its own
    # "jobs" collection -- reset here for the same isolation reason.
    job_repository._store = test_store
    job_repository._jobs = {}
    # Section 199A: itinerary_lineage_repository (branches/revisions)
    # shares the same underlying file as the other four, under its own
    # two new collections -- reset here for the same isolation reason.
    itinerary_lineage_repository._store = test_store
    itinerary_lineage_repository._branches = {}
    itinerary_lineage_repository._revisions = {}
    yield


@pytest.fixture(autouse=True)
def _configured_session_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Step 184D: every `/trips/*` route now requires a real, verified
    session, so every test that hits one (which is most of this suite)
    needs a working `SESSION_SECRET_KEY` -- made autouse here rather than
    per-file opt-in (the pattern Step 184B/184C's own auth-specific test
    files used) precisely because it's no longer an auth-specific
    concern, it's a whole-suite one.

    This is a real, working secret enabling real signed-cookie
    verification -- exactly as a real deployment configures one via
    `.env` -- just a fixed, non-secret test value instead of a real one.
    Not a bypass: `client` below still performs a genuine
    `POST /auth/signup` to obtain its session; no route/dependency is
    ever skipped or faked.
    """
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-only-session-secret-for-pytest")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_provider_cache_store(monkeypatch: pytest.MonkeyPatch):
    """Isolate the provider cache singleton (`get_provider_cache_store`,
    Steps 164A-164G) between test functions, mirroring
    `_reset_in_memory_repositories` above for the same reason.

    `get_provider_cache_store` is a process-wide singleton keyed by
    resolved path (`backend/app/storage/provider_cache_store.py`). Any real
    provider adapter (`OpenMeteoWeatherAdapter`, `NagerDateHolidaysAdapter`,
    `FrankfurterCurrencyAdapter`, `OpenStreetMapPlacesAdapter`,
    `OSRMRoutingAdapter`, `ScrapedLocalHotelRatingsProvider`) constructed
    without an explicit `cache_store` --
    e.g. a test that monkeypatches
    `provider_gateway.places` with a real `OpenStreetMapPlacesAdapter()` to
    exercise its real containment/normalization logic against a fake HTTP
    client -- would otherwise lazily resolve and share the *same* real
    cache store across the whole test session (and, without this fixture,
    the real repo-local `.data/provider_cache.sqlite3` file). That let one
    test's cached, successful POI/geocode/weather/holiday/currency result
    silently satisfy a *different* test's fake-client expectations instead
    of exercising the fake client at all.

    Each adapter module binds `get_provider_cache_store` as a local name at
    import time (`from ... import get_provider_cache_store`), so it must be
    patched per-module, not on the source module alone. The store's file
    lives in its own throwaway `tempfile.mkdtemp()` directory -- deliberately
    *not* a test's own `tmp_path` fixture, since eagerly creating a file
    there would break tests that assert on `tmp_path`'s exact contents
    (e.g. `test_write_is_atomic_and_leaves_no_temp_files`).
    """
    import shutil
    import tempfile

    import app.providers.accommodation.scraped_adapter as scraped_accommodation_adapter_module
    import app.providers.currency.frankfurter_adapter as frankfurter_adapter_module
    import app.providers.flights.scraped_adapter as scraped_flight_adapter_module
    import app.providers.holidays.nager_date_adapter as nager_date_adapter_module
    import app.providers.hotel_ratings.scraped_adapter as scraped_hotel_ratings_adapter_module
    import app.providers.places.openstreetmap_adapter as openstreetmap_adapter_module
    import app.providers.routing.osrm_adapter as osrm_adapter_module
    import app.providers.weather.open_meteo_adapter as open_meteo_adapter_module

    isolation_dir = Path(tempfile.mkdtemp(prefix="travelobligator_test_provider_cache_"))
    fresh_store = ProviderCacheStore(isolation_dir / "test_provider_cache.sqlite3")
    for adapter_module in (
        openstreetmap_adapter_module,
        open_meteo_adapter_module,
        nager_date_adapter_module,
        frankfurter_adapter_module,
        osrm_adapter_module,
        scraped_accommodation_adapter_module,
        scraped_flight_adapter_module,
        scraped_hotel_ratings_adapter_module,
    ):
        monkeypatch.setattr(adapter_module, "get_provider_cache_store", lambda path: fresh_store)

    yield
    shutil.rmtree(isolation_dir, ignore_errors=True)


@pytest.fixture()
def async_generation_enabled(monkeypatch: pytest.MonkeyPatch):
    """Step 186C: opts a single test into `ASYNC_GENERATION_ENABLED=true`
    -- deliberately NOT autouse, since the default (`false`) synchronous
    behavior is what almost every existing test in this suite exercises
    and must keep exercising unchanged. `get_settings()` reads config
    fresh on every call (never cached across a config change within a
    test), matching every other config-toggling fixture in this file.
    """
    monkeypatch.setenv("ASYNC_GENERATION_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _unique_test_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


def new_authenticated_client() -> TestClient:
    """Returns a fresh `TestClient` that has already completed a real
    `POST /auth/signup` (a unique, throwaway user every call) -- so it
    carries a real, working session cookie for its lifetime, exactly as a
    logged-in browser would. No test-only auth bypass: this is the exact
    same signup endpoint `test_auth_routes.py` exercises directly.

    Used directly by tests that need a second, distinct user (e.g. cross-
    user 403 isolation checks) -- the shared `client` fixture below always
    represents "the current test's own logged-in user."
    """
    test_client = TestClient(app)
    signup_response = test_client.post(
        "/auth/signup",
        json={"email": _unique_test_email(), "password": "testpassword123"},
    )
    assert signup_response.status_code == 201, signup_response.text
    return test_client


@pytest.fixture()
def client() -> TestClient:
    """Step 184D: every `/trips/*` route requires a real session, so this
    shared fixture -- used by the large majority of this suite's existing
    tests -- now signs up a fresh, unique user before handing back the
    client, exactly like `new_authenticated_client()` above. This is why
    every pre-existing test that creates/reads/mutates a trip via `client`
    keeps passing unmodified after this step: it was always implicitly
    "the trip owner," it just didn't need to say so before.
    """
    return new_authenticated_client()


@pytest.fixture()
def second_client() -> TestClient:
    """A second, distinct logged-in user -- for cross-user isolation
    tests that need to prove `second_client` cannot read/mutate whatever
    `client` created."""
    return new_authenticated_client()


def create_trip_payload() -> dict[str, Any]:
    return {
        "destination_scope": "single_city",
        "primary_destination": "Testville, Testland",
        "origin_city": "Home City",
        "start_date": "2026-08-10",
        "end_date": "2026-08-12",
        "travelers_count": 2,
        "travel_group_type": "couple",
    }


@pytest.fixture()
def created_trip_id(client: TestClient) -> str:
    response = client.post("/trips", json=create_trip_payload())
    assert response.status_code == 201
    return response.json()["data"]["trip_id"]


@pytest.fixture()
def generated_trip_id(client: TestClient, created_trip_id: str) -> str:
    response = client.post(f"/trips/{created_trip_id}/generate")
    assert response.status_code == 200
    return created_trip_id
