from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[2] is the backend/ project root, so
# a relative local_storage_path resolves the same way whether the app is
# started from backend/ (local dev, Docker WORKDIR) or from the repo root.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = Field(default="TravelObligator", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")

    backend_host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    backend_cors_origins: str = Field(
        default="http://localhost:3000",
        alias="BACKEND_CORS_ORIGINS",
    )

    database_url: str = Field(
        default="postgresql://travelobligator_user:change_me@postgres:5432/travelobligator",
        alias="DATABASE_URL",
    )

    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")

    # Claude/Anthropic is the selected LLM base for AI candidate proposals
    # (Step 161A, docs/13_llm_reasoning_pipeline.md section 39). Missing
    # `anthropic_api_key` must never crash default app/test behavior --
    # `AnthropicAICandidateProposalProvider.propose` returns an honest
    # `not_connected` result instead of raising.
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL")

    # Groq is a second, cheap/dev-only LLM base for AI candidate proposals
    # (Step 162A, docs/13_llm_reasoning_pipeline.md section 41). Like
    # `anthropic_api_key`, a missing `groq_api_key` must never crash default
    # app/test behavior -- `GroqAICandidateProposalProvider.propose` returns
    # an honest `not_connected` result instead of raising.
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    groq_model: str = Field(default="openai/gpt-oss-20b", alias="GROQ_MODEL")

    # Config gate for get_ai_candidate_proposal_provider (Step 160E,
    # extended in Step 161A). "not_connected" (default) and "anthropic" are
    # the only supported values today. An unsupported/unrecognized value
    # falls back to "not_connected" rather than raising or fabricating
    # output -- see backend/app/providers/ai_candidate_proposal/factory.py.
    ai_candidate_proposal_provider: str = Field(
        default="not_connected", alias="AI_CANDIDATE_PROPOSAL_PROVIDER"
    )

    # Config gate for PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage
    # (Step 161B, docs/13_llm_reasoning_pipeline.md section 40). Default is
    # False so normal generation is completely unaffected -- when disabled,
    # AICandidateDiscoveryService.dry_run is never called during generation
    # and ai_candidate_proposal_batch/candidate_grounding_batch stay None,
    # exactly as before this step.
    ai_candidate_discovery_shadow_mode_enabled: bool = Field(
        default=False,
        alias="AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED",
    )

    google_places_api_key: str | None = Field(default=None, alias="GOOGLE_PLACES_API_KEY")
    google_routes_api_key: str | None = Field(default=None, alias="GOOGLE_ROUTES_API_KEY")
    mapbox_access_token: str | None = Field(default=None, alias="MAPBOX_ACCESS_TOKEN")

    amadeus_client_id: str | None = Field(default=None, alias="AMADEUS_CLIENT_ID")
    amadeus_client_secret: str | None = Field(default=None, alias="AMADEUS_CLIENT_SECRET")

    overpass_api_url: str = Field(
        default="https://overpass-api.de/api/interpreter",
        alias="OVERPASS_API_URL",
    )
    nominatim_api_url: str = Field(
        default="https://nominatim.openstreetmap.org",
        alias="NOMINATIM_API_URL",
    )
    open_meteo_api_url: str = Field(
        default="https://api.open-meteo.com",
        alias="OPEN_METEO_API_URL",
    )
    nager_date_api_url: str = Field(
        default="https://date.nager.at",
        alias="NAGER_DATE_API_URL",
    )
    frankfurter_api_url: str = Field(
        default="https://api.frankfurter.app",
        alias="FRANKFURTER_API_URL",
    )

    use_real_providers: bool = Field(default=True, alias="USE_REAL_PROVIDERS")
    allow_mock_travel_facts: bool = Field(default=False, alias="ALLOW_MOCK_TRAVEL_FACTS")

    local_storage_path: str = Field(
        default=".data/travelobligator_state.json",
        alias="LOCAL_STORAGE_PATH",
    )

    # Provider cache foundation (Step 164A,
    # docs/12_provider_architecture.md "Provider Cache Foundation" section).
    # Declared here so a later step can wire provider adapters to
    # `ProviderCacheStore` without a config change -- no adapter reads
    # `provider_cache_enabled` yet, and this step does not change any
    # provider behavior.
    provider_cache_path: str = Field(
        default=".data/provider_cache.sqlite3",
        alias="PROVIDER_CACHE_PATH",
    )
    provider_cache_enabled: bool = Field(
        default=True,
        alias="PROVIDER_CACHE_ENABLED",
    )

    # Open-Meteo cache TTL (Step 164B, docs/12_provider_architecture.md
    # "Provider Cache Foundation" section). Only `OpenMeteoWeatherAdapter`
    # reads this -- no other provider is wired to the cache yet. Must be
    # non-negative; a negative TTL has no sane meaning for `ProviderCacheStore.set`.
    open_meteo_cache_ttl_seconds: int = Field(
        default=3600,
        alias="OPEN_METEO_CACHE_TTL_SECONDS",
        ge=0,
    )

    # Nager.Date cache TTL (Step 164C, docs/12_provider_architecture.md
    # "Provider Cache Foundation" section). Only `NagerDateHolidaysAdapter`
    # reads this. 30 days is acceptable here because public holiday
    # calendars change slowly once published for a given year/country, but
    # it stays configurable. Must be non-negative, matching
    # `open_meteo_cache_ttl_seconds`.
    nager_date_cache_ttl_seconds: int = Field(
        default=2592000,
        alias="NAGER_DATE_CACHE_TTL_SECONDS",
        ge=0,
    )

    # Frankfurter cache TTL (Step 164D, docs/12_provider_architecture.md
    # "Provider Cache Foundation" section). Only `FrankfurterCurrencyAdapter`
    # reads this. 6 hours is acceptable here because this app's currency
    # data can change but not minute-by-minute, but it stays configurable.
    # Must be non-negative, matching the other provider cache TTL settings.
    frankfurter_cache_ttl_seconds: int = Field(
        default=21600,
        alias="FRANKFURTER_CACHE_TTL_SECONDS",
        ge=0,
    )

    # OpenStreetMap/Nominatim geocode cache TTL (Step 164E,
    # docs/12_provider_architecture.md "Provider Cache Foundation" section).
    # Only `OpenStreetMapPlacesAdapter`'s destination-geocode path
    # (`_resolve_destination`) reads this -- Overpass POI searches are not
    # cache-wired yet. 30 days is acceptable here because geocoding a given
    # destination string changes slowly, but it stays configurable. Must be
    # non-negative, matching the other provider cache TTL settings.
    osm_geocode_cache_ttl_seconds: int = Field(
        default=2592000,
        alias="OSM_GEOCODE_CACHE_TTL_SECONDS",
        ge=0,
    )

    # OpenStreetMap/Overpass POI search cache TTL (Step 164G,
    # docs/12_provider_architecture.md "Provider Cache Foundation" section).
    # Only `OpenStreetMapPlacesAdapter`'s Overpass POI search path
    # (`_try_query`, used by attractions/restaurants/accommodation search)
    # reads this -- geocoding uses `osm_geocode_cache_ttl_seconds` instead.
    # 7 days is shorter than the geocode TTL because POI data changes more
    # often than geocoding but not every minute, and it stays configurable.
    # Must be non-negative, matching the other provider cache TTL settings.
    osm_poi_cache_ttl_seconds: int = Field(
        default=604800,
        alias="OSM_POI_CACHE_TTL_SECONDS",
        ge=0,
    )

    # Routing provider skeleton (Step 165A, docs/12_provider_architecture.md
    # section 31, docs/13_llm_reasoning_pipeline.md section 54). "not_connected"
    # (default) and "osrm" are the only supported values -- see
    # backend/app/providers/routing/factory.py. Not wired into
    # ProviderGateway, PlanningOrchestrator, ExperiencePlannerService, or
    # PlanValidatorService yet.
    routing_provider: str = Field(default="not_connected", alias="ROUTING_PROVIDER")

    # `osrm_base_url` is deliberately unset by default -- a conservative
    # choice matching `anthropic_api_key`/`groq_api_key` above, so no OSRM
    # instance (public demo or self-hosted) is silently used without an
    # explicit developer choice. `OSRMRoutingAdapter.get_route` returns an
    # honest `not_connected` result when this is unset, even if
    # `routing_provider="osrm"` is also set. A real deployment might set
    # this to a self-hosted OSRM instance or the public OSRM demo server
    # (https://router.project-osrm.org) -- never assumed here.
    osrm_base_url: str | None = Field(default=None, alias="OSRM_BASE_URL")
    osrm_timeout_seconds: float = Field(default=15.0, alias="OSRM_TIMEOUT_SECONDS", ge=0.0)
    osrm_profile: str = Field(default="driving", alias="OSRM_PROFILE")

    # OSRM route cache TTL (Step 165C, docs/12_provider_architecture.md
    # "Provider Cache Foundation" section). Only `OSRMRoutingAdapter` reads
    # this. 24 hours is shorter than the geocode TTL because route
    # conditions (e.g. road network changes) can shift, but a route is
    # still reasonably stable within a day; it stays configurable. Must be
    # non-negative, matching the other provider cache TTL settings.
    osrm_route_cache_ttl_seconds: int = Field(
        default=86400,
        alias="OSRM_ROUTE_CACHE_TTL_SECONDS",
        ge=0,
    )

    # Config gate for RouteAwareSequencingService.apply_report (Step 166B,
    # docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
    # docs/14_backend_architecture.md). Default is False so normal
    # generation is completely unaffected -- when disabled,
    # PlanningOrchestrator never calls apply_report, and
    # route_aware_sequencing_report stays exactly as Step 166A left it
    # (is_shadow_only=True, applied_to_itinerary=False, scheduled
    # itinerary order unchanged).
    route_aware_scheduling_enabled: bool = Field(
        default=False,
        alias="ROUTE_AWARE_SCHEDULING_ENABLED",
    )

    # Minimum real, provider-backed improvement (in seconds) a day's
    # route-aware sequencing suggestion must show before it is ever
    # applied, even when route_aware_scheduling_enabled is True.
    # Conservative default of 0.0 combined with a strict "greater than"
    # comparison in RouteAwareSequencingService.apply_report means only a
    # suggestion with a genuinely positive real improvement is ever
    # applied -- a suggestion with zero or negative improvement never
    # changes the schedule. Must be non-negative.
    route_aware_scheduling_min_improvement_seconds: float = Field(
        default=0.0,
        alias="ROUTE_AWARE_SCHEDULING_MIN_IMPROVEMENT_SECONDS",
        ge=0.0,
    )

    # Config gate for get_accommodation_provider (Step 167B, extended in
    # Step 168C, default flipped in Step 168F). "not_connected" and
    # "scraped_local" (default) are the only supported values today -- see
    # backend/app/providers/accommodation/factory.py. An unsupported/
    # unrecognized value falls back to "not_connected" rather than raising
    # or fabricating lodging inventory. Not wired into ProviderGateway or
    # PlanningOrchestrator yet.
    accommodation_provider: str = Field(
        default="scraped_local", alias="ACCOMMODATION_PROVIDER"
    )

    # Scraping policy foundation (Step 168A, default flipped to enabled in
    # Step 168F, docs/12_provider_architecture.md, docs/14_backend_architecture.md).
    # Enabled by default so `ScrapedAccommodationProvider` (the default
    # accommodation provider as of Step 168F) can actually run -- but this
    # never causes a network call or fabricates data by itself.
    # `ScrapingSourceRegistry` (backend/app/models/scraping.py) still only
    # ever returns sources that are both explicitly `enabled=True` on
    # their own `ScrapingSourcePolicy` and safe (not login-required/
    # paywalled/captcha-expected, and `approved_for_personal_use=True`) --
    # this app-wide flag is a second, coarser gate, never a substitute for
    # per-source approval.
    scraping_enabled: bool = Field(default=True, alias="SCRAPING_ENABLED")

    # Config gate for the scraped-data accommodation provider slot (Step
    # 168A, default flipped to enabled in Step 168F). Enabled by default
    # alongside `scraping_enabled` and `accommodation_provider=
    # "scraped_local"` so the default accommodation provider is
    # `ScrapedAccommodationProvider` -- but with no local HTML file at
    # `scraped_accommodation_html_path` present, that provider still
    # returns an honest `unavailable` result, never a fabricated one (see
    # `ScrapedAccommodationProvider.search_accommodations`).
    scraped_accommodation_provider_enabled: bool = Field(
        default=True, alias="SCRAPED_ACCOMMODATION_PROVIDER_ENABLED"
    )

    # Conservative default per-source rate limit (seconds), for a future
    # scraper to fall back on when a `ScrapingSourcePolicy` doesn't specify
    # its own. Not read by any code path yet -- Step 168A only defines the
    # policy/registry/provenance contract, not a live scraper. Must be
    # non-negative, matching the other cache/rate-limit-style settings.
    scraping_default_rate_limit_seconds: int = Field(
        default=10, alias="SCRAPING_DEFAULT_RATE_LIMIT_SECONDS", ge=0
    )

    # Config for the manual/local scraped accommodation provider (Step
    # 168C, default path added in Step 168F,
    # docs/12_provider_architecture.md, docs/14_backend_architecture.md,
    # backend/app/providers/accommodation/scraped_adapter.py). This
    # provider never fetches a live website: `scraped_accommodation_
    # html_path` must point at a local file the developer/operator
    # supplies themselves (e.g. by manually saving a page from a browser).
    # The default path is relative to the backend project root (mirroring
    # `local_storage_path`/`provider_cache_path` above) and is not created
    # automatically -- when no file exists there, `ScrapedAccommodationProvider`
    # honestly reports `unavailable` with empty offers, never a fabricated
    # or placeholder offer.
    scraped_accommodation_html_path: str | None = Field(
        default=".data/manual_scrapes/accommodations.html",
        alias="SCRAPED_ACCOMMODATION_HTML_PATH",
    )
    scraped_accommodation_source_id: str = Field(
        default="manual_local_scraped_accommodation",
        alias="SCRAPED_ACCOMMODATION_SOURCE_ID",
    )
    scraped_accommodation_source_name: str = Field(
        default="Manual local scraped accommodation source",
        alias="SCRAPED_ACCOMMODATION_SOURCE_NAME",
    )
    scraped_accommodation_base_url: str | None = Field(
        default=None, alias="SCRAPED_ACCOMMODATION_BASE_URL"
    )

    # Cache for the local/manual scraped accommodation provider (Step
    # 168D, docs/12_provider_architecture.md, docs/14_backend_architecture.md,
    # "Provider Cache Foundation" section). Separate from the global
    # `provider_cache_enabled` flag so the experimental scraped path can be
    # toggled independently of every real provider's cache. Caches only
    # the normalized `AccommodationSearchResult` payload -- never the raw
    # HTML file content. Enabled by default because caching itself never
    # causes a network call or changes what data is returned, only how
    # often the local file is re-read/re-parsed; the cache key includes
    # the file's mtime/size, so an edited local file is never served stale
    # cached data.
    scraped_accommodation_cache_enabled: bool = Field(
        default=True, alias="SCRAPED_ACCOMMODATION_CACHE_ENABLED"
    )
    scraped_accommodation_cache_ttl_seconds: int = Field(
        default=3600, alias="SCRAPED_ACCOMMODATION_CACHE_TTL_SECONDS", ge=0
    )

    # Config gate for get_flight_provider (Step 169B,
    # docs/12_provider_architecture.md, docs/14_backend_architecture.md).
    # "not_connected" and "scraped_local" (default) are the only supported
    # values today -- see backend/app/providers/flights/factory.py. An
    # unsupported/unrecognized value falls back to "not_connected" rather
    # than raising or fabricating flight inventory. Default matches
    # accommodation's Step 168F decision: flight scraping is on by
    # default, the same way accommodation scraping is -- but this never
    # itself causes a network call or fabricates data. Not wired into
    # ProviderGateway or PlanningOrchestrator yet.
    flight_provider: str = Field(default="scraped_local", alias="FLIGHT_PROVIDER")

    # Config gate for the scraped-data flight provider slot (Step 169B),
    # mirroring `scraped_accommodation_provider_enabled`. Enabled by
    # default alongside `scraping_enabled` and
    # `flight_provider="scraped_local"` so the default flight provider is
    # `ScrapedLocalFlightProvider` -- but with no flight HTML parser
    # implemented yet (Step 169C) and no local HTML file present, that
    # provider still returns an honest `unavailable` result, never a
    # fabricated one (see `ScrapedLocalFlightProvider.search_flights`).
    scraped_flight_provider_enabled: bool = Field(
        default=True, alias="SCRAPED_FLIGHT_PROVIDER_ENABLED"
    )

    # Config for the manual/local scraped flight provider (Step 169B,
    # parser added in Step 169C, provider wired to the parser+cache in
    # Step 169D, docs/12_provider_architecture.md,
    # docs/14_backend_architecture.md,
    # backend/app/providers/flights/scraped_adapter.py). This provider
    # never fetches a live website: `scraped_flight_html_path` must point
    # at a local file the developer/operator supplies themselves (e.g. by
    # manually saving a page from a browser). The default path is
    # relative to the backend project root (mirroring
    # `scraped_accommodation_html_path` above) and is not created
    # automatically -- when no file exists there, `ScrapedLocalFlightProvider`
    # honestly reports `unavailable` with empty offers, never a fabricated
    # or placeholder offer. As of Step 169D, when a file does exist, it is
    # read and parsed via `parse_scraped_flight_html` (Step 169C).
    scraped_flight_html_path: str | None = Field(
        default=".data/manual_scrapes/flights.html",
        alias="SCRAPED_FLIGHT_HTML_PATH",
    )
    scraped_flight_source_id: str = Field(
        default="manual_local_scraped_flight",
        alias="SCRAPED_FLIGHT_SOURCE_ID",
    )
    scraped_flight_source_name: str = Field(
        default="Manual local scraped flight source",
        alias="SCRAPED_FLIGHT_SOURCE_NAME",
    )
    scraped_flight_base_url: str | None = Field(default=None, alias="SCRAPED_FLIGHT_BASE_URL")

    # Cache for the local/manual scraped flight provider (Step 169D,
    # docs/12_provider_architecture.md, docs/14_backend_architecture.md,
    # "Provider Cache Foundation" section), mirroring
    # `scraped_accommodation_cache_enabled`/`scraped_accommodation_cache_
    # ttl_seconds` (Step 168D). Separate from the global
    # `provider_cache_enabled` flag so the experimental scraped-flight path
    # can be toggled independently of every real provider's cache. Caches
    # only the normalized `FlightSearchResult` payload -- never the raw
    # HTML file content. Enabled by default because caching itself never
    # causes a network call or changes what data is returned, only how
    # often the local file is re-read/re-parsed; the cache key includes
    # the file's mtime/size, so an edited local file is never served stale
    # cached data.
    scraped_flight_cache_enabled: bool = Field(
        default=True, alias="SCRAPED_FLIGHT_CACHE_ENABLED"
    )
    scraped_flight_cache_ttl_seconds: int = Field(
        default=3600, alias="SCRAPED_FLIGHT_CACHE_TTL_SECONDS", ge=0
    )

    # Config gate for POST /trips/{trip_id}/generate's engine selection
    # (Step 171D, made the default in Step 171E once the LangGraph path
    # reached stage parity with legacy -- docs/13_llm_reasoning_pipeline.md,
    # docs/14_backend_architecture.md). "langgraph" (default as of Step
    # 171E) and "legacy" are the only supported values today -- see
    # app.api.routes.trips.generate_trip_plan. Mirrors the existing
    # `accommodation_provider`/`flight_provider`/`routing_provider`
    # convention: an unsupported/unrecognized value falls back to
    # "legacy" (PlanningOrchestrator.generate_full_plan) rather than
    # raising, silently doing nothing, or defaulting to the newer engine --
    # only an explicit exact "langgraph" (or the default) selects the
    # LangGraph path. Changing this value never itself calls a provider/
    # LLM/network service, and never changes itinerary scheduling,
    # route-aware scheduling, or regeneration behavior -- both engines call
    # the exact same deterministic stage services, in the same relative
    # order, either way. Set PLANNING_ENGINE_MODE=legacy to keep using the
    # original hand-written orchestrator loop.
    planning_engine_mode: str = Field(default="langgraph", alias="PLANNING_ENGINE_MODE")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Lets `Settings(...)` be constructed with plain field names
        # (e.g. `Settings(groq_api_key=None)`) in addition to the alias/env
        # var name (`Settings(GROQ_API_KEY=None)`) -- both now bind the same
        # field. Purely additive: env var / .env resolution still goes
        # through each field's `alias` exactly as before (Step 162A fix,
        # docs/13_llm_reasoning_pipeline.md section 41).
        populate_by_name=True,
    )

    def resolved_local_storage_path(self) -> Path:
        """Local development storage path, not a production database.

        Resolved against the backend project root (not the process's
        current working directory) so the default value works the same way
        regardless of where the app was started from.
        """
        path = Path(self.local_storage_path)
        return path if path.is_absolute() else _BACKEND_ROOT / path

    def resolved_provider_cache_path(self) -> Path:
        """Local provider-cache SQLite path, not a production database.

        Mirrors `resolved_local_storage_path` -- resolved against the
        backend project root so the default value works the same way
        regardless of where the app was started from.
        """
        path = Path(self.provider_cache_path)
        return path if path.is_absolute() else _BACKEND_ROOT / path

    def resolved_scraped_accommodation_html_path(self) -> Path | None:
        """Local, manually-supplied scraped-accommodation HTML file path
        (Step 168C, default path added in Step 168F) -- never a live
        website URL. Mirrors `resolved_local_storage_path`/
        `resolved_provider_cache_path`: a relative value (including the
        default) resolves against the backend project root, not the
        process's current working directory, so the default path finds
        the same file regardless of where the app was started from.
        Returns `None` when unset (`scraped_accommodation_html_path=None`,
        e.g. explicitly cleared via config) -- callers must not assume a
        file exists at the resolved path either way.
        """
        if self.scraped_accommodation_html_path is None:
            return None
        path = Path(self.scraped_accommodation_html_path)
        return path if path.is_absolute() else _BACKEND_ROOT / path

    def resolved_scraped_flight_html_path(self) -> Path | None:
        """Local, manually-supplied scraped-flight HTML file path (Step
        169B) -- never a live website URL. Mirrors
        `resolved_scraped_accommodation_html_path`: a relative value
        (including the default) resolves against the backend project
        root, not the process's current working directory, so the
        default path finds the same file regardless of where the app was
        started from. Returns `None` when unset
        (`scraped_flight_html_path=None`, e.g. explicitly cleared via
        config) -- callers must not assume a file exists at the resolved
        path either way.
        """
        if self.scraped_flight_html_path is None:
            return None
        path = Path(self.scraped_flight_html_path)
        return path if path.is_absolute() else _BACKEND_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()