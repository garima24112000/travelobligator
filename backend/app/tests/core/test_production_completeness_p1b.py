from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.operational_config import (
    OperationalConfigurationError,
    production_configuration_problems,
    validate_runtime_configuration,
)

# P1-B: the production configuration is reproducible from the repository. `render.yaml` plus the secrets it
# names (and nothing typed into a dashboard from memory) is a COMPLETE configuration that starts, with the
# model stages the quality evaluation ran with; anything missing stops startup with a message that names
# the variable. Config-only: no PostgreSQL, Redis, provider or model is touched, and every "secret" below
# is a placeholder.

_ROOT = Path(__file__).resolve().parents[4]
_RENDER = yaml.safe_load((_ROOT / "render.yaml").read_text())
_ENV_EXAMPLE = (_ROOT / ".env.example").read_text()
_DOC = (_ROOT / "docs" / "22_free_tier_deployment.md").read_text()

# What an operator supplies in the hosting dashboard (`sync: false`) or the platform generates.
_PLACEHOLDERS = {
    "DATABASE_URL": "postgresql://owner:SENTINEL_P1B_DB_PASSWORD@db.example.test/app?sslmode=require",
    "REDIS_URL": "rediss://default:SENTINEL_P1B_REDIS_PASSWORD@cache.example.test:6379",
    "GROQ_API_KEY": "SENTINEL_P1B_GROQ_KEY",
    "GEMINI_API_KEY": "SENTINEL_P1B_GEMINI_KEY",
    "GEOAPIFY_API_KEY": "SENTINEL_P1B_GEOAPIFY_KEY",
    "SESSION_SECRET_KEY": "SENTINEL_P1B_SESSION_SECRET_" + "x" * 24,
    "OPS_TOKEN": "SENTINEL_P1B_OPS_TOKEN_" + "y" * 16,
    "BACKEND_CORS_ORIGINS": "https://frontend.example.test",
    "BETA_REGISTRATION_TOKEN": "SENTINEL_P1B_BETA_TOKEN_" + "z" * 16,
}
_SECRET_VALUES = [value for key, value in _PLACEHOLDERS.items() if key != "BACKEND_CORS_ORIGINS"]


def _render_env() -> dict[str, dict]:
    (service,) = _RENDER["services"]
    return {entry["key"]: entry for entry in service["envVars"]}


def _from_render(**overrides: object) -> Settings:
    """Settings exactly as a service created from render.yaml would have them: its literal values, plus a
    placeholder for every variable it leaves to the dashboard or to the platform."""
    env = {key: entry["value"] for key, entry in _render_env().items() if "value" in entry}
    supplied = {key for key, entry in _render_env().items() if "value" not in entry}
    assert supplied == set(_PLACEHOLDERS), "a dashboard-supplied variable has no placeholder in this test"
    return Settings(_env_file=None, **{**env, **_PLACEHOLDERS, **overrides})


def _rejected(**overrides: object) -> str:
    with pytest.raises(OperationalConfigurationError) as excinfo:
        validate_runtime_configuration(_from_render(**overrides))
    message = str(excinfo.value)
    for secret in _SECRET_VALUES:
        assert secret not in message  # a problem names the variable, never a value
    return message


# -- render.yaml is a complete production configuration ------------------------------------------------


def test_a_service_built_from_render_yaml_starts() -> None:
    settings = _from_render()
    assert production_configuration_problems(settings) == []
    validate_runtime_configuration(settings)


def test_render_yaml_carries_the_evaluated_model_configuration() -> None:
    """The model-stage settings the quality evaluation recorded (run provenance, `model_stages`). A
    rebuilt service plans with these, not with whatever defaults happen to apply."""
    settings = _from_render()
    assert {
        "ai_candidate_proposal_provider": settings.ai_candidate_proposal_provider,
        "ai_candidate_discovery_enabled": settings.ai_candidate_discovery_enabled,
        "ai_candidate_discovery_shadow_mode_enabled": settings.ai_candidate_discovery_shadow_mode_enabled,
        "ai_itinerary_reasoning_enabled": settings.ai_itinerary_reasoning_enabled,
        "ai_itinerary_reasoning_provider": settings.ai_itinerary_reasoning_provider,
        "ai_itinerary_reasoning_model": settings.ai_itinerary_reasoning_model,
        "ai_itinerary_repair_enabled": settings.ai_itinerary_repair_enabled,
        "itinerary_narrator_enabled": settings.itinerary_narrator_enabled,
        "itinerary_narrator_provider": settings.itinerary_narrator_provider,
        "itinerary_narrator_model": settings.itinerary_narrator_model,
        "groq_model": settings.groq_model,
        "gemini_model": settings.gemini_model,
        "llm_failover_enabled": settings.llm_failover_enabled,
        "llm_primary_provider": settings.llm_primary_provider,
        "llm_secondary_provider": settings.llm_secondary_provider,
    } == {
        "ai_candidate_proposal_provider": "groq",
        "ai_candidate_discovery_enabled": True,
        "ai_candidate_discovery_shadow_mode_enabled": False,
        "ai_itinerary_reasoning_enabled": True,
        "ai_itinerary_reasoning_provider": "groq",
        "ai_itinerary_reasoning_model": None,
        "ai_itinerary_repair_enabled": True,
        "itinerary_narrator_enabled": True,
        "itinerary_narrator_provider": "groq",
        "itinerary_narrator_model": None,
        "groq_model": "openai/gpt-oss-20b",
        "gemini_model": "gemini-3.5-flash-lite",
        "llm_failover_enabled": True,
        "llm_primary_provider": "groq",
        "llm_secondary_provider": "gemini",
    }


def test_render_yaml_leaves_the_quality_phase_flags_and_budgets_at_their_tested_defaults() -> None:
    """Nothing here re-tunes the planner: the quality-phase switches, the provider budgets and the stage
    budgets are whatever the code defaults are -- the values the evaluation ran with."""
    rendered, defaults = _from_render(), Settings(_env_file=None)
    untouched = (
        "planning_engine_mode",
        "day_composition_enabled",
        "route_recomposition_enabled",
        "route_burden_repair_enabled",
        "route_aware_scheduling_enabled",
        "ai_day_spatial_regrouping_enabled",
        "schedule_diversity_enabled",
        "provider_io_concurrency_enabled",
        "geoapify_max_concurrent_requests",
        "geoapify_process_max_concurrent_requests",
        "geoapify_max_place_details_per_generation",
        "geoapify_max_identity_lookups_per_generation",
        "route_alternate_mode_max_requests_per_generation",
        "route_recomposition_max_requests_per_generation",
        "ai_itinerary_reasoning_max_candidates",
        "ai_itinerary_repair_max_attempts",
        "groq_max_retries",
        "groq_anchor_total_budget_seconds",
        "groq_reasoning_total_budget_seconds",
        "groq_repair_total_budget_seconds",
        "groq_narrator_total_budget_seconds",
    )
    for name in untouched:
        assert getattr(rendered, name) == getattr(defaults, name), name
    names = set(_render_env())
    for prefix in ("DAY_COMPOSITION", "ROUTE_", "SCHEDULE_DIVERSITY", "GROQ_ANCHOR", "GROQ_REASONING",
                   "GROQ_REPAIR", "GROQ_NARRATOR", "AI_DAY_SPATIAL", "PLANNING_ENGINE"):
        assert not [name for name in names if name.startswith(prefix)], prefix
    # and the cost / abuse limits of P0 are exactly as approved
    assert rendered.geoapify_max_credits_per_generation == 100
    assert (
        rendered.generation_max_jobs_per_user_per_window,
        rendered.generation_max_jobs_global_per_window,
        rendered.generation_max_concurrent_jobs,
        rendered.max_trips_per_user,
        rendered.max_trip_days,
    ) == (5, 25, 2, 20, 7)


def test_secrets_are_referenced_by_name_only() -> None:
    env = _render_env()
    dashboard = {key for key, entry in env.items() if entry.get("sync") is False}
    generated = {key for key, entry in env.items() if entry.get("generateValue") is True}
    assert dashboard == {
        "DATABASE_URL", "REDIS_URL", "GROQ_API_KEY", "GEMINI_API_KEY", "GEOAPIFY_API_KEY", "BACKEND_CORS_ORIGINS",
        # limited beta: the owner chooses (and replaces) the shareable link's token
        "BETA_REGISTRATION_TOKEN",
    }
    assert generated == {"SESSION_SECRET_KEY", "OPS_TOKEN"}
    for key in dashboard | generated:
        assert "value" not in env[key], key
    # nothing that looks like a credential is written anywhere in the file
    text = (_ROOT / "render.yaml").read_text()
    for marker in ("sk-", "gsk_", "AIza", "://default:", "password"):
        assert marker not in text, marker


def test_every_secret_and_every_manual_setting_is_in_the_deployment_document() -> None:
    for name in (*_PLACEHOLDERS, "GEMINI_MODEL", "PRODUCTION_ALLOW_AI_STAGES_DISABLED",
                 "AI_ITINERARY_REASONING_ENABLED", "AI_ITINERARY_REPAIR_ENABLED", "ITINERARY_NARRATOR_ENABLED",
                 "FRONTEND_HSTS_MAX_AGE_SECONDS", "BACKEND_ORIGIN", "API_BASE_URL"):
        assert name in _DOC, name
    for name in ("PRODUCTION_ALLOW_AI_STAGES_DISABLED", "FRONTEND_HSTS_MAX_AGE_SECONDS", "GEMINI_API_KEY"):
        assert name in _ENV_EXAMPLE, name
    assert Settings.model_fields["production_allow_ai_stages_disabled"].default is False


# -- a missing secret stops startup, clearly ----------------------------------------------------------


@pytest.mark.parametrize(
    "variable, named",
    [
        ("DATABASE_URL", "DATABASE_URL"),
        ("SESSION_SECRET_KEY", "SESSION_SECRET_KEY"),
        ("GEOAPIFY_API_KEY", "GEOAPIFY_API_KEY"),
        ("GROQ_API_KEY", "GROQ_API_KEY"),
        ("GEMINI_API_KEY", "GEMINI_API_KEY"),
        ("GEMINI_MODEL", "GEMINI_MODEL"),
    ],
)
@pytest.mark.parametrize("missing", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_a_missing_required_value_stops_startup_and_names_the_variable(
    variable: str, named: str, missing: str | None
) -> None:
    assert named in _rejected(**{variable: missing})


def test_a_missing_groq_key_is_not_rescued_by_the_other_provider() -> None:
    message = _rejected(GROQ_API_KEY=None)
    assert "Groq provider requires GROQ_API_KEY" in message


def test_failover_needs_both_providers_or_an_explicit_single_provider_choice() -> None:
    message = _rejected(GEMINI_API_KEY=None)
    assert "LLM_FAILOVER_ENABLED=true requires GEMINI_API_KEY and GEMINI_MODEL" in message
    assert "LLM_FAILOVER_ENABLED=false" in message  # says how to run one provider on purpose
    # that explicit choice is accepted: Groq alone, no Gemini at all
    validate_runtime_configuration(
        _from_render(LLM_FAILOVER_ENABLED="false", GEMINI_API_KEY=None, GEMINI_MODEL=None)
    )
    # and the mirror image: every stage on Gemini, failover on, no Groq key
    gemini_only = dict(
        AI_CANDIDATE_PROPOSAL_PROVIDER="gemini",
        AI_ITINERARY_REASONING_PROVIDER="gemini",
        ITINERARY_NARRATOR_PROVIDER="gemini",
        GROQ_API_KEY=None,
    )
    assert "LLM_FAILOVER_ENABLED=true requires GROQ_API_KEY" in _rejected(**gemini_only)
    validate_runtime_configuration(_from_render(LLM_FAILOVER_ENABLED="false", **gemini_only))


# -- an AI capability is never silently off -----------------------------------------------------------


@pytest.mark.parametrize(
    "switch",
    [
        "AI_CANDIDATE_DISCOVERY_ENABLED",
        "AI_ITINERARY_REASONING_ENABLED",
        "AI_ITINERARY_REPAIR_ENABLED",
        "ITINERARY_NARRATOR_ENABLED",
    ],
)
def test_production_refuses_to_start_with_a_release_stage_switched_off(switch: str) -> None:
    message = _rejected(**{switch: "false"})
    assert f"{switch}=true" in message and "PRODUCTION_ALLOW_AI_STAGES_DISABLED=true" in message
    # switching it off on purpose is possible, and is one explicit setting
    validate_runtime_configuration(_from_render(**{switch: "false", "PRODUCTION_ALLOW_AI_STAGES_DISABLED": "true"}))


def test_a_service_with_no_model_configuration_at_all_does_not_start() -> None:
    """The clean-rebuild failure this phase exists for: the infrastructure settings are right, the AI
    settings were never typed in. Every stage is reported, in one message."""
    bare = {
        key: None
        for key in (
            "AI_CANDIDATE_PROPOSAL_PROVIDER", "AI_CANDIDATE_DISCOVERY_ENABLED", "AI_ITINERARY_REASONING_ENABLED",
            "AI_ITINERARY_REASONING_PROVIDER", "AI_ITINERARY_REPAIR_ENABLED", "ITINERARY_NARRATOR_ENABLED",
            "ITINERARY_NARRATOR_PROVIDER", "GROQ_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL",
        )
    }
    env = {key: entry["value"] for key, entry in _render_env().items() if "value" in entry}
    settings = Settings(_env_file=None, **{k: v for k, v in {**env, **_PLACEHOLDERS, **bare}.items() if v is not None})
    with pytest.raises(OperationalConfigurationError) as excinfo:
        validate_runtime_configuration(settings)
    for switch in ("AI_CANDIDATE_DISCOVERY_ENABLED", "AI_ITINERARY_REASONING_ENABLED",
                   "AI_ITINERARY_REPAIR_ENABLED", "ITINERARY_NARRATOR_ENABLED"):
        assert f"{switch}=true" in str(excinfo.value)


@pytest.mark.parametrize(
    "selector, stage",
    [
        ("AI_CANDIDATE_PROPOSAL_PROVIDER", "anchor discovery"),
        ("AI_ITINERARY_REASONING_PROVIDER", "itinerary reasoning"),
        ("ITINERARY_NARRATOR_PROVIDER", "narrator"),
    ],
)
def test_a_stage_that_is_on_must_have_a_connected_provider(selector: str, stage: str) -> None:
    message = _rejected(**{selector: "not_connected"})
    assert f"the {stage} stage is switched on but {selector}" in message and "would do nothing" in message


def test_repair_runs_on_the_reasoning_provider() -> None:
    message = _rejected(AI_ITINERARY_REASONING_PROVIDER="not_connected")
    assert "the itinerary repair stage is switched on but AI_ITINERARY_REASONING_PROVIDER" in message


def test_the_optional_feedback_interpreter_is_never_required_but_is_checked_when_on() -> None:
    assert _from_render().ai_feedback_interpreter_enabled is False  # not part of render.yaml
    message = _rejected(AI_FEEDBACK_INTERPRETER_ENABLED="true")
    assert "the feedback interpreter stage is switched on but AI_FEEDBACK_INTERPRETER_PROVIDER" in message
    assert "ANTHROPIC_API_KEY" in _rejected(
        AI_FEEDBACK_INTERPRETER_ENABLED="true", AI_FEEDBACK_INTERPRETER_PROVIDER="anthropic"
    )
    validate_runtime_configuration(
        _from_render(AI_FEEDBACK_INTERPRETER_ENABLED="true", AI_FEEDBACK_INTERPRETER_PROVIDER="groq")
    )


def test_every_problem_is_reported_in_one_message() -> None:
    message = _rejected(GROQ_API_KEY=None, GEOAPIFY_API_KEY=None, ITINERARY_NARRATOR_ENABLED="false")
    for named in ("GROQ_API_KEY", "GEOAPIFY_API_KEY", "ITINERARY_NARRATOR_ENABLED=true"):
        assert named in message


def test_the_opt_out_relaxes_only_the_disabled_stage_check() -> None:
    relaxed = dict(PRODUCTION_ALLOW_AI_STAGES_DISABLED="true")
    # a stage that is ON still needs its provider and its key
    assert "GROQ_API_KEY" in _rejected(GROQ_API_KEY=None, **relaxed)
    assert "would do nothing" in _rejected(ITINERARY_NARRATOR_PROVIDER="not_connected", **relaxed)
    # and every other production rule still applies
    assert "SESSION_COOKIE_SECURE" in _rejected(SESSION_COOKIE_SECURE="false", **relaxed)
    assert "GENERATION_MAX_CONCURRENT_JOBS" in _rejected(GENERATION_MAX_CONCURRENT_JOBS="0", **relaxed)


# -- the process itself refuses to start ---------------------------------------------------------------


def test_the_application_does_not_start_in_production_without_a_required_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.main import app

    env = {key: entry["value"] for key, entry in _render_env().items() if "value" in entry}
    for key, value in {**env, **_PLACEHOLDERS}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GEMINI_API_KEY")
    get_settings.cache_clear()
    with pytest.raises(OperationalConfigurationError) as excinfo:
        with TestClient(app):  # entering runs the startup sequence
            pytest.fail("the application started without GEMINI_API_KEY")
    assert "GEMINI_API_KEY" in str(excinfo.value)
    for secret in _SECRET_VALUES:
        assert secret not in str(excinfo.value)


# -- development is not affected -----------------------------------------------------------------------


def test_development_needs_none_of_this() -> None:
    development = {"APP_ENV": "development", "PERSISTENCE_BACKEND": "local_json", "PROVIDER_CACHE_BACKEND": "sqlite"}
    settings = Settings(_env_file=None, **development)
    assert settings.ai_itinerary_reasoning_enabled is False and not settings.groq_api_key
    validate_runtime_configuration(settings)
    # a half-configured development setup is the developer's business
    validate_runtime_configuration(
        Settings(_env_file=None, AI_ITINERARY_REASONING_ENABLED="true", LLM_FAILOVER_ENABLED="true", **development)
    )
