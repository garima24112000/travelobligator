import os
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
# Step 185B: mirrors the two constants above, for a future manual/local
# hotel-ratings ingestion adapter (Step 185E's job to actually build).
_ALLOWED_HOTEL_RATINGS_MANUAL_HTML_SOURCES = frozenset(
    {"generic", "tripadvisor", "google_places", "other"}
)

# Section 200A: allowed values for the ONE persistence selector,
# `PERSISTENCE_BACKEND`. "postgres" is the DEFAULT normal runtime backend;
# "local_json" is an explicit development/test fallback and is never chosen
# implicitly. Unlike the provider-selection fields in this file, an
# unrecognized value RAISES instead of clamping: silently mapping a typo to
# a different persistence backend is exactly the silent fallback this
# contract forbids. `DATABASE_URL` alone never selects anything.
_ALLOWED_PERSISTENCE_BACKENDS = frozenset({"local_json", "postgres"})

# Section 200B: the ONE provider-response-cache selector. "redis" (default) is
# the shared cache; "sqlite" is an EXPLICIT local-development alternative (the
# pre-200B file cache). Turning caching off entirely stays the existing
# PROVIDER_CACHE_ENABLED=false switch -- there is no third backend value, so
# the two settings never overlap. An unknown value raises (no silent switch).
_ALLOWED_PROVIDER_CACHE_BACKENDS = frozenset({"redis", "sqlite"})
_ALLOWED_GEOCODING_PROVIDERS = frozenset({"nominatim", "geoapify"})
_ALLOWED_PLACES_PROVIDERS = frozenset({"openstreetmap", "geoapify"})

# Step 187B: allowed values for the structured-logging foundation's log
# level. An unrecognized value normalizes to "INFO" rather than raising
# or crashing -- same convention as every other constrained-value field
# in this file.
_ALLOWED_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

# Step 184B: allowed values for the session cookie's SameSite attribute.
# An unrecognized value clamps to "lax" (a safe, standard default) rather
# than raising -- same convention as every other constrained-value field
# in this file. Comparison is case-insensitive since real cookie
# attribute values are conventionally capitalized ("Lax"/"Strict"/"None")
# but env vars are easiest to write in lowercase.
_ALLOWED_SESSION_COOKIE_SAMESITE_VALUES = frozenset({"lax", "strict", "none"})

# backend/app/core/config.py -> parents[2] is the backend/ project root, so
# a relative local_storage_path resolves the same way whether the app is
# started from backend/ (local dev, Docker WORKDIR) or from the repo root.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Step 190D.1: the repo-root parent of _BACKEND_ROOT, used only to anchor
# `.env` resolution (see `model_config` below) -- README's own documented
# setup (`cp .env.example .env` at the repo root, *then* `cd backend` before
# `uvicorn app.main:app --reload`) means the real `.env` a developer creates
# always lives one level above `_BACKEND_ROOT`, never inside it. Before this
# fix, `SettingsConfigDict(env_file=".env")` resolved that bare relative
# string against the process's current working directory at `Settings()`
# construction time -- so a backend launched exactly as documented (from
# `backend/`) silently found no dotenv file at all (pydantic-settings never
# raises for a missing one) and every field silently fell back to its
# class-level default, real local `.env` values like
# `AI_CANDIDATE_PROPOSAL_PROVIDER=groq`/`ITINERARY_NARRATOR_ENABLED=true`/
# `ROUTING_PROVIDER=osrm` included. Docker was never affected by this bug --
# `docker-compose.yml`'s own `env_file: - .env` directive already injects
# the repo-root `.env` as real process environment variables before
# `Settings()` ever runs, and real OS environment variables always take
# priority over anything a dotenv file would set regardless of this anchor.
_REPO_ROOT_ENV_FILE = _BACKEND_ROOT.parent / ".env"


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

    # Section 200A: NO built-in default. The old default embedded a
    # placeholder username/password pointing at the compose host `postgres`;
    # a real deployment must supply its own DATABASE_URL explicitly, and
    # `PERSISTENCE_BACKEND=postgres` (the default backend) refuses to start
    # without one (see `app/core/persistence.py`). Never logged or returned.
    database_url: str | None = Field(default=None, alias="DATABASE_URL", repr=False)

    # Section 200B: Redis backs ONLY the provider-response cache (never
    # persistence, sessions, jobs or AI output). The default targets the
    # docker-compose `redis` service and carries no credentials; a real
    # deployment supplies its own URL. Never logged, returned or put in keys.
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL", repr=False)
    # Short timeouts: the cache is an optimisation and must never stall a
    # provider call. After a connection failure the store bypasses Redis for a
    # short cooldown instead of paying the timeout on every call.
    redis_connect_timeout_seconds: float = Field(
        default=1.0, alias="REDIS_CONNECT_TIMEOUT_SECONDS", gt=0, le=30
    )
    redis_socket_timeout_seconds: float = Field(
        default=1.0, alias="REDIS_SOCKET_TIMEOUT_SECONDS", gt=0, le=30
    )
    # Key namespace prefix so several environments can share one Redis.
    redis_key_prefix: str = Field(default="travelobligator", alias="REDIS_KEY_PREFIX", min_length=1, max_length=64)

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
    # extended in Step 161A, corrected in Step 191A). "not_connected"
    # (default), "anthropic", and "groq" (Step 162A) are the only supported
    # values today. An unsupported/unrecognized value falls back to
    # "not_connected" rather than raising or fabricating output -- see
    # backend/app/providers/ai_candidate_proposal/factory.py.
    ai_candidate_proposal_provider: str = Field(
        default="not_connected", alias="AI_CANDIDATE_PROPOSAL_PROVIDER"
    )

    # Config gate for PlanningOrchestrator._run_ai_candidate_discovery_shadow_stage
    # (Step 161B, docs/13_llm_reasoning_pipeline.md section 40). Default is
    # False so normal generation is completely unaffected -- when disabled,
    # AICandidateDiscoveryService.dry_run is never called during generation
    # and ai_candidate_proposal_batch/candidate_grounding_batch stay None,
    # exactly as before this step.
    #
    # This is a LEGACY-ENGINE-ONLY, observation/testing path: it populates
    # ai_candidate_proposal_batch/candidate_grounding_batch for inspection,
    # but (per build_ai_candidate_node's own docstring) the LangGraph engine
    # never reads it and never runs this stage, regardless of its value.
    # Kept for backward compatibility -- not the production live-discovery
    # switch (see ai_candidate_discovery_enabled below).
    ai_candidate_discovery_shadow_mode_enabled: bool = Field(
        default=False,
        alias="AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED",
    )

    # Config gate for the Step 191A LIVE AI candidate discovery LangGraph
    # stage (docs/14_backend_architecture.md section 135) -- a real
    # planning dependency, deliberately a separate flag from
    # `ai_candidate_discovery_shadow_mode_enabled` above (which stays a
    # legacy-engine-only observation path; this flag never reinterprets or
    # is reinterpreted by it). Default is False so normal generation is
    # completely unaffected: when disabled, the LangGraph `ai_candidate`
    # node stays the exact pure no-op checkpoint it always was -- no
    # provider/LLM call, no `PlanningState` mutation. When enabled, the
    # node runs live proposal -> grounding -> promotion through the same
    # already-existing, already-deterministic-except-for-the-LLM-call
    # services the shadow stage above uses, gated by whichever
    # `ai_candidate_proposal_provider` is configured (still safely
    # `not_connected` with no provider/API key set). Grounding stays
    # mandatory either way: an AI proposal only reaches
    # `ai_candidate_promotion_report.promoted_candidates` (and, through the
    # existing Step 170D `ExperiencePlannerService` merge, the itinerary
    # itself) if `CandidateGroundingService` actually matched it against a
    # real `PlanningState.destination_context` provider candidate.
    ai_candidate_discovery_enabled: bool = Field(
        default=False,
        alias="AI_CANDIDATE_DISCOVERY_ENABLED",
    )

    # Section 192 (docs/14_backend_architecture.md section 138): bounds how
    # many AI candidate proposals `AIDirectedProviderDiscoveryService` will
    # run a real targeted provider lookup for in one `dry_run`/generation
    # call. Only proposals that actually need a lookup count against this
    # bound (a named_place proposal that already matched the broad
    # destination_context pool is never searched at all) -- this exists so
    # one LLM response (up to `AICandidateProposalRequest.max_candidates`,
    # itself capped at 25) can never trigger an unbounded number of real
    # Nominatim/Overpass calls. Each search still returns at most one
    # result (`PlacesProvider.search_must_visit_place`'s own existing
    # contract), so no separate "max results per proposal" setting is
    # needed. A proposal that exceeds this bound is recorded honestly as
    # `not_searched`, never silently dropped.
    ai_directed_provider_discovery_max_searches: int = Field(
        default=5,
        ge=0,
        alias="AI_DIRECTED_PROVIDER_DISCOVERY_MAX_SEARCHES",
    )
    # Section 202C.1C: a small RESERVE on top of the bound above, spent only
    # on high-confidence NAMED proposals and only while fewer than
    # `max_searches` proposals have actually been matched. 202C.1B saw a flat
    # cutoff of 5 leave a 0.78-confidence named landmark unsearched while an
    # earlier lookup had come back empty. Hard ceiling on real provider calls
    # per generation = max_searches + max_extra_searches (8 by default).
    ai_directed_provider_discovery_max_extra_searches: int = Field(
        default=3,
        ge=0,
        alias="AI_DIRECTED_PROVIDER_DISCOVERY_MAX_EXTRA_SEARCHES",
    )

    # Section 193A (docs/14_backend_architecture.md section 141): bounds
    # how many `ItineraryCandidateReference` entries
    # `AIItineraryReasoningRequestBuilder.build_request` includes in one
    # `AIItineraryReasoningRequest` -- selected deterministically by
    # quality tier then score (never by AI proposal confidence, never
    # arbitrary truncation). Contract-only in this step: nothing calls a
    # live LLM #2 provider yet, so this setting has no runtime effect
    # until Section 193B exists.
    ai_itinerary_reasoning_max_candidates: int = Field(
        default=40,
        ge=1,
        alias="AI_ITINERARY_REASONING_MAX_CANDIDATES",
    )

    # Section 193B (docs/14_backend_architecture.md section 142): config
    # gate for the real LLM #2 provider layer, mirroring
    # `ai_candidate_discovery_enabled`/`ai_candidate_proposal_provider`
    # (Step 191A/160E) and `itinerary_narrator_enabled`/
    # `itinerary_narrator_provider` (Step 182F) exactly. Default `False`/
    # `"not_connected"` so nothing calls a real LLM by default. Neither
    # flag is read by any runtime generation path yet -- Section 193C is
    # what would wire `AIItineraryReasoningService` into normal
    # `POST /trips/{id}/generate`; until then this only affects direct,
    # isolated calls to that service. `ai_itinerary_reasoning_model`
    # deliberately has no repo-wide default of its own: each adapter falls
    # back to the already-existing `groq_model`/`anthropic_model` setting
    # when unset, the same three-level fallback
    # `GroqItineraryNarratorProvider`/`AnthropicItineraryNarratorProvider`
    # already use -- this never introduces a second, divergent default
    # model per provider. No new API key setting is added: both adapters
    # reuse the existing `groq_api_key`/`anthropic_api_key`.
    ai_itinerary_reasoning_enabled: bool = Field(
        default=False,
        alias="AI_ITINERARY_REASONING_ENABLED",
    )
    ai_itinerary_reasoning_provider: str = Field(
        default="not_connected",
        alias="AI_ITINERARY_REASONING_PROVIDER",
    )
    ai_itinerary_reasoning_model: str | None = Field(
        default=None,
        alias="AI_ITINERARY_REASONING_MODEL",
    )

    # Section 194B (docs/14_backend_architecture.md, following section
    # 144): config gate for the bounded automatic repair loop built on
    # Section 194A's contract/service layer. Default `False` so a normal
    # `/generate` run stays exactly Section 193C's behavior (a completed
    # LLM #2 reasoning result is used as-is, however deterministic
    # routing/validation judge it) until explicitly opted in. Deliberately
    # reuses `ai_itinerary_reasoning_provider`/`ai_itinerary_reasoning_model`
    # (and their existing `groq_api_key`/`anthropic_api_key`) rather than
    # a second provider/model setting -- repair is the same LLM #2 job as
    # reasoning, just scoped to a subset of days. `ai_itinerary_repair_max_attempts`
    # is deliberately capped at `2` (`ge=1, le=2`): a "bounded loop" that
    # allowed an arbitrarily large value would not actually bound anything
    # in practice, and every real repair call costs a live LLM request.
    ai_itinerary_repair_enabled: bool = Field(
        default=False,
        alias="AI_ITINERARY_REPAIR_ENABLED",
    )
    ai_itinerary_repair_max_attempts: int = Field(
        default=1,
        ge=1,
        le=2,
        alias="AI_ITINERARY_REPAIR_MAX_ATTEMPTS",
    )

    # Section 196 (docs/14_backend_architecture.md, following section
    # 146): config gate for the AI feedback interpreter -- a genuinely
    # separate job from itinerary reasoning/repair (translating a user's
    # natural-language feedback into a structured change request, never
    # selecting/repairing itinerary candidates itself), so it gets its
    # own explicit provider/model settings rather than reusing
    # `ai_itinerary_reasoning_provider`/`ai_itinerary_reasoning_model`,
    # mirroring `itinerary_narrator_enabled`/`itinerary_narrator_provider`/
    # `itinerary_narrator_model`'s own precedent for "a distinct AI
    # feature gets its own config surface." Default `False`/
    # `"not_connected"` so nothing calls a real LLM by default. No new
    # API key setting: both adapters reuse the existing `groq_api_key`/
    # `anthropic_api_key`. `ai_feedback_interpreter_model` has no
    # repo-wide default of its own -- each adapter falls back to
    # `groq_model`/`anthropic_model` when unset, the same three-level
    # fallback every other AI feature in this repo already uses. Not
    # called by any runtime generation/feedback/regeneration path yet
    # (Section 197 is what would wire it in) -- these settings currently
    # only affect a direct, isolated call to
    # `AIFeedbackInterpreterService`.
    ai_feedback_interpreter_enabled: bool = Field(
        default=False,
        alias="AI_FEEDBACK_INTERPRETER_ENABLED",
    )
    ai_feedback_interpreter_provider: str = Field(
        default="not_connected",
        alias="AI_FEEDBACK_INTERPRETER_PROVIDER",
    )
    ai_feedback_interpreter_model: str | None = Field(
        default=None,
        alias="AI_FEEDBACK_INTERPRETER_MODEL",
    )

    # Section 197C (docs/14_backend_architecture.md, following section
    # 149.1): the runtime gate for the whole Sections 196/197A/197B
    # pipeline inside `POST /trips/{trip_id}/regenerate`. Deliberately a
    # SEPARATE flag from `ai_feedback_interpreter_enabled` -- they answer
    # different questions: the latter is "may the interpreter ever call a
    # provider at all" (also usable in isolation, per Section 196's own
    # scope), while this one is "should `/regenerate` route through the
    # targeted pipeline instead of the legacy coarse stage-rerun path."
    # Both default `False`, so a fresh deployment's `/regenerate`
    # behavior is completely unchanged until an operator opts in
    # explicitly. When this is `True` but `ai_feedback_interpreter_enabled`
    # is `False` (or the configured provider is unreachable), the targeted
    # path still never falls back to legacy stage-rerun -- it returns an
    # honest `not_connected`-derived failure instead (Task 6: silently
    # running broad regeneration when the user asked for a scoped change
    # would violate their intent).
    targeted_regeneration_enabled: bool = Field(
        default=False,
        alias="TARGETED_REGENERATION_ENABLED",
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
    # Geocoder selection (Section 203C.1, docs/22_free_tier_deployment.md).
    # Geocoding/named-place search is separate from Overpass POI discovery:
    # "nominatim" (default; development/tests, paced to the public usage
    # policy) or "geoapify" (the production geocoder; needs
    # GEOAPIFY_API_KEY). There is no automatic fallback between them.
    # APP_ENV=production rejects the public Nominatim endpoint unless
    # ALLOW_PUBLIC_NOMINATIM_IN_PRODUCTION=true is set explicitly.
    geocoding_provider: str = Field(default="nominatim", alias="GEOCODING_PROVIDER")
    geoapify_api_key: str | None = Field(default=None, alias="GEOAPIFY_API_KEY", repr=False)
    geoapify_api_url: str = Field(default="https://api.geoapify.com", alias="GEOAPIFY_API_URL")
    geoapify_timeout_seconds: float = Field(default=10.0, alias="GEOAPIFY_TIMEOUT_SECONDS", gt=0.0)
    # Section 203C.2B: places (POI discovery) provider. "openstreetmap"
    # (default; Overpass, development/experiments) or "geoapify" (the
    # production provider: Geoapify Places inside the destination boundary).
    # There is no automatic fallback between them.
    places_provider: str = Field(default="openstreetmap", alias="PLACES_PROVIDER")
    # Hard cap on the Geoapify credits ONE generation may spend (geocoding +
    # places + place details + routing). A call beyond it is refused locally
    # and reported honestly; nothing falls through to another provider.
    # Worst-case math per trip shape: docs/22_free_tier_deployment.md.
    geoapify_max_credits_per_generation: int = Field(
        default=100, alias="GEOAPIFY_MAX_CREDITS_PER_GENERATION", ge=1
    )
    # A successful Places response with ZERO results is cached for this
    # short time, so repeated generations do not keep paying for the same
    # empty category. Failures (auth, 429, 5xx, timeout, malformed) are
    # never cached.
    geoapify_empty_result_cache_ttl_seconds: int = Field(
        default=21600, alias="GEOAPIFY_EMPTY_RESULT_CACHE_TTL_SECONDS", ge=0
    )
    # Place Details lookups (category/evidence for a grounded anchor that is
    # not already in the broad pool) allowed per generation.
    geoapify_max_place_details_per_generation: int = Field(
        default=12, alias="GEOAPIFY_MAX_PLACE_DETAILS_PER_GENERATION", ge=0
    )
    # Of those, how many may be spent corroborating the identity of
    # suspected duplicate candidates (two co-located records of a compatible
    # class). Each is one Place Details lookup, inside the cap above.
    geoapify_max_identity_lookups_per_generation: int = Field(
        default=4, alias="GEOAPIFY_MAX_IDENTITY_LOOKUPS_PER_GENERATION", ge=0
    )
    # Section 203C.2B: the inventory sufficiency gate + usefulness contract
    # (T / R / H; `app.services.usefulness_contract`). On by default and
    # required in production. The hermetic test suite turns it off for its
    # small legacy fixtures and enables it explicitly where it is tested.
    inventory_sufficiency_gate_enabled: bool = Field(
        default=True, alias="INVENTORY_SUFFICIENCY_GATE_ENABLED"
    )
    # Section 203C.2B (canary correction): when the reasoning model's own day
    # grouping is geographically poor, the SAME places are regrouped into
    # geographic day clusters (deterministic; see
    # `services/day_order_heuristics.balanced_spatial_clusters`). The model
    # still chooses which places; geography decides which share a day.
    ai_day_spatial_regrouping_enabled: bool = Field(
        default=True, alias="AI_DAY_SPATIAL_REGROUPING_ENABLED"
    )
    # Route-burden quality thresholds (walking, from the routing provider's
    # own leg data; never an estimate). A day beyond its pace's total, or a
    # single leg beyond the leg limit, gets a long-travel warning and the
    # `LONG_TRAVEL_DAY` review code.
    route_burden_max_leg_seconds: int = Field(default=2400, alias="ROUTE_BURDEN_MAX_LEG_SECONDS", ge=60)
    route_burden_max_day_seconds_relaxed: int = Field(
        default=3600, alias="ROUTE_BURDEN_MAX_DAY_SECONDS_RELAXED", ge=60
    )
    route_burden_max_day_seconds_balanced: int = Field(
        default=5400, alias="ROUTE_BURDEN_MAX_DAY_SECONDS_BALANCED", ge=60
    )
    route_burden_max_day_seconds_packed: int = Field(
        default=7200, alias="ROUTE_BURDEN_MAX_DAY_SECONDS_PACKED", ge=60
    )
    # One bounded, deterministic replacement of a geographically isolated
    # stop per long-route day (`services/route_burden_repair_service.py`).
    # Kept only when the provider-measured walking total falls by at least
    # this share and no leg gets longer.
    route_burden_repair_enabled: bool = Field(default=True, alias="ROUTE_BURDEN_REPAIR_ENABLED")
    route_burden_repair_min_improvement_ratio: float = Field(
        default=0.15, alias="ROUTE_BURDEN_REPAIR_MIN_IMPROVEMENT_RATIO", gt=0.0, lt=1.0
    )
    # Mixed-mode transfers (Section 203C.2B). A walking leg longer than
    # `ROUTE_BURDEN_MAX_LEG_SECONDS` gets ONE driving-route request for that
    # same leg; this caps how many such requests one generation may make.
    # With the cap used up, the leg stays a walking leg and keeps its warning.
    route_alternate_mode_max_requests_per_generation: int = Field(
        default=6, alias="ROUTE_ALTERNATE_MODE_MAX_REQUESTS_PER_GENERATION", ge=0
    )
    # A vehicle transfer is "reasonable" up to this long; a day's transfers
    # of every mode together up to the day total. Beyond either the day is
    # still a long-travel day.
    route_burden_max_drive_leg_seconds: int = Field(
        default=2700, alias="ROUTE_BURDEN_MAX_DRIVE_LEG_SECONDS", ge=60
    )
    route_burden_max_day_transfer_seconds: int = Field(
        default=9000, alias="ROUTE_BURDEN_MAX_DAY_TRANSFER_SECONDS", ge=60
    )
    # Schedule diversity (`services/schedule_diversity.py`): stops of one
    # coarse attraction class per day, and marketplace stops per day unless
    # the traveller explicitly asked for markets/shopping.
    schedule_diversity_enabled: bool = Field(default=True, alias="SCHEDULE_DIVERSITY_ENABLED")
    schedule_diversity_max_per_class_per_day: int = Field(
        default=2, alias="SCHEDULE_DIVERSITY_MAX_PER_CLASS_PER_DAY", ge=1
    )
    schedule_diversity_max_markets_per_day: int = Field(
        default=1, alias="SCHEDULE_DIVERSITY_MAX_MARKETS_PER_DAY", ge=1
    )
    # Open-Meteo's forecast endpoint only covers the next ~16 days. A trip
    # that starts beyond it is not requested at all (reported as
    # `forecast_not_yet_available`); future weather is never invented.
    open_meteo_forecast_horizon_days: int = Field(
        default=16, alias="OPEN_METEO_FORECAST_HORIZON_DAYS", ge=1
    )
    # Travel mode for Geoapify Routing legs (ROUTING_PROVIDER=geoapify).
    # In-city itinerary legs are walking routes; no transit schedule is
    # ever requested or invented.
    geoapify_routing_mode: str = Field(default="walk", alias="GEOAPIFY_ROUTING_MODE")
    allow_public_nominatim_in_production: bool = Field(
        default=False, alias="ALLOW_PUBLIC_NOMINATIM_IN_PRODUCTION"
    )
    # Optional contact (URL or email) added to the User-Agent sent to
    # Overpass/Nominatim, as their usage policies ask for.
    osm_user_agent_contact: str | None = Field(default=None, alias="OSM_USER_AGENT_CONTACT")
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
    # Section 200B: `redis` (default, shared) or `sqlite` (explicit local
    # development file cache at PROVIDER_CACHE_PATH). See
    # `_ALLOWED_PROVIDER_CACHE_BACKENDS`. PROVIDER_CACHE_PATH is only used by
    # the sqlite backend.
    provider_cache_backend: str = Field(default="redis", alias="PROVIDER_CACHE_BACKEND")
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

    # Geocode cache TTL (Step 164E, docs/12_provider_architecture.md
    # "Provider Cache Foundation" section). Applies to every geocoder
    # (Nominatim and Geoapify, Section 203C.1): destination geocodes and
    # named-place lookups made by `OpenStreetMapPlacesAdapter`. 30 days is acceptable here because geocoding a given
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

    # Config gate for get_hotel_ratings_provider (Step 177B, wired to a
    # real local/manual adapter in Step 185E,
    # docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
    # "not_connected" (default), "scraped_local", and "manual_html" (an
    # alias for "scraped_local", mirroring `accommodation_provider`/
    # `flight_provider`'s identical alias convention) are the only
    # supported values -- see backend/app/providers/hotel_ratings/factory.py.
    # An unsupported/unrecognized value falls back to "not_connected"
    # rather than raising or fabricating rating data. No real Google
    # Places/Tripadvisor/Amadeus/Yelp *live* rating provider is wired in --
    # "scraped_local"/"manual_html" only ever read an already-supplied
    # local HTML file (see `ScrapedLocalHotelRatingsProvider`), never a
    # live network call; every such adapter's identity matching is
    # conservative (no fuzzy matching). Wired into
    # `AccommodationInventoryService` via `HotelRatingEnrichmentService`
    # (Step 177C) -- still not read by `ProviderGateway`/
    # `PlanningOrchestrator` directly.
    hotel_ratings_provider: str = Field(
        default="not_connected", alias="HOTEL_RATINGS_PROVIDER"
    )

    # Step 185B, wired to a real adapter in Step 185E. Mirrors
    # `scraped_accommodation_provider_enabled`/
    # `scraped_flight_provider_enabled` exactly: gates
    # `ScrapedLocalHotelRatingsProvider` independently of
    # `hotel_ratings_provider` selecting it -- both must be satisfied
    # (`scraping_enabled` too) for a real local-file read to happen.
    # Enabled by default for MVP local/manual testing; still never
    # fabricates a rating with no file present -- see that adapter's own
    # docstring.
    scraped_hotel_ratings_provider_enabled: bool = Field(
        default=True, alias="SCRAPED_HOTEL_RATINGS_PROVIDER_ENABLED"
    )

    # Step 185B, read by a real adapter as of Step 185E. Mirrors
    # `accommodation_manual_html_source`/`flight_manual_html_source`.
    # "generic" (default), "tripadvisor", "google_places", or "other" --
    # an unrecognized value falls back to "generic".
    # `resolve_manual_source_policy` (backend/app/providers/
    # scraping_source_registry_defaults.py) resolves this label against
    # the populated registry for display naming only -- it never selects
    # a different provider, never enables a live API call, and never
    # implies the labeled site's official API/site was actually used.
    hotel_ratings_manual_html_source: str = Field(
        default="generic", alias="HOTEL_RATINGS_MANUAL_HTML_SOURCE"
    )

    # Step 185B, read by a real adapter as of Step 185E. Mirrors
    # `scraped_accommodation_html_path`/`scraped_flight_html_path`.
    scraped_hotel_ratings_html_path: str | None = Field(
        default=".data/manual_scrapes/hotel_ratings.html",
        alias="SCRAPED_HOTEL_RATINGS_HTML_PATH",
    )
    scraped_hotel_ratings_source_id: str = Field(
        default="manual_local_scraped_hotel_ratings",
        alias="SCRAPED_HOTEL_RATINGS_SOURCE_ID",
    )
    scraped_hotel_ratings_source_name: str = Field(
        default="Manual local scraped hotel ratings source",
        alias="SCRAPED_HOTEL_RATINGS_SOURCE_NAME",
    )
    scraped_hotel_ratings_base_url: str | None = Field(
        default=None, alias="SCRAPED_HOTEL_RATINGS_BASE_URL"
    )

    # Cache for the local/manual scraped hotel ratings provider (Step
    # 185E), mirroring `scraped_accommodation_cache_enabled`/
    # `scraped_flight_cache_enabled` exactly. Caches only the normalized
    # `HotelRatingsResult` payload -- never the raw HTML file content.
    # Enabled by default because caching itself never causes a network
    # call or changes what data is returned, only how often the local
    # file is re-read/re-parsed; the cache key includes every configured
    # source file's mtime/size plus the incoming requests' own identity
    # fields, so an edited local file (or a different set of accommodation
    # offers to look ratings up for) is never served a stale cached
    # result.
    scraped_hotel_ratings_cache_enabled: bool = Field(
        default=True, alias="SCRAPED_HOTEL_RATINGS_CACHE_ENABLED"
    )
    scraped_hotel_ratings_cache_ttl_seconds: int = Field(
        default=3600, alias="SCRAPED_HOTEL_RATINGS_CACHE_TTL_SECONDS", ge=0
    )

    # Step 185E: optional, independent per-source local file paths --
    # multi-source hotel-ratings ingestion, mirroring Step 185C/185D's
    # accommodation/flight equivalents. Each defaults to its own real path
    # under `.data/manual_scrapes/`, never created automatically, so
    # "every source is eligible by default" without fabricating anything:
    # with no file actually present at either path (the out-of-the-box
    # state), `ScrapedLocalHotelRatingsProvider` honestly reports that
    # source as missing, exactly like the original single-file path.
    # These are independent of, and additive to, the pre-existing
    # single-file `scraped_hotel_ratings_html_path`/
    # `hotel_ratings_manual_html_source` pair (Step 185B) -- supplying
    # only the original single file still works exactly as before.
    # `google_places_ratings` here is a manual/local file label only --
    # Google Places is never called live anywhere in this codebase; see
    # `app.providers.scraping_source_registry_defaults`'s
    # `google_places_ratings` entry for why it is tracked as a future
    # *official-API* candidate rather than a scraping target.
    scraped_hotel_ratings_html_path_tripadvisor: str | None = Field(
        default=".data/manual_scrapes/hotel_ratings_tripadvisor.html",
        alias="SCRAPED_HOTEL_RATINGS_HTML_PATH_TRIPADVISOR",
    )
    scraped_hotel_ratings_html_path_google_places_ratings: str | None = Field(
        default=".data/manual_scrapes/hotel_ratings_google_places_ratings.html",
        alias="SCRAPED_HOTEL_RATINGS_HTML_PATH_GOOGLE_PLACES_RATINGS",
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

    # Step 185C: optional, independent per-source local file paths --
    # multi-source accommodation ingestion. Each defaults to its own real
    # path under `.data/manual_scrapes/` (mirroring
    # `scraped_accommodation_html_path` above), never created
    # automatically, so "every source is eligible by default" without
    # fabricating anything: with no file actually present at any of these
    # paths (the out-of-the-box state), `ScrapedAccommodationProvider`
    # honestly reports that source as missing, exactly like the original
    # single-file path. Setting one of these to `None` explicitly disables
    # that source's slot entirely (mirrors `scraped_accommodation_html_path
    # =None`'s existing "explicitly cleared" behavior). These are
    # independent of, and additive to, the pre-existing single-file
    # `scraped_accommodation_html_path`/`accommodation_manual_html_source`
    # pair -- supplying only the original single file (the pre-185C
    # default) still works exactly as before; see
    # `ScrapedAccommodationProvider`'s own docstring for the deterministic
    # precedence used when both a per-source path and the legacy
    # single-file path happen to resolve to the same real file.
    scraped_accommodation_html_path_booking: str | None = Field(
        default=".data/manual_scrapes/accommodations_booking.html",
        alias="SCRAPED_ACCOMMODATION_HTML_PATH_BOOKING",
    )
    scraped_accommodation_html_path_expedia: str | None = Field(
        default=".data/manual_scrapes/accommodations_expedia.html",
        alias="SCRAPED_ACCOMMODATION_HTML_PATH_EXPEDIA",
    )
    scraped_accommodation_html_path_hotelbeds: str | None = Field(
        default=".data/manual_scrapes/accommodations_hotelbeds.html",
        alias="SCRAPED_ACCOMMODATION_HTML_PATH_HOTELBEDS",
    )
    scraped_accommodation_html_path_hostelworld: str | None = Field(
        default=".data/manual_scrapes/accommodations_hostelworld.html",
        alias="SCRAPED_ACCOMMODATION_HTML_PATH_HOSTELWORLD",
    )
    scraped_accommodation_html_path_vrbo: str | None = Field(
        default=".data/manual_scrapes/accommodations_vrbo.html",
        alias="SCRAPED_ACCOMMODATION_HTML_PATH_VRBO",
    )
    scraped_accommodation_html_path_airbnb: str | None = Field(
        default=".data/manual_scrapes/accommodations_airbnb.html",
        alias="SCRAPED_ACCOMMODATION_HTML_PATH_AIRBNB",
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

    # Step 185D: optional, independent per-source local file paths --
    # multi-source flight ingestion, mirroring Step 185C's accommodation
    # equivalent (`scraped_accommodation_html_path_booking` and friends).
    # Each defaults to its own real path under `.data/manual_scrapes/`,
    # never created automatically, so "every source is eligible by
    # default" without fabricating anything: with no file actually
    # present, that source is honestly recorded as missing (see
    # `FlightSearchResult.warnings`) rather than blocking any other
    # source that *does* have a file. These are independent of, and
    # additive to, `scraped_flight_html_path`/`flight_manual_html_source`
    # above -- supplying only the original single file (the pre-185D
    # default) still works exactly as before. `kiwi_manual` here is a
    # manual/local file label only, completely distinct from
    # `flight_provider="kiwi_mcp"`/`kiwi_mcp_enabled` below.
    scraped_flight_html_path_skyscanner: str | None = Field(
        default=".data/manual_scrapes/flights_skyscanner.html",
        alias="SCRAPED_FLIGHT_HTML_PATH_SKYSCANNER",
    )
    scraped_flight_html_path_google_flights: str | None = Field(
        default=".data/manual_scrapes/flights_google_flights.html",
        alias="SCRAPED_FLIGHT_HTML_PATH_GOOGLE_FLIGHTS",
    )
    scraped_flight_html_path_kiwi_manual: str | None = Field(
        default=".data/manual_scrapes/flights_kiwi_manual.html",
        alias="SCRAPED_FLIGHT_HTML_PATH_KIWI_MANUAL",
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

    # Persistence backend selector (Step 183B; default flipped in Section
    # 200A, docs/14_backend_architecture.md). "postgres" (DEFAULT) is the
    # normal runtime backend for every repository (users, trips, planning
    # state, jobs, branches/revisions) and requires DATABASE_URL.
    # "local_json" is an EXPLICIT development/test fallback
    # (`PERSISTENCE_BACKEND=local_json`), never used implicitly and never a
    # fallback for a failed Postgres connection. Setting `DATABASE_URL`
    # alone selects nothing; an unknown value is a startup error.
    persistence_backend: str = Field(default="postgres", alias="PERSISTENCE_BACKEND")

    # Async job foundation (Step 186B, docs/14_backend_architecture.md
    # section 116). Declared now so a later step (186C) has a real, typed
    # config surface to gate on -- nothing reads `async_generation_enabled`
    # yet: `POST /trips/{trip_id}/generate`/`.../regenerate` remain fully
    # synchronous regardless of this value, exactly as before this step.
    # Default `False` so current behavior is completely unchanged; only an
    # explicit `ASYNC_GENERATION_ENABLED=true` (once 186C wires it up) will
    # ever change that. This never implies Redis/Celery/RQ/a worker process
    # are in use -- see `redis_url` above, still never read by any code
    # path in `backend/app/` today.
    async_generation_enabled: bool = Field(
        default=False, alias="ASYNC_GENERATION_ENABLED"
    )

    # How long a terminal (succeeded/failed/cancelled) `GenerationJob`
    # record is considered fresh enough to still be worth reading/showing,
    # for a future cleanup/expiry step to reference (Step 186B declares
    # this; no code reads or enforces it yet -- `JobRepository` never
    # deletes or hides an expired job on its own). Must be positive: zero
    # or negative has no sane meaning for a TTL. Default of 86400s (24
    # hours) is a conservative, human-scale window for local/manual
    # testing, not a production retention policy.
    generation_job_ttl_seconds: int = Field(
        default=86400, alias="GENERATION_JOB_TTL_SECONDS", gt=0
    )

    # Maximum number of `queued`/`running` jobs allowed to exist at once
    # for a single trip, for a future step (186C) to enforce as a
    # duplicate-job guard before starting a new background generation/
    # regeneration. Declared now so that guard has a real config surface;
    # `JobRepository`/`get_job_repository()` themselves never enforce this
    # limit -- Step 186B adds no route wiring or background execution.
    # Must be at least 1 -- a limit of 0 would make generation/regeneration
    # permanently unavailable, which is never the intent of this setting.
    generation_job_max_running_per_trip: int = Field(
        default=1, alias="GENERATION_JOB_MAX_RUNNING_PER_TRIP", ge=1
    )

    # Duplicate-job/restart hardening (Step 186E,
    # docs/14_backend_architecture.md section 118). Distinct from
    # `generation_job_ttl_seconds` above on purpose -- that setting is
    # about how long a *finished* job record stays worth displaying;
    # this one is about how long a job is allowed to sit `queued`/
    # `running` before `generation_job_service.check_no_duplicate_
    # running_job` treats it as abandoned (a background task that died
    # without the process crashing outright, so app startup's own
    # recovery pass never saw it) rather than genuinely still in
    # progress. Reusing one field for both concepts would make either
    # value's "safe default" wrong for the other purpose. A stale job is
    # never deleted or silently ignored -- it is marked `failed` with a
    # safe, honest `JOB_INTERRUPTED` error (see `mark_job_interrupted`)
    # so Developer Mode can explain what happened and a fresh attempt is
    # never blocked forever. Must be positive, matching every other
    # duration setting in this file. Default of 3600s (1 hour) is a
    # conservative upper bound for this app's real, typically-fast
    # pipeline -- generous enough to never fire during a normal run.
    generation_job_stale_after_seconds: int = Field(
        default=3600, alias="GENERATION_JOB_STALE_AFTER_SECONDS", gt=0
    )

    # Section 200C: multi-instance job ownership (PostgreSQL only). A running job is
    # owned by one backend process through a DB lease; the owner heartbeats every
    # `lease/3` seconds (min 1) to extend it, and another instance may reclaim/interrupt
    # the job only after the lease has expired without a heartbeat -- so a crashed
    # process's work is released after roughly this many seconds, and a healthy job
    # owned by a live instance is never touched. Independent of
    # `generation_job_stale_after_seconds`, which now only bounds jobs that were queued
    # but never claimed (and the local_json single-process age check).
    # ge=3: the heartbeat runs every lease/3 seconds (minimum 1 s), so a lease shorter than 3 s
    # could not be renewed before it expires (Section 200D validation).
    generation_job_lease_seconds: int = Field(
        default=120, alias="GENERATION_JOB_LEASE_SECONDS", ge=3
    )

    # Section 200D: operational observability / readiness.
    # `GET /metrics` (Prometheus text, low-cardinality, no user content). No authentication exists in
    # this app, so deployments should keep /metrics on a private network (see docs section 163).
    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")
    # Section 203B: exposure policy for /ready and /metrics on a PUBLIC backend (see `api/routes/ops.py`).
    # When set, both endpoints require `Authorization: Bearer <OPS_TOKEN>`. When unset they stay open
    # outside production (local Compose/CI probes) and answer 404 with APP_ENV=production. /health is
    # always public. A secret: never logged or returned.
    ops_token: str | None = Field(default=None, alias="OPS_TOKEN", repr=False)
    # Overall wall-clock bound for one `GET /ready` (PostgreSQL + Redis probes together).
    readiness_timeout_seconds: float = Field(
        default=2.0, alias="READINESS_TIMEOUT_SECONDS", gt=0, le=30
    )
    # libpq connect timeout for EVERY PostgreSQL connection the app opens (without it an
    # unreachable/blackholed host can hang a connection attempt for minutes). Pool size, statement
    # timeout and lock timeout are NOT configured here (200E).
    db_connect_timeout_seconds: int = Field(
        default=5, alias="DB_CONNECT_TIMEOUT_SECONDS", ge=1, le=60
    )

    # Section 200E: PostgreSQL connection pool + request-transaction bounds (one web process per
    # container). Maximum connections per backend container = DB_POOL_SIZE + DB_MAX_OVERFLOW
    # (default 5 + 5 = 10), plus the readiness probe's single dedicated connection. Total database
    # connections = connections_per_container x number_of_containers -- sized in Section 203.
    db_pool_size: int = Field(default=5, alias="DB_POOL_SIZE", ge=1, le=100)
    db_max_overflow: int = Field(default=5, alias="DB_MAX_OVERFLOW", ge=0, le=100)
    # How long a request waits for a free pooled connection before failing safely (HTTP 503).
    db_pool_timeout_seconds: float = Field(default=10.0, alias="DB_POOL_TIMEOUT_SECONDS", gt=0, le=120)
    # Recycle pooled connections older than this (0 disables recycling).
    db_pool_recycle_seconds: int = Field(default=1800, alias="DB_POOL_RECYCLE_SECONDS", ge=0, le=86400)
    # Server-side bounds applied to every APPLICATION connection (not to migrations, which use their
    # own engine, and not to the readiness probe, which sets its own). 0 disables. Conservative
    # non-sub-second defaults: a runaway statement is cancelled after 30 s, a blocked lock wait
    # after 10 s; both roll the transaction back and surface as a sanitized 503.
    db_statement_timeout_ms: int = Field(default=30000, alias="DB_STATEMENT_TIMEOUT_MS", ge=0, le=600000)
    db_lock_timeout_ms: int = Field(default=10000, alias="DB_LOCK_TIMEOUT_MS", ge=0, le=600000)

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

    # Auth/session foundation (Step 184B, docs/14_backend_architecture.md).
    # Nothing reads these yet outside app/auth/ and this step's own tests --
    # no route is auth-gated, no owner check exists, `/trips/*` behavior is
    # completely unchanged. `session_secret_key` deliberately defaults to
    # `None` ("auth not configured") rather than any built-in fallback
    # value: `app/auth/sessions.py` refuses to sign or verify a session
    # token at all while this is unset, rather than silently using a
    # weak/predictable/shared default key. A real deployment sets a real
    # secret only in its own `.env` -- `.env.example` ships a blank
    # placeholder, never a real value, matching every other secret in this
    # file. Because this defaults to `None`, importing this module, running
    # the app, or running the test suite never requires it to be set.
    session_secret_key: str | None = Field(default=None, alias="SESSION_SECRET_KEY")
    session_cookie_name: str = Field(
        default="travelobligator_session", alias="SESSION_COOKIE_NAME"
    )
    # One week, matching this being a simple, no-server-side-store session
    # (the signed cookie's own embedded timestamp is the only expiry
    # state) rather than a short-lived access token -- there is no refresh
    # flow in this MVP, so re-login after expiry is the expected UX.
    session_ttl_seconds: int = Field(
        default=604800, alias="SESSION_TTL_SECONDS", gt=0
    )
    # `False` by default so local HTTP development (http://localhost:3000)
    # keeps working without HTTPS -- a real deployment over HTTPS must set
    # this to `true`, matching the standard "Secure cookies require HTTPS"
    # rule; this app never sets it `true` on its own behalf.
    session_cookie_secure: bool = Field(default=False, alias="SESSION_COOKIE_SECURE")
    session_cookie_samesite: str = Field(default="lax", alias="SESSION_COOKIE_SAMESITE")
    session_cookie_httponly: bool = Field(default=True, alias="SESSION_COOKIE_HTTPONLY")

    # Structured logging foundation (Step 187B, docs/14_backend_architecture.md
    # section 121, following Step 187A's read-only audit). Stdlib-only --
    # no OpenTelemetry/Elastic APM/Sentry/Datadog/structlog dependency
    # exists in this codebase, and neither of these two fields adds one.
    # `log_level` controls the dedicated `"app"` logger's effective level
    # (see `app/core/logging_config.py`) -- an unrecognized value
    # normalizes to `"INFO"` rather than raising, matching every other
    # constrained-value field's fallback convention in this file (e.g.
    # `persistence_backend` above).
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # `True` (default) renders every existing `logger.*` call as one JSON
    # object per stdout line via `JsonFormatter`; `False` falls back to a
    # plain, single-line, human-readable formatter instead. Neither value
    # changes what is logged (no new logging call site exists yet), adds
    # a request-scoped correlation id (Step 187C's job), or ships a log
    # line anywhere outside this process's own stdout -- no log
    # aggregator/APM/network destination is configured by this field.
    structured_logging_enabled: bool = Field(
        default=True, alias="STRUCTURED_LOGGING_ENABLED"
    )

    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV_FILE,
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

    @field_validator("hotel_ratings_manual_html_source", mode="after")
    @classmethod
    def _normalize_hotel_ratings_manual_html_source(cls, value: str) -> str:
        return value if value in _ALLOWED_HOTEL_RATINGS_MANUAL_HTML_SOURCES else "generic"

    @field_validator("persistence_backend", mode="after")
    @classmethod
    def _normalize_persistence_backend(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in _ALLOWED_PERSISTENCE_BACKENDS:
            raise ValueError(
                "PERSISTENCE_BACKEND must be 'postgres' (default) or 'local_json' (explicit "
                "development/test fallback)."
            )
        return normalized

    @field_validator("places_provider", mode="after")
    @classmethod
    def _normalize_places_provider(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in _ALLOWED_PLACES_PROVIDERS:
            raise ValueError("PLACES_PROVIDER must be 'openstreetmap' (default) or 'geoapify'.")
        return normalized

    @field_validator("geocoding_provider", mode="after")
    @classmethod
    def _normalize_geocoding_provider(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in _ALLOWED_GEOCODING_PROVIDERS:
            raise ValueError("GEOCODING_PROVIDER must be 'nominatim' (default) or 'geoapify'.")
        return normalized

    @field_validator("provider_cache_backend", mode="after")
    @classmethod
    def _normalize_provider_cache_backend(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in _ALLOWED_PROVIDER_CACHE_BACKENDS:
            raise ValueError(
                "PROVIDER_CACHE_BACKEND must be 'redis' (default) or 'sqlite' (explicit local "
                "development cache); use PROVIDER_CACHE_ENABLED=false to disable caching."
            )
        return normalized

    @field_validator("db_statement_timeout_ms", "db_lock_timeout_ms", mode="after")
    @classmethod
    def _timeouts_are_disabled_or_at_least_one_second(cls, value: int) -> int:
        if value != 0 and value < 1000:
            raise ValueError(
                "DB_STATEMENT_TIMEOUT_MS / DB_LOCK_TIMEOUT_MS must be 0 (disabled) or at least 1000 ms "
                "(sub-second timeouts would cancel healthy work)."
            )
        return value

    @field_validator("session_cookie_samesite", mode="after")
    @classmethod
    def _normalize_session_cookie_samesite(cls, value: str) -> str:
        lowered = value.strip().lower()
        return lowered if lowered in _ALLOWED_SESSION_COOKIE_SAMESITE_VALUES else "lax"

    @field_validator("log_level", mode="after")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        normalized = value.strip().upper() if value else "INFO"
        return normalized if normalized in _ALLOWED_LOG_LEVELS else "INFO"

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

    def resolved_scraped_accommodation_html_paths_by_source(self) -> dict[str, Path | None]:
        """Step 185C: multi-source accommodation ingestion -- resolves
        every independent per-source local file path
        (`scraped_accommodation_html_path_booking`/`_expedia`/
        `_hotelbeds`/`_hostelworld`/`_vrbo`/`_airbnb`) the same way
        `resolved_scraped_accommodation_html_path` resolves the original
        single-file setting: a relative value resolves against the
        backend project root, and `None` (explicitly cleared) stays
        `None`. Returns one entry per source regardless of whether a real
        file exists there -- callers must not assume any of these paths
        actually exist; `ScrapedAccommodationProvider` is the one place
        that checks.
        """

        def _resolve(value: str | None) -> Path | None:
            if value is None:
                return None
            path = Path(value)
            return path if path.is_absolute() else _BACKEND_ROOT / path

        return {
            "booking": _resolve(self.scraped_accommodation_html_path_booking),
            "expedia": _resolve(self.scraped_accommodation_html_path_expedia),
            "hotelbeds": _resolve(self.scraped_accommodation_html_path_hotelbeds),
            "hostelworld": _resolve(self.scraped_accommodation_html_path_hostelworld),
            "vrbo": _resolve(self.scraped_accommodation_html_path_vrbo),
            "airbnb": _resolve(self.scraped_accommodation_html_path_airbnb),
        }

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

    def resolved_scraped_flight_html_paths_by_source(self) -> dict[str, Path | None]:
        """Step 185D: multi-source flight ingestion -- resolves every
        independent per-source local file path
        (`scraped_flight_html_path_skyscanner`/`_google_flights`/
        `_kiwi_manual`) the same way
        `resolved_scraped_accommodation_html_paths_by_source` resolves
        its accommodation equivalents. Returns one entry per source
        regardless of whether a real file exists there -- callers must
        not assume any of these paths actually exist;
        `ScrapedLocalFlightProvider` is the one place that checks.
        """

        def _resolve(value: str | None) -> Path | None:
            if value is None:
                return None
            path = Path(value)
            return path if path.is_absolute() else _BACKEND_ROOT / path

        return {
            "skyscanner": _resolve(self.scraped_flight_html_path_skyscanner),
            "google_flights": _resolve(self.scraped_flight_html_path_google_flights),
            "kiwi_manual": _resolve(self.scraped_flight_html_path_kiwi_manual),
        }

    def resolved_scraped_hotel_ratings_html_path(self) -> Path | None:
        """Local, manually-supplied scraped-hotel-ratings HTML file path
        (Step 185B, wired to a real adapter in Step 185E) -- never a live
        website URL. Mirrors `resolved_scraped_accommodation_html_path`/
        `resolved_scraped_flight_html_path` exactly. Returns `None` when
        unset.
        """
        if self.scraped_hotel_ratings_html_path is None:
            return None
        path = Path(self.scraped_hotel_ratings_html_path)
        return path if path.is_absolute() else _BACKEND_ROOT / path

    def resolved_scraped_hotel_ratings_html_paths_by_source(self) -> dict[str, Path | None]:
        """Step 185E: multi-source hotel-ratings ingestion -- resolves
        every independent per-source local file path
        (`scraped_hotel_ratings_html_path_tripadvisor`/
        `_google_places_ratings`) the same way
        `resolved_scraped_flight_html_paths_by_source` resolves its flight
        equivalents. Returns one entry per source regardless of whether a
        real file exists there -- callers must not assume any of these
        paths actually exist; `ScrapedLocalHotelRatingsProvider` is the
        one place that checks.
        """

        def _resolve(value: str | None) -> Path | None:
            if value is None:
                return None
            path = Path(value)
            return path if path.is_absolute() else _BACKEND_ROOT / path

        return {
            "tripadvisor": _resolve(self.scraped_hotel_ratings_html_path_tripadvisor),
            "google_places_ratings": _resolve(
                self.scraped_hotel_ratings_html_path_google_places_ratings
            ),
        }


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached `Settings` -- every route/service/provider
    factory reads config through this, never `Settings()` directly.

    Step 183B-FIX: when `TRAVELOB_TEST_MODE=1` (set as the very first line
    of `backend/app/tests/conftest.py`, before any other import -- see
    that file's comment for why the ordering matters), this constructs
    `Settings(_env_file=None)` instead of `Settings()`, so the developer's
    real local `.env` (e.g. live `FLIGHT_PROVIDER=kiwi_mcp`,
    `ROUTING_PROVIDER=osrm`, `ITINERARY_NARRATOR_ENABLED=true`) never
    leaks into the automated test suite's config, regardless of whether a
    given test happens to `monkeypatch.setenv(...)` that specific field.
    A test can still opt into a specific value via `monkeypatch.setenv(...)`
    followed by `get_settings.cache_clear()` -- `_env_file=None` only
    disables the dotenv *file* source, real OS environment variables (which
    is exactly what `monkeypatch.setenv` sets) still apply.

    Normal app/dev-server runtime never sets `TRAVELOB_TEST_MODE`, so this
    branch never affects real usage -- `.env` is read exactly as before.
    """
    if os.environ.get("TRAVELOB_TEST_MODE") == "1":
        return Settings(_env_file=None)
    return Settings()