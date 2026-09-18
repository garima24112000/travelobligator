from __future__ import annotations

import pytest

from app.core.config import Settings

# Config surface for the Section 193A itinerary-reasoning candidate-universe
# bound (docs/14_backend_architecture.md section 141). Contract-only in
# this step -- no live provider reads this setting yet.


def test_ai_itinerary_reasoning_max_candidates_default() -> None:
    field_info = Settings.model_fields["ai_itinerary_reasoning_max_candidates"]
    assert field_info.default == 40
    assert field_info.alias == "AI_ITINERARY_REASONING_MAX_CANDIDATES"


def test_settings_constructs_without_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.ai_itinerary_reasoning_max_candidates == 40


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("AI_ITINERARY_REASONING_MAX_CANDIDATES", "10")

    settings = Settings()

    assert settings.ai_itinerary_reasoning_max_candidates == 10


def test_rejects_zero_or_negative_value() -> None:
    with pytest.raises(ValueError):
        Settings(ai_itinerary_reasoning_max_candidates=0)
    with pytest.raises(ValueError):
        Settings(ai_itinerary_reasoning_max_candidates=-1)
