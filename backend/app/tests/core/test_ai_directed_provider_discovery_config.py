from __future__ import annotations

import pytest

from app.core.config import Settings

# Config surface for the Section 192 AI-directed provider discovery search
# bound (docs/14_backend_architecture.md section 138). See
# backend/app/tests/services/test_ai_directed_provider_discovery_service.py
# for the behavioral bound tests themselves.


def test_ai_directed_provider_discovery_max_searches_default() -> None:
    field_info = Settings.model_fields["ai_directed_provider_discovery_max_searches"]
    assert field_info.default == 5
    assert field_info.alias == "AI_DIRECTED_PROVIDER_DISCOVERY_MAX_SEARCHES"


def test_settings_constructs_without_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.ai_directed_provider_discovery_max_searches == 5


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("AI_DIRECTED_PROVIDER_DISCOVERY_MAX_SEARCHES", "2")

    settings = Settings()

    assert settings.ai_directed_provider_discovery_max_searches == 2


def test_rejects_negative_value() -> None:
    with pytest.raises(ValueError):
        Settings(ai_directed_provider_discovery_max_searches=-1)


def test_zero_is_a_valid_value() -> None:
    """Zero is a legitimate, if extreme, configuration: it disables every
    targeted provider lookup (every proposal that needs one is recorded as
    `not_searched`) without touching the separate
    `AI_CANDIDATE_DISCOVERY_ENABLED` feature gate."""
    settings = Settings(ai_directed_provider_discovery_max_searches=0)
    assert settings.ai_directed_provider_discovery_max_searches == 0
