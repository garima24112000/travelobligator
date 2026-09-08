from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

# Tests for the Kiwi MCP flight provider config surface (Step 178B). See
# backend/app/tests/providers/test_kiwi_mcp_client.py and
# test_kiwi_mcp_adapter.py for the client/provider behavior tests
# themselves.


# ---------------------------------------------------------------------------
# 1. flight_provider default remains "scraped_local" -- unaffected by
#    adding kiwi_mcp as a supported (but non-default) provider name.
# ---------------------------------------------------------------------------


def test_flight_provider_default_still_scraped_local_after_kiwi_mcp_added() -> None:
    field_info = Settings.model_fields["flight_provider"]
    assert field_info.default == "scraped_local"


# ---------------------------------------------------------------------------
# 2. kiwi_mcp_enabled defaults to False.
# ---------------------------------------------------------------------------


def test_kiwi_mcp_enabled_default_is_false() -> None:
    field_info = Settings.model_fields["kiwi_mcp_enabled"]
    assert field_info.default is False
    assert field_info.alias == "KIWI_MCP_ENABLED"


def test_settings_constructs_without_any_kiwi_mcp_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API key, token, or credential is required for the Kiwi MCP
    config surface -- constructing `Settings()` with no `.env`/env-var
    input still yields a safe default: disabled, never a live call."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    settings = Settings()

    assert settings.kiwi_mcp_enabled is False


def test_kiwi_mcp_enabled_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("KIWI_MCP_ENABLED", "true")

    settings = Settings()

    assert settings.kiwi_mcp_enabled is True


# ---------------------------------------------------------------------------
# 3. kiwi_mcp_endpoint defaults to https://mcp.kiwi.com.
# ---------------------------------------------------------------------------


def test_kiwi_mcp_endpoint_default() -> None:
    field_info = Settings.model_fields["kiwi_mcp_endpoint"]
    assert field_info.default == "https://mcp.kiwi.com"
    assert field_info.alias == "KIWI_MCP_ENDPOINT"


def test_kiwi_mcp_endpoint_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("KIWI_MCP_ENDPOINT", "https://example-test-only-mcp.test")

    settings = Settings()

    assert settings.kiwi_mcp_endpoint == "https://example-test-only-mcp.test"


# ---------------------------------------------------------------------------
# 4. kiwi_mcp_timeout_seconds is a positive float, default ~10.0.
# ---------------------------------------------------------------------------


def test_kiwi_mcp_timeout_seconds_default() -> None:
    field_info = Settings.model_fields["kiwi_mcp_timeout_seconds"]
    assert field_info.default == pytest.approx(10.0)
    assert field_info.alias == "KIWI_MCP_TIMEOUT_SECONDS"


@pytest.mark.parametrize("invalid_timeout", [0.0, -1.0, -0.001])
def test_kiwi_mcp_timeout_seconds_rejects_non_positive(invalid_timeout: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, kiwi_mcp_timeout_seconds=invalid_timeout)


def test_kiwi_mcp_timeout_seconds_accepts_positive_override() -> None:
    settings = Settings(_env_file=None, kiwi_mcp_timeout_seconds=5.0)
    assert settings.kiwi_mcp_timeout_seconds == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# kiwi_mcp_tool_name is optional, defaults to None.
# ---------------------------------------------------------------------------


def test_kiwi_mcp_tool_name_default_is_none() -> None:
    field_info = Settings.model_fields["kiwi_mcp_tool_name"]
    assert field_info.default is None
    assert field_info.alias == "KIWI_MCP_TOOL_NAME"


def test_kiwi_mcp_tool_name_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("KIWI_MCP_TOOL_NAME", "search-flight")

    settings = Settings()

    assert settings.kiwi_mcp_tool_name == "search-flight"
