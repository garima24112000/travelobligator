from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.core.config import Settings

# Section 203B: static audit of the deployment artifacts (render.yaml, the frontend rewrite, the deployment
# document). No network, no remote service, no secret.
_ROOT = Path(__file__).resolve().parents[4]
_RENDER_TEXT = (_ROOT / "render.yaml").read_text()
_RENDER = yaml.safe_load(_RENDER_TEXT)
_NEXT_CONFIG = (_ROOT / "frontend" / "next.config.ts").read_text()
_DOC = (_ROOT / "docs" / "22_free_tier_deployment.md").read_text()

_SECRET_KEYS = {"DATABASE_URL", "REDIS_URL", "GROQ_API_KEY", "SESSION_SECRET_KEY", "OPS_TOKEN"}
# scheme://user:password@host -- a URL carrying credentials
_CREDENTIAL_URL = re.compile(r"[a-z][a-z0-9+.\-]*://[^\s/@<>`]+:[^\s/@<>`]+@", re.I)
_HOSTED_URL = re.compile(r"https?://[a-z0-9.-]+\.(onrender\.com|vercel\.app|neon\.tech|upstash\.io)", re.I)


def _env_vars() -> dict[str, dict]:
    (service,) = _RENDER["services"]
    return {entry["key"]: entry for entry in service["envVars"]}


def test_render_blueprint_is_one_free_docker_web_service_and_nothing_else() -> None:
    assert set(_RENDER) == {"services"}  # no databases, no keyvalue/redis, no envVarGroups
    (service,) = _RENDER["services"]
    assert service["type"] == "web" and service["runtime"] == "docker" and service["plan"] == "free"
    assert service["dockerfilePath"] == "./backend/Dockerfile" and service["dockerContext"] == "./backend"
    assert service["healthCheckPath"] == "/health"
    assert service["autoDeploy"] is False  # migrations first, deploy second
    for paid_or_stateful in ("preDeployCommand", "disk", "numInstances", "scaling", "dockerCommand", "startCommand"):
        assert paid_or_stateful not in service, paid_or_stateful


def test_render_blueprint_holds_no_secret_value() -> None:
    env = _env_vars()
    assert _SECRET_KEYS <= set(env)
    for key in _SECRET_KEYS:
        assert "value" not in env[key], key
        assert env[key].get("sync") is False or env[key].get("generateValue") is True, key
    assert env["SESSION_SECRET_KEY"] == {"key": "SESSION_SECRET_KEY", "generateValue": True}
    assert not _CREDENTIAL_URL.search(_RENDER_TEXT) and not _HOSTED_URL.search(_RENDER_TEXT)


def test_render_blueprint_selects_the_production_contract() -> None:
    env = {key: entry.get("value") for key, entry in _env_vars().items()}
    assert env["APP_ENV"] == "production" and env["APP_DEBUG"] == "false"
    assert env["PERSISTENCE_BACKEND"] == "postgres" and env["PROVIDER_CACHE_BACKEND"] == "redis"
    assert env["SESSION_COOKIE_SECURE"] == "true" and env["SESSION_COOKIE_HTTPONLY"] == "true"
    assert env["SESSION_COOKIE_SAMESITE"] == "lax"
    assert env["METRICS_ENABLED"] == "false"
    assert "PORT" not in env and "HOST" not in env  # PORT is injected by the platform; the image binds 0.0.0.0
    assert "LOCAL_STORAGE_PATH" not in env and "PROVIDER_CACHE_PATH" not in env  # no local-filesystem state


def test_every_blueprint_variable_is_a_real_setting() -> None:
    aliases = {field.alias for field in Settings.model_fields.values()}
    assert set(_env_vars()) <= aliases


def test_frontend_rewrite_is_configured_from_the_environment_only() -> None:
    assert "process.env.BACKEND_ORIGIN" in _NEXT_CONFIG
    assert "NEXT_PUBLIC_BACKEND_ORIGIN" not in _NEXT_CONFIG  # never compiled into browser JavaScript
    assert "${API_PROXY_PREFIX}/:path*" in _NEXT_CONFIG and "${origin}/:path*" in _NEXT_CONFIG
    assert '{ key: "Cache-Control", value: "no-store" }' in _NEXT_CONFIG
    assert not _HOSTED_URL.search(_NEXT_CONFIG)
    assert not (_ROOT / "vercel.json").exists() and not (_ROOT / "frontend" / "vercel.json").exists()


def test_deployment_document_uses_placeholders_and_real_variable_names() -> None:
    for placeholder in (
        "<NEON_DATABASE_URL>",
        "<UPSTASH_REDIS_URL>",
        "<RENDER_BACKEND_ORIGIN>",
        "<VERCEL_FRONTEND_ORIGIN>",
    ):
        assert placeholder in _DOC
    assert not _CREDENTIAL_URL.search(_DOC) and not _HOSTED_URL.search(_DOC)
    aliases = {field.alias for field in Settings.model_fields.values()}
    frontend_only = {"BACKEND_ORIGIN", "API_BASE_URL", "NEXT_PUBLIC_API_BASE_URL"}
    manifest = _DOC.split("## 9. Variable manifest", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)`", manifest, re.M))
    assert {"DATABASE_URL", "REDIS_URL", "GROQ_API_KEY", "SESSION_SECRET_KEY", "OPS_TOKEN"} <= listed
    assert listed - frontend_only <= aliases, sorted(listed - frontend_only - aliases)


# -- managed-service URL shapes (offline: nothing connects) ---------------------------------------------------------


def test_a_managed_postgres_tls_url_keeps_its_tls_parameters_and_the_bounded_pool() -> None:
    from sqlalchemy import create_engine

    from app.core.persistence import require_database_url
    from app.db.session import engine_options, normalize_database_url

    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://owner:placeholder@db.example.test/app?sslmode=require&channel_binding=require",
        DB_POOL_SIZE=3,
        DB_MAX_OVERFLOW=2,
    )
    options = engine_options(settings)
    engine = create_engine(normalize_database_url(require_database_url(settings)), **options)
    try:
        assert engine.dialect.driver == "psycopg"
        assert dict(engine.url.query) == {"sslmode": "require", "channel_binding": "require"}
        assert options["pool_pre_ping"] is True and options["pool_size"] == 3 and options["max_overflow"] == 2
        assert options["connect_args"]["connect_timeout"] == settings.db_connect_timeout_seconds
        assert "statement_timeout" in options["connect_args"]["options"]
        assert "lock_timeout" in options["connect_args"]["options"]
    finally:
        engine.dispose()


def test_alembic_accepts_a_percent_encoded_database_password() -> None:
    # A subprocess, like test_alembic_env.py: alembic/env.py reconfigures logging when it runs. Offline mode
    # (`--sql`) executes env.py -- including its URL handling -- and only PRINTS the DDL; nothing connects.
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://owner:pass%40word@db.example.test/app?sslmode=require",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=_ROOT / "backend",
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-400:]
    assert "CREATE TABLE trips" in result.stdout
    assert "pass%40word" not in result.stdout + result.stderr


def test_a_managed_redis_tls_url_is_accepted_and_verifies_certificate_and_hostname() -> None:
    import ssl

    import redis

    from app.storage.redis_provider_cache_store import redis_client_options, validate_redis_url

    settings = Settings(_env_file=None)
    url = validate_redis_url("rediss://default:placeholder@cache.example.test:6379")
    client = redis.Redis.from_url(url, **redis_client_options(url, settings))  # lazy: no connection is opened
    try:
        connection = client.connection_pool.make_connection()
        assert type(connection).__name__ == "SSLConnection"
        assert connection.cert_reqs == ssl.CERT_REQUIRED and connection.check_hostname is True
        assert connection.socket_timeout == settings.redis_socket_timeout_seconds
    finally:
        client.close()
    plain = "redis://redis:6379/0"
    assert "ssl_check_hostname" not in redis_client_options(plain, settings)
    redis.Redis.from_url(plain, **redis_client_options(plain, settings)).close()
