from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.operational_config import (
    OperationalConfigurationError,
    production_configuration_problems,
    validate_runtime_configuration,
)

# Section 203B: deterministic production-configuration validation. Config-only -- no PostgreSQL, Redis or
# network is touched; every value below is a syntactically valid placeholder, never a real credential.

_DB_SECRET = "SENTINEL_203B_DB_PASSWORD_6610"
_REDIS_SECRET = "SENTINEL_203B_REDIS_PASSWORD_2284"
_SESSION_SECRET = "SENTINEL_203B_SESSION_SECRET_" + "x" * 24
_OPS_SECRET = "SENTINEL_203B_OPS_TOKEN_" + "y" * 16

_PRODUCTION = {
    "APP_ENV": "production",
    "APP_DEBUG": "false",
    "PERSISTENCE_BACKEND": "postgres",
    # the managed-PostgreSQL shape: TLS + channel binding in the query string
    "DATABASE_URL": f"postgresql://owner:{_DB_SECRET}@db.example.test/app?sslmode=require&channel_binding=require",
    "PROVIDER_CACHE_BACKEND": "redis",
    "REDIS_URL": f"rediss://default:{_REDIS_SECRET}@cache.example.test:6379",
    "SESSION_SECRET_KEY": _SESSION_SECRET,
    "SESSION_COOKIE_SECURE": "true",
    "SESSION_COOKIE_SAMESITE": "lax",
    "BACKEND_CORS_ORIGINS": "https://frontend.example.test",
    "METRICS_ENABLED": "false",
    "OPS_TOKEN": _OPS_SECRET,
    # Section 203C.1: production geocoding is Geoapify, never the public Nominatim endpoint
    "GEOCODING_PROVIDER": "geoapify",
    "GEOAPIFY_API_KEY": "SENTINEL_203C1_GEOAPIFY_KEY_5527",
    # Section 203C.2B: production POI discovery and routing are Geoapify
    "PLACES_PROVIDER": "geoapify",
    "ROUTING_PROVIDER": "geoapify",
    "INVENTORY_SUFFICIENCY_GATE_ENABLED": "true",
}


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**_PRODUCTION, **overrides})


def _rejected(**overrides: object) -> str:
    with pytest.raises(OperationalConfigurationError) as excinfo:
        validate_runtime_configuration(_settings(**overrides))
    message = str(excinfo.value)
    for secret in (_DB_SECRET, _REDIS_SECRET, _SESSION_SECRET, _OPS_SECRET):
        assert secret not in message
    return message


def test_the_documented_production_configuration_is_accepted() -> None:
    settings = _settings()
    assert production_configuration_problems(settings) == []
    validate_runtime_configuration(settings)


def test_production_accepts_no_cross_origin_browser_at_all() -> None:
    # same-origin proxy design: an empty allowlist is valid
    validate_runtime_configuration(_settings(BACKEND_CORS_ORIGINS=""))


def test_production_rejects_local_json_persistence() -> None:
    assert "PERSISTENCE_BACKEND=postgres" in _rejected(PERSISTENCE_BACKEND="local_json")


def test_production_rejects_a_missing_postgres_url() -> None:
    assert "DATABASE_URL" in _rejected(DATABASE_URL=None)


@pytest.mark.parametrize("url", ["sqlite:///prod.db", "postgresql://owner@", "not a url"])
def test_production_rejects_a_non_postgres_or_malformed_database_url(url: str) -> None:
    assert "DATABASE_URL" in _rejected(DATABASE_URL=url)


def test_production_rejects_the_sqlite_provider_cache() -> None:
    assert "PROVIDER_CACHE_BACKEND=redis" in _rejected(PROVIDER_CACHE_BACKEND="sqlite")


def test_production_allows_the_provider_cache_kill_switch() -> None:
    validate_runtime_configuration(_settings(PROVIDER_CACHE_ENABLED="false", PROVIDER_CACHE_BACKEND="sqlite"))


def test_an_unknown_provider_cache_backend_is_rejected_when_settings_load() -> None:
    with pytest.raises(ValidationError):
        _settings(PROVIDER_CACHE_BACKEND="memcached")


def test_production_rejects_a_malformed_redis_url() -> None:
    assert "REDIS_URL" in _rejected(REDIS_URL=f"https://default:{_REDIS_SECRET}@cache.example.test")


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"SESSION_COOKIE_SECURE": "false"}, "SESSION_COOKIE_SECURE=true"),
        ({"SESSION_COOKIE_HTTPONLY": "false"}, "SESSION_COOKIE_HTTPONLY=true"),
        ({"SESSION_COOKIE_SAMESITE": "none"}, "SESSION_COOKIE_SAMESITE"),
        ({"SESSION_SECRET_KEY": None}, "SESSION_SECRET_KEY"),
        ({"SESSION_SECRET_KEY": "too-short"}, "SESSION_SECRET_KEY"),
        ({"APP_DEBUG": "true"}, "APP_DEBUG=false"),
        ({"OPS_TOKEN": "short"}, "OPS_TOKEN"),
    ],
)
def test_production_rejects_insecure_session_debug_and_token_settings(overrides: dict, needle: str) -> None:
    assert needle in _rejected(**overrides)


@pytest.mark.parametrize(
    "origins",
    [
        "*",
        "https://frontend.example.test,*",
        "https://*.example.test",
        f"https://user:{_DB_SECRET}@frontend.example.test",
        "https://frontend.example.test/app",
        "https://frontend.example.test?x=1",
        "frontend.example.test",
        "http://frontend.example.test",  # production requires https
        "http://localhost:3000",  # the development default must be replaced explicitly
    ],
)
def test_production_rejects_wildcard_credentialed_malformed_and_plain_http_origins(origins: str) -> None:
    assert "BACKEND_CORS_ORIGINS" in _rejected(BACKEND_CORS_ORIGINS=origins)


def test_every_problem_is_reported_at_once() -> None:
    message = _rejected(
        PERSISTENCE_BACKEND="local_json", PROVIDER_CACHE_BACKEND="sqlite", SESSION_COOKIE_SECURE="false"
    )
    assert "PERSISTENCE_BACKEND" in message and "PROVIDER_CACHE_BACKEND" in message and "SESSION_COOKIE_SECURE" in message


def test_development_keeps_its_defaults_but_never_a_wildcard_or_credentialed_origin() -> None:
    development = {"APP_ENV": "development", "PERSISTENCE_BACKEND": "local_json", "PROVIDER_CACHE_BACKEND": "sqlite"}
    validate_runtime_configuration(Settings(_env_file=None, **development))  # http://localhost:3000, insecure cookie
    for origins in ("*", f"http://user:{_DB_SECRET}@localhost:3000"):
        with pytest.raises(OperationalConfigurationError) as excinfo:
            validate_runtime_configuration(Settings(_env_file=None, BACKEND_CORS_ORIGINS=origins, **development))
        assert "BACKEND_CORS_ORIGINS" in str(excinfo.value) and _DB_SECRET not in str(excinfo.value)
