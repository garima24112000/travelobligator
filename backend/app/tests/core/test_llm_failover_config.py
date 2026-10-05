from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core import redaction
from app.core.config import Settings, get_settings
from app.core.logging_config import _is_sensitive_field_name
from app.providers.llm_provider_health import GEMINI, GROQ
from app.providers.llm_provider_router import configured_pair_providers, resolve_chain

# Configuration of the Groq <-> Gemini resilience group: every new setting,
# its validation, and that nothing about Gemini is required to start.

_REPO_ROOT = Path(__file__).resolve().parents[4]
_KEY = "SENTINEL_GEMINI_CONFIG_KEY_0001"


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)


def test_the_defaults() -> None:
    settings = _settings()
    # (the suite blanks GEMINI_API_KEY in the environment, so it reads as empty here)
    assert not settings.gemini_api_key and settings.gemini_model is None
    assert (settings.llm_primary_provider, settings.llm_secondary_provider) == ("groq", "gemini")
    assert settings.llm_failover_enabled is True
    assert settings.llm_quota_reserve_ratio == 0.10
    assert (settings.llm_provider_short_cooldown_seconds, settings.llm_provider_probe_seconds) == (60.0, 300.0)
    assert (settings.gemini_rpm_limit, settings.gemini_tpm_limit, settings.gemini_rpd_limit) == (None, None, None)


def test_the_gemini_model_has_no_default_and_no_limit_has_one() -> None:
    for name in ("gemini_model", "gemini_rpm_limit", "gemini_tpm_limit", "gemini_rpd_limit", "gemini_api_key"):
        assert Settings.model_fields[name].default is None, name


@pytest.mark.parametrize(
    ("values", "configured"),
    [
        ({"GROQ_API_KEY": "g"}, [GROQ]),  # A: only Groq
        ({"GEMINI_API_KEY": _KEY, "GEMINI_MODEL": "some-model"}, [GEMINI]),  # B: only Gemini
        ({"GROQ_API_KEY": "g", "GEMINI_API_KEY": _KEY, "GEMINI_MODEL": "some-model"}, [GROQ, GEMINI]),  # C: both
        ({}, []),  # D: neither
        ({"GROQ_API_KEY": "g", "GEMINI_API_KEY": _KEY}, [GROQ]),  # a key without a model is not configured
        ({"GROQ_API_KEY": "g", "GEMINI_MODEL": "some-model"}, [GROQ]),
        ({"GROQ_API_KEY": "", "GEMINI_API_KEY": "", "GEMINI_MODEL": ""}, []),
    ],
)
def test_which_members_of_the_pair_are_configured(values: dict[str, str], configured: list[str]) -> None:
    assert configured_pair_providers(_settings(**values)) == configured


def test_the_app_starts_without_any_gemini_configuration() -> None:
    settings = _settings(GROQ_API_KEY="g")
    assert resolve_chain(GROQ, settings) == [GROQ]  # existing Groq-only behaviour
    assert resolve_chain(GROQ, _settings()) == []  # neither: the stage reports not_connected, as before


@pytest.mark.parametrize("enabled", [True, False])
def test_failover_can_be_switched_on_and_off(enabled: bool) -> None:
    settings = _settings(
        GROQ_API_KEY="g", GEMINI_API_KEY=_KEY, GEMINI_MODEL="some-model", LLM_FAILOVER_ENABLED=str(enabled).lower()
    )
    assert settings.llm_failover_enabled is enabled
    assert resolve_chain(GROQ, settings) == ([GROQ, GEMINI] if enabled else [GROQ])


def test_the_pair_order_is_configurable_and_normalized() -> None:
    settings = _settings(LLM_PRIMARY_PROVIDER=" Gemini ", LLM_SECONDARY_PROVIDER="GROQ")
    assert (settings.llm_primary_provider, settings.llm_secondary_provider) == ("gemini", "groq")


@pytest.mark.parametrize("value", ["anthropic", "openai", "not_connected", "", "gemini-pro", "groq,gemini"])
def test_an_unsupported_primary_provider_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError, match="LLM_PRIMARY_PROVIDER / LLM_SECONDARY_PROVIDER"):
        _settings(LLM_PRIMARY_PROVIDER=value)


@pytest.mark.parametrize("value", ["anthropic", "openai", "not_connected", "", "none"])
def test_an_unsupported_secondary_provider_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError, match="LLM_PRIMARY_PROVIDER / LLM_SECONDARY_PROVIDER"):
        _settings(LLM_SECONDARY_PROVIDER=value)


def test_the_two_providers_of_the_pair_must_differ() -> None:
    with pytest.raises(ValidationError, match="must be different"):
        _settings(LLM_PRIMARY_PROVIDER="gemini", LLM_SECONDARY_PROVIDER="gemini")
    with pytest.raises(ValidationError, match="must be different"):
        _settings(LLM_SECONDARY_PROVIDER="groq")


@pytest.mark.parametrize("value", ["0", "0.05", "0.5", "0.99"])
def test_a_valid_quota_reserve_ratio_is_accepted(value: str) -> None:
    assert _settings(LLM_QUOTA_RESERVE_RATIO=value).llm_quota_reserve_ratio == float(value)


@pytest.mark.parametrize("value", ["-0.1", "1", "1.5", "ten percent"])
def test_an_invalid_quota_reserve_ratio_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        _settings(LLM_QUOTA_RESERVE_RATIO=value)


@pytest.mark.parametrize("name", ["LLM_PROVIDER_SHORT_COOLDOWN_SECONDS", "LLM_PROVIDER_PROBE_SECONDS"])
@pytest.mark.parametrize("value", ["-1", "0", "-60.5", "soon"])
def test_a_negative_or_zero_cooldown_is_rejected(name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _settings(**{name: value})


def test_cooldowns_accept_positive_values() -> None:
    settings = _settings(LLM_PROVIDER_SHORT_COOLDOWN_SECONDS="15", LLM_PROVIDER_PROBE_SECONDS="900.5")
    assert (settings.llm_provider_short_cooldown_seconds, settings.llm_provider_probe_seconds) == (15.0, 900.5)


def test_optional_gemini_limits_are_absent_by_default_and_blank_means_unset() -> None:
    # a dotenv line such as `GEMINI_RPM_LIMIT=` is "not configured", not a startup failure
    settings = _settings(GEMINI_RPM_LIMIT="", GEMINI_TPM_LIMIT="   ", GEMINI_RPD_LIMIT="", GEMINI_MODEL="  ")
    assert (settings.gemini_rpm_limit, settings.gemini_tpm_limit, settings.gemini_rpd_limit) == (None, None, None)
    assert settings.gemini_model is None


def test_valid_optional_gemini_limits_are_read() -> None:
    settings = _settings(GEMINI_RPM_LIMIT="15", GEMINI_TPM_LIMIT="250000", GEMINI_RPD_LIMIT="1000")
    assert (settings.gemini_rpm_limit, settings.gemini_tpm_limit, settings.gemini_rpd_limit) == (15, 250_000, 1000)


@pytest.mark.parametrize("name", ["GEMINI_RPM_LIMIT", "GEMINI_TPM_LIMIT", "GEMINI_RPD_LIMIT"])
@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5", "10 per minute"])
def test_a_malformed_optional_gemini_limit_is_rejected_like_any_other_typed_setting(name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _settings(**{name: value})


def test_the_environment_is_read_through_get_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", _KEY)
    monkeypatch.setenv("GEMINI_MODEL", "some-model")
    monkeypatch.setenv("GEMINI_RPD_LIMIT", "500")
    get_settings.cache_clear()
    settings = get_settings()
    assert (settings.gemini_api_key, settings.gemini_model, settings.gemini_rpd_limit) == (_KEY, "some-model", 500)


def test_the_gemini_key_is_handled_as_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(GEMINI_API_KEY=_KEY, GEMINI_MODEL="some-model")
    assert _KEY not in repr(settings) and _KEY not in str(settings)
    # covered by the existing log-field and redaction rules, with no new special case
    assert _is_sensitive_field_name("gemini_api_key")
    monkeypatch.setenv("GEMINI_API_KEY", _KEY)
    get_settings.cache_clear()
    assert _KEY in redaction.configured_secret_values()
    assert _KEY not in redaction.redact(f"request failed for key {_KEY}")


# -- committed artefacts ----------------------------------------------------------------------


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (_REPO_ROOT / ".env.example").read_text().splitlines():
        match = re.match(r"^(#\s*)?([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            values[match.group(2)] = match.group(3)
    return values


def test_env_example_documents_every_new_setting_with_safe_values_only() -> None:
    example = _env_example()
    assert example["GEMINI_API_KEY"] == "" and example["GEMINI_MODEL"] == ""
    assert (example["LLM_PRIMARY_PROVIDER"], example["LLM_SECONDARY_PROVIDER"]) == ("groq", "gemini")
    assert example["LLM_FAILOVER_ENABLED"] == "true"
    assert example["LLM_QUOTA_RESERVE_RATIO"] == "0.10"
    assert (example["LLM_PROVIDER_SHORT_COOLDOWN_SECONDS"], example["LLM_PROVIDER_PROBE_SECONDS"]) == ("60", "300")
    # the optional limits are listed without a guessed number
    assert (example["GEMINI_RPM_LIMIT"], example["GEMINI_TPM_LIMIT"], example["GEMINI_RPD_LIMIT"]) == ("", "", "")


def test_the_env_example_values_load_into_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    example = _env_example()
    names = [
        "GEMINI_API_KEY", "GEMINI_MODEL", "LLM_PRIMARY_PROVIDER", "LLM_SECONDARY_PROVIDER", "LLM_FAILOVER_ENABLED",
        "LLM_QUOTA_RESERVE_RATIO", "LLM_PROVIDER_SHORT_COOLDOWN_SECONDS", "LLM_PROVIDER_PROBE_SECONDS",
        "GEMINI_RPM_LIMIT", "GEMINI_TPM_LIMIT", "GEMINI_RPD_LIMIT",
    ]
    settings = _settings(**{name: example[name] for name in names})
    assert settings.gemini_model is None and settings.gemini_rpm_limit is None
    assert not settings.gemini_api_key and settings.llm_failover_enabled is True


def test_requirements_pin_the_google_sdk_exactly_and_none_of_its_transitive_dependencies() -> None:
    lines = [line.strip() for line in (_REPO_ROOT / "backend" / "requirements.txt").read_text().splitlines()]
    packages = [line for line in lines if line and not line.startswith("#")]
    assert "google-genai==2.28.0" in packages
    assert [line for line in packages if line.lower().startswith("google")] == ["google-genai==2.28.0"]
    for transitive in ("google-auth", "pyasn1", "pyasn1-modules", "pyasn1_modules", "tenacity", "websockets"):
        assert not any(re.split(r"[=<>~ ]", line)[0].lower() == transitive for line in packages), transitive
    # every requirement line is still a valid pin or bare name (no `name version` typo)
    assert all(re.fullmatch(r"[A-Za-z0-9_.\-]+(\[[a-z,]+\])?(==[A-Za-z0-9_.]+)?", line) for line in packages)
