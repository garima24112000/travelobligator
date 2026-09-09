from __future__ import annotations

import pytest

from app.core.config import Settings

# Tests for the itinerary narrator's config surface (Step 182F,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
# See backend/app/tests/providers/test_itinerary_narrator_factory.py and
# test_groq_itinerary_narrator_provider.py/
# test_anthropic_itinerary_narrator_provider.py for the provider/factory
# behavior tests themselves.


def test_itinerary_narrator_disabled_by_default() -> None:
    field_info = Settings.model_fields["itinerary_narrator_enabled"]
    assert field_info.default is False
    assert field_info.alias == "ITINERARY_NARRATOR_ENABLED"


def test_itinerary_narrator_provider_default_is_not_connected() -> None:
    field_info = Settings.model_fields["itinerary_narrator_provider"]
    assert field_info.default == "not_connected"
    assert field_info.alias == "ITINERARY_NARRATOR_PROVIDER"


def test_itinerary_narrator_is_a_separate_config_surface_from_ai_candidate_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user's own instruction: do not reuse
    AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED for the narrator -- these
    are two independent env vars that can be set independently."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ITINERARY_NARRATOR_ENABLED", "true")
    monkeypatch.setenv("AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED", "false")

    settings = Settings()
    assert settings.itinerary_narrator_enabled is True
    assert settings.ai_candidate_discovery_shadow_mode_enabled is False

    monkeypatch.setenv("ITINERARY_NARRATOR_ENABLED", "false")
    monkeypatch.setenv("AI_CANDIDATE_DISCOVERY_SHADOW_MODE_ENABLED", "true")

    settings2 = Settings()
    assert settings2.itinerary_narrator_enabled is False
    assert settings2.ai_candidate_discovery_shadow_mode_enabled is True


def test_itinerary_narrator_model_defaults_to_none() -> None:
    field_info = Settings.model_fields["itinerary_narrator_model"]
    assert field_info.default is None
    assert field_info.alias == "ITINERARY_NARRATOR_MODEL"


def test_itinerary_narrator_timeout_and_prompt_size_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.itinerary_narrator_timeout_seconds == 20.0
    assert settings.itinerary_narrator_max_days == 10
    assert settings.itinerary_narrator_max_items_per_day == 6


def test_settings_constructs_without_any_itinerary_narrator_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API key, token, or credential is required for the narrator
    config surface -- constructing `Settings()` with no `.env`/env-var
    input still yields usable, safe defaults: disabled, not_connected."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.itinerary_narrator_enabled is False
    assert settings.itinerary_narrator_provider == "not_connected"


def test_itinerary_narrator_enabled_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ITINERARY_NARRATOR_ENABLED", "true")
    monkeypatch.setenv("ITINERARY_NARRATOR_PROVIDER", "groq")

    settings = Settings()

    assert settings.itinerary_narrator_enabled is True
    assert settings.itinerary_narrator_provider == "groq"


def test_itinerary_narrator_max_days_rejects_zero_or_negative() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, itinerary_narrator_max_days=0)


def test_itinerary_narrator_max_items_per_day_rejects_zero_or_negative() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, itinerary_narrator_max_items_per_day=0)


def test_itinerary_narrator_timeout_seconds_rejects_zero_or_negative() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, itinerary_narrator_timeout_seconds=0.0)
