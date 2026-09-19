from __future__ import annotations

import pytest

from app.core.config import Settings

# Config surface for the Section 193B AI itinerary-reasoning provider layer
# (docs/14_backend_architecture.md section 142), mirroring
# `test_ai_candidate_proposal_factory.py`'s config coverage and
# `itinerary_narrator_enabled`/`itinerary_narrator_provider`'s own tests.


def test_ai_itinerary_reasoning_enabled_default_is_false() -> None:
    field_info = Settings.model_fields["ai_itinerary_reasoning_enabled"]
    assert field_info.default is False
    assert field_info.alias == "AI_ITINERARY_REASONING_ENABLED"


def test_ai_itinerary_reasoning_provider_default_is_not_connected() -> None:
    field_info = Settings.model_fields["ai_itinerary_reasoning_provider"]
    assert field_info.default == "not_connected"
    assert field_info.alias == "AI_ITINERARY_REASONING_PROVIDER"


def test_ai_itinerary_reasoning_model_default_is_unset() -> None:
    field_info = Settings.model_fields["ai_itinerary_reasoning_model"]
    assert field_info.default is None
    assert field_info.alias == "AI_ITINERARY_REASONING_MODEL"


def test_settings_constructs_without_any_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """No API key, token, or credential is required -- constructing
    `Settings()` with no `.env`/env-var input yields safe, conservative
    defaults (reasoning disabled)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.ai_itinerary_reasoning_enabled is False
    assert settings.ai_itinerary_reasoning_provider == "not_connected"
    assert settings.ai_itinerary_reasoning_model is None


def test_ai_itinerary_reasoning_enabled_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("AI_ITINERARY_REASONING_ENABLED", "true")

    settings = Settings()

    assert settings.ai_itinerary_reasoning_enabled is True


def test_ai_itinerary_reasoning_provider_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("AI_ITINERARY_REASONING_PROVIDER", "groq")

    settings = Settings()

    assert settings.ai_itinerary_reasoning_provider == "groq"


def test_ai_itinerary_reasoning_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("AI_ITINERARY_REASONING_MODEL", "custom-model")

    settings = Settings()

    assert settings.ai_itinerary_reasoning_model == "custom-model"


def test_no_new_api_key_setting_was_introduced() -> None:
    """Section 193B must reuse the existing groq_api_key/anthropic_api_key
    settings, never duplicate a secret under a new name."""
    field_names = set(Settings.model_fields.keys())
    assert "ai_itinerary_reasoning_api_key" not in field_names
    assert "ai_itinerary_reasoning_groq_api_key" not in field_names
    assert "ai_itinerary_reasoning_anthropic_api_key" not in field_names
