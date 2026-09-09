from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Step 182E: allowed values for the optional manual/local HTML
# "source label" config (see `accommodation_manual_html_source`/
# `flight_manual_html_source` below). An unrecognized value never
# reaches an adapter -- the field validators below clamp it to
# `"generic"` instead, matching every other "unsupported value falls
# back safely" convention in this file (see e.g.
# `accommodation_provider`/`flight_provider`/`hotel_ratings_provider`
# and their factories). This label only ever changes provenance
# display text (source_id/source_name on a manually-supplied local
# HTML file's parsed offers) -- it never selects a different provider,
# never enables a live API call, and never implies the labeled site's
# official API was actually used.
_ALLOWED_ACCOMMODATION_MANUAL_HTML_SOURCES = frozenset(
    {"generic", "booking", "expedia", "hotelbeds", "hostelworld", "vrbo", "airbnb"}
)
_ALLOWED_FLIGHT_MANUAL_HTML_SOURCES = frozenset(
    {"generic", "skyscanner", "google_flights", "kiwi", "other"}
)

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

    # Step 182E: partner/paid-access provider credential placeholders,
    # documented in README.md's "Provider activation" section. None of
    # these are read by any provider adapter or factory today -- declared
    # here only so `.env.example`/a real `.env` have a typed, tested place
    # to put a real credential *if and when* a real adapter for that
    # provider is implemented (with its own request/response schema and
    # tests, per this repo's no-fabrication policy). Setting any of these
    # today has zero effect on `accommodation_provider`/`flight_provider`/
    # `hotel_ratings_provider` provider selection -- those still only
    # recognize the provider names their own factories already support
    # (see `backend/app/providers/accommodation/factory.py`,
    # `backend/app/providers/flights/factory.py`,
    # `backend/app/providers/hotel_ratings/factory.py`), and an
    # unsupported/unrecognized provider name always falls back to
    # `not_connected` rather than fabricating data. Every field defaults
    # to `None` -- never a real key -- and `.env.example` only ever ships
    # empty placeholders.
    booking_demand_api_key: str | None = Field(default=None, alias="BOOKING_DEMAND_API_KEY")
    booking_demand_api_base_url: str | None = Field(
        default=None, alias="BOOKING_DEMAND_API_BASE_URL"
    )
    expedia_rapid_api_key: str | None = Field(default=None, alias="EXPEDIA_RAPID_API_KEY")
    expedia_rapid_api_secret: str | None = Field(default=None, alias="EXPEDIA_RAPID_API_SECRET")
    expedia_rapid_api_base_url: str | None = Field(
        default=None, alias="EXPEDIA_RAPID_API_BASE_URL"
    )
    hotelbeds_api_key: str | None = Field(default=None, alias="HOTELBEDS_API_KEY")
    hotelbeds_secret: str | None = Field(default=None, alias="HOTELBEDS_SECRET")
    hotelbeds_api_base_url: str | None = Field(default=None, alias="HOTELBEDS_API_BASE_URL")
    hostelworld_api_key: str | None = Field(default=None, alias="HOSTELWORLD_API_KEY")
    hostelworld_api_base_url: str | None = Field(default=None, alias="HOSTELWORLD_API_BASE_URL")
    vrbo_partner_api_key: str | None = Field(default=None, alias="VRBO_PARTNER_API_KEY")
    vrbo_partner_api_base_url: str | None = Field(
        default=None, alias="VRBO_PARTNER_API_BASE_URL"
    )
    airbnb_partner_api_key: str | None = Field(default=None, alias="AIRBNB_PARTNER_API_KEY")
    airbnb_partner_api_base_url: str | None = Field(
        default=None, alias="AIRBNB_PARTNER_API_BASE_URL"
    )
    skyscanner_api_key: str | None = Field(default=None, alias="SKYSCANNER_API_KEY")
    skyscanner_api_base_url: str | None = Field(default=None, alias="SKYSCANNER_API_BASE_URL")
    tripadvisor_api_key: str | None = Field(default=None, alias="TRIPADVISOR_API_KEY")
    tripadvisor_api_base_url: str | None = Field(
        default=None, alias="TRIPADVISOR_API_BASE_URL"
    )

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
    # made the default in Step 172A once its safety contract was judged
    # solid enough to trust by default -- docs/12_provider_architecture.md,
    # docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
    # Default `True` as of Step 172A: PlanningOrchestrator (and the
    # LangGraph engine's equivalent node) now calls apply_report by
    # default, but apply_report's own safety contract is unchanged and
    # still the only thing that decides whether anything actually
    # happens -- with the default `routing_provider="not_connected"`, no
    # routing provider is configured, every suggestion's status stays
    # non-`success`, and apply_report remains a complete no-op exactly as
    # before this step: route_aware_sequencing_report stays
    # `is_shadow_only=True`/`applied_to_itinerary=False`/
    # `status="not_connected"` and the scheduled itinerary order is
    # unchanged. Set ROUTE_AWARE_SCHEDULING_ENABLED=false for the explicit
    # opt-out back to Step 166A's original shadow/report-only-forever
    # behavior, matching every other config flag's fallback convention in
    # this codebase.
    route_aware_scheduling_enabled: bool = Field(
        default=True,
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

    # Config gate for get_hotel_ratings_provider (Step 177B,
    # docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
    # "not_connected" (default, and the only supported value as of Step
    # 177B) -- see backend/app/providers/hotel_ratings/factory.py. An
    # unsupported/unrecognized value falls back to "not_connected" rather
    # than raising or fabricating rating data. No real Google Places/
    # Tripadvisor/Amadeus/Yelp rating provider is wired in yet -- every
    # such adapter would require its own conservative identity-matching
    # design (no fuzzy matching), deferred to a later step. Not wired
    # into ProviderGateway, PlanningOrchestrator, or
    # AccommodationInventoryService yet.
    hotel_ratings_provider: str = Field(
        default="not_connected", alias="HOTEL_RATINGS_PROVIDER"
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

    # Optional provenance "source label" for the manual/local scraped
    # accommodation file (Step 182E, docs/12_provider_architecture.md).
    # Purely cosmetic: it only changes the displayed `source_id`/
    # `source_name` on offers parsed from `scraped_accommodation_html_path`
    # (see `ScrapedAccommodationProvider`) -- and only when the operator
    # has not already customized `scraped_accommodation_source_id`/
    # `scraped_accommodation_source_name` themselves. `"generic"`
    # (default) leaves both fields completely unchanged. Setting this to
    # e.g. `"booking"` never means real Booking.com API data -- it means
    # "this manually-saved local HTML file is labeled by the user as
    # Booking.com-derived," and the generated source_name always says so
    # explicitly ("not official Booking.com data"). An unrecognized value
    # falls back to `"generic"` rather than raising.
    accommodation_manual_html_source: str = Field(
        default="generic", alias="ACCOMMODATION_MANUAL_HTML_SOURCE"
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

    # Optional provenance "source label" for the manual/local scraped
    # flight file (Step 182E), mirroring
    # `accommodation_manual_html_source` above. `"kiwi"` here means "this
    # manually-saved local HTML file is labeled by the user as
    # Kiwi-derived" -- it is a completely separate config surface from
    # `flight_provider="kiwi_mcp"`/`kiwi_mcp_enabled` (the real, live Kiwi
    # MCP integration below) and never causes a network call or implies
    # live Kiwi MCP data. An unrecognized value falls back to `"generic"`.
    flight_manual_html_source: str = Field(
        default="generic", alias="FLIGHT_MANUAL_HTML_SOURCE"
    )

    # Kiwi MCP flight provider foundation (Step 178B,
    # docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
    # docs/14_backend_architecture.md,
    # backend/app/providers/flights/kiwi_mcp_client.py,
    # backend/app/providers/flights/kiwi_mcp_adapter.py). Unlike every
    # other provider flag in this file, this one gates a *real, live*
    # network integration (Kiwi's hosted MCP server at
    # `kiwi_mcp_endpoint`) -- so it defaults to fully disabled, and
    # `flight_provider` itself still defaults to `"scraped_local"` above,
    # never `"kiwi_mcp"`. Selecting `flight_provider="kiwi_mcp"` alone
    # does nothing live: `KiwiMcpFlightProvider.search_flights` still
    # returns an honest `not_connected` result unless `kiwi_mcp_enabled`
    # is also explicitly set to `true` -- two independent opt-ins are
    # required, mirroring how `AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED`
    # gates a real Anthropic call separately from
    # `AI_CANDIDATE_PROPOSAL_PROVIDER=anthropic` selecting the adapter.
    # As of Step 178B, even when enabled, this only performs MCP tool
    # *discovery* (`initialize`/`list_tools`) -- no search tool is ever
    # called, and no `FlightOffer` is ever constructed from MCP data; see
    # that adapter's own docstring. This is a dev-workflow-adjacent
    # capability, not something a deployed instance should enable by
    # default -- distinct from Claude Code's own local
    # `claude mcp add --transport http kiwi-com-flight-search
    # https://mcp.kiwi.com` command, which configures the Claude Code CLI's
    # *own* MCP client for interactive coding sessions and has no
    # relationship to this backend's runtime configuration at all.
    kiwi_mcp_enabled: bool = Field(default=False, alias="KIWI_MCP_ENABLED")
    kiwi_mcp_endpoint: str = Field(
        default="https://mcp.kiwi.com", alias="KIWI_MCP_ENDPOINT"
    )
    kiwi_mcp_timeout_seconds: float = Field(
        default=10.0, alias="KIWI_MCP_TIMEOUT_SECONDS", gt=0.0
    )
    # Optional explicit tool name for a future step (178C) to target
    # exactly, once the real Kiwi MCP flight-search tool's name is known
    # from live discovery -- unset by default, since this audit/step does
    # not assume that name. When unset, `KiwiMcpFlightProvider` falls back
    # to a conservative, non-fabricating heuristic (a discovered tool
    # whose name or description mentions "flight") purely to decide
    # *whether* flight-search tooling exists, never to call it.
    kiwi_mcp_tool_name: str | None = Field(default=None, alias="KIWI_MCP_TOOL_NAME")

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

    # Itinerary narrator (Step 182F, docs/13_llm_reasoning_pipeline.md,
    # docs/14_backend_architecture.md). A separate, optional, read-only
    # LLM feature -- deliberately its own config surface, never reusing
    # `ai_candidate_discovery_shadow_mode_enabled`/
    # `ai_candidate_proposal_provider` (those gate the unrelated AI
    # candidate-proposal/grounding pipeline). Off by default
    # (`itinerary_narrator_enabled=False`); even when enabled,
    # `itinerary_narrator_provider` still defaults to `"not_connected"`,
    # matching every other provider-selection field's safe-default
    # convention in this file. The narrator only ever reads an
    # already-computed `PlanningState` (see
    # `ItineraryNarrativeRequestBuilder`) and writes prose to
    # `PlanningState.itinerary_narrative_report` -- it never mutates
    # `experience_plan`, `validation_report`, `provider_coverage`,
    # `regeneration_readiness`, or any other factual field, and
    # generation/regeneration must succeed whether or not it is enabled
    # or fails.
    itinerary_narrator_enabled: bool = Field(
        default=False, alias="ITINERARY_NARRATOR_ENABLED"
    )
    itinerary_narrator_provider: str = Field(
        default="not_connected", alias="ITINERARY_NARRATOR_PROVIDER"
    )
    # `None` (default) means "use that provider's own default model" --
    # `anthropic_model`/`groq_model` above -- exactly like
    # `AnthropicAICandidateProposalProvider`/`GroqAICandidateProposalProvider`
    # already do for the unrelated AI candidate-proposal feature.
    itinerary_narrator_model: str | None = Field(
        default=None, alias="ITINERARY_NARRATOR_MODEL"
    )
    itinerary_narrator_timeout_seconds: float = Field(
        default=20.0, alias="ITINERARY_NARRATOR_TIMEOUT_SECONDS", gt=0.0
    )
    # Prompt-size safety caps (never a factual limit -- a trip longer than
    # this still generates completely normally; only the narrator's own
    # input/output gets capped). The request builder truncates `days` to
    # this count and each day's `experiences`/`restaurant_names` to
    # `itinerary_narrator_max_items_per_day`, honestly marking
    # `ItineraryNarrativeRequest.truncated=True` when it does.
    itinerary_narrator_max_days: int = Field(
        default=10, alias="ITINERARY_NARRATOR_MAX_DAYS", ge=1
    )
    itinerary_narrator_max_items_per_day: int = Field(
        default=6, alias="ITINERARY_NARRATOR_MAX_ITEMS_PER_DAY", ge=1
    )

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

    @field_validator("accommodation_manual_html_source", mode="after")
    @classmethod
    def _normalize_accommodation_manual_html_source(cls, value: str) -> str:
        return value if value in _ALLOWED_ACCOMMODATION_MANUAL_HTML_SOURCES else "generic"

    @field_validator("flight_manual_html_source", mode="after")
    @classmethod
    def _normalize_flight_manual_html_source(cls, value: str) -> str:
        return value if value in _ALLOWED_FLIGHT_MANUAL_HTML_SOURCES else "generic"

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