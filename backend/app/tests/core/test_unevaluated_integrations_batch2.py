from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.operational_config import (
    OperationalConfigurationError,
    startup_config_summary,
    validate_runtime_configuration,
)
from app.tests.core.test_production_completeness_p1b import (
    _PLACEHOLDERS,
    _from_render,
    _rejected,
    _render_env,
)

# Final Batch 2: production never runs an integration the release was not evaluated with because a
# dashboard value switched it on. render.yaml pins each one off; startup refuses each one unless
# PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS names it. Config-only: nothing is called.

_SWITCHES = {
    "targeted_regeneration": {"TARGETED_REGENERATION_ENABLED": "true"},
    # the interpreter's own provider check still applies, so give it a connected one
    "feedback_interpreter": {"AI_FEEDBACK_INTERPRETER_ENABLED": "true", "AI_FEEDBACK_INTERPRETER_PROVIDER": "groq"},
    "kiwi_flights": {"FLIGHT_PROVIDER": "kiwi_mcp", "KIWI_MCP_ENABLED": "true"},
    "airport_lookup": {"FLIGHT_AIRPORT_LOOKUP_PROVIDER": "ourairports_file"},
}


def test_render_yaml_pins_every_unevaluated_integration_and_manual_source_off() -> None:
    pinned = {key: entry.get("value") for key, entry in _render_env().items()}
    assert {
        key: pinned.get(key)
        for key in (
            "TARGETED_REGENERATION_ENABLED", "AI_FEEDBACK_INTERPRETER_ENABLED", "KIWI_MCP_ENABLED",
            "FLIGHT_AIRPORT_LOOKUP_PROVIDER", "FLIGHT_PROVIDER", "ACCOMMODATION_PROVIDER",
            "HOTEL_RATINGS_PROVIDER",
        )
    } == {
        "TARGETED_REGENERATION_ENABLED": "false",
        "AI_FEEDBACK_INTERPRETER_ENABLED": "false",
        "KIWI_MCP_ENABLED": "false",
        "FLIGHT_AIRPORT_LOOKUP_PROVIDER": "not_connected",
        "FLIGHT_PROVIDER": "not_connected",
        "ACCOMMODATION_PROVIDER": "not_connected",
        "HOTEL_RATINGS_PROVIDER": "not_connected",
    }
    assert "PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS" not in pinned  # no allowance in the release
    settings = _from_render()
    assert settings.targeted_regeneration_enabled is False and settings.ai_feedback_interpreter_enabled is False
    assert startup_config_summary(settings)["production_integration_overrides"] == "none"


@pytest.mark.parametrize("name", list(_SWITCHES))
def test_a_switched_on_unevaluated_integration_stops_production_startup(name: str) -> None:
    message = _rejected(**_SWITCHES[name])
    assert f"the {name} integration, which the release was not evaluated with" in message
    assert f"PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS={name}" in message


@pytest.mark.parametrize("switch", [{"FLIGHT_PROVIDER": "kiwi_mcp"}, {"KIWI_MCP_ENABLED": "true"}])
def test_either_kiwi_opt_in_alone_is_refused(switch: dict[str, str]) -> None:
    assert "kiwi_flights integration" in _rejected(**switch)


@pytest.mark.parametrize("name", list(_SWITCHES))
def test_the_allowance_names_one_integration_and_relaxes_only_that(name: str) -> None:
    validate_runtime_configuration(
        _from_render(PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS=name, **_SWITCHES[name])
    )
    assert startup_config_summary(
        _from_render(PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS=name, **_SWITCHES[name])
    )["production_integration_overrides"] == name
    # another integration still needs its own name
    other = next(candidate for candidate in _SWITCHES if candidate != name)
    message = _rejected(PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS=name, **_SWITCHES[name], **_SWITCHES[other])
    assert f"PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS={other}" in message


def test_the_allowance_never_relaxes_another_production_rule() -> None:
    allowed = dict(PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS="feedback_interpreter,targeted_regeneration")
    # a switched-on interpreter still needs a connected provider and that provider's key
    assert "would do nothing" in _rejected(AI_FEEDBACK_INTERPRETER_ENABLED="true", **allowed)
    assert "ANTHROPIC_API_KEY" in _rejected(
        AI_FEEDBACK_INTERPRETER_ENABLED="true", AI_FEEDBACK_INTERPRETER_PROVIDER="anthropic", **allowed
    )
    # beta access, cookies and cost limits are untouched
    assert "SIGNUP_MODE=open" in _rejected(SIGNUP_MODE="open", **allowed)
    assert "SESSION_COOKIE_SECURE" in _rejected(SESSION_COOKIE_SECURE="false", **allowed)
    assert "GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW" in _rejected(GENERATION_MAX_JOBS_GLOBAL_PER_WINDOW="0", **allowed)
    assert "INVENTORY_SUFFICIENCY_GATE_ENABLED" in _rejected(INVENTORY_SUFFICIENCY_GATE_ENABLED="false", **allowed)


@pytest.mark.parametrize("value", ["kiwi", "all", "*", "true", "kiwi_flights,everything"])
def test_an_unknown_allowance_name_stops_startup_rather_than_being_ignored(value: str) -> None:
    message = _rejected(PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS=value)
    assert "PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS may only name" in message


def test_the_allowance_is_case_and_space_tolerant_but_exact() -> None:
    validate_runtime_configuration(
        _from_render(PRODUCTION_ALLOW_UNEVALUATED_INTEGRATIONS=" Targeted_Regeneration ", TARGETED_REGENERATION_ENABLED="true")
    )


def test_the_application_does_not_start_in_production_with_targeted_regeneration_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.main import app

    env = {key: entry["value"] for key, entry in _render_env().items() if "value" in entry}
    for key, value in {**env, **_PLACEHOLDERS}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TARGETED_REGENERATION_ENABLED", "true")  # e.g. a leftover dashboard value
    get_settings.cache_clear()
    with pytest.raises(OperationalConfigurationError) as excinfo:
        with TestClient(app):
            pytest.fail("the application started with an unevaluated integration switched on")
    assert "TARGETED_REGENERATION_ENABLED" in str(excinfo.value)


def test_development_is_not_affected() -> None:
    development = {"APP_ENV": "development", "PERSISTENCE_BACKEND": "local_json", "PROVIDER_CACHE_BACKEND": "sqlite"}
    validate_runtime_configuration(
        Settings(_env_file=None, TARGETED_REGENERATION_ENABLED="true", KIWI_MCP_ENABLED="true",
                 FLIGHT_PROVIDER="kiwi_mcp", FLIGHT_AIRPORT_LOOKUP_PROVIDER="ourairports_file", **development)
    )
