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


@lru_cache
def get_settings() -> Settings:
    return Settings()