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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def resolved_local_storage_path(self) -> Path:
        """Local development storage path, not a production database.

        Resolved against the backend project root (not the process's
        current working directory) so the default value works the same way
        regardless of where the app was started from.
        """
        path = Path(self.local_storage_path)
        return path if path.is_absolute() else _BACKEND_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()