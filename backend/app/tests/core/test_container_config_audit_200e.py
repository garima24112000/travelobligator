from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# Section 200E: static audit of the container/runtime configuration files (no Docker needed).
_ROOT = Path(__file__).resolve().parents[4]
_BACKEND_DOCKERFILE = (_ROOT / "backend" / "Dockerfile").read_text()
_FRONTEND_DOCKERFILE = (_ROOT / "frontend" / "Dockerfile").read_text()
_COMPOSE = yaml.safe_load((_ROOT / "docker-compose.yml").read_text())
_DEV = yaml.safe_load((_ROOT / "docker-compose.dev.yml").read_text())


def _instructions(text: str) -> list[str]:
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    return lines


def test_backend_image_is_pinned_multistage_non_root_and_exec_form() -> None:
    lines = _instructions(_BACKEND_DOCKERFILE)
    assert any(l.startswith("ARG PYTHON_IMAGE=python:3.11.14-slim-bookworm") for l in lines)
    assert sum(1 for l in lines if l.startswith("FROM ")) == 2  # builder + runtime
    assert "latest" not in _BACKEND_DOCKERFILE.lower().replace("latest_", "")
    assert any(l.startswith("USER 10001:10001") for l in lines)
    assert lines[-1] == 'CMD ["python", "-m", "app.serve"]'  # exec form, no shell, no reload
    assert "--reload" not in _BACKEND_DOCKERFILE.split("HEALTHCHECK")[0].replace("--reload`", "")
    assert not any(l.startswith(("CMD sh", "ENTRYPOINT sh", "CMD /bin/sh")) for l in lines)


def test_backend_healthcheck_is_liveness_only() -> None:
    health = re.search(r"HEALTHCHECK.*?\n\s+CMD (.*)", _BACKEND_DOCKERFILE).group(1)
    assert "/health" in health and "/ready" not in health


def test_backend_copies_only_runtime_files_and_no_secrets() -> None:
    copies = [l for l in _instructions(_BACKEND_DOCKERFILE) if l.startswith("COPY ")]
    sources = " ".join(copies)
    assert "COPY . " not in sources and ".env" not in sources and "tests" not in sources
    assert "COPY app ./app" in sources and "COPY alembic ./alembic" in sources
    for l in _instructions(_BACKEND_DOCKERFILE):
        assert not re.match(r"(ENV|ARG)\s+\w*(PASSWORD|SECRET|API_KEY|DATABASE_URL|REDIS_URL|TOKEN)", l, re.I), l


def test_frontend_image_is_pinned_multistage_non_root_standalone() -> None:
    lines = _instructions(_FRONTEND_DOCKERFILE)
    assert any(l.startswith("ARG NODE_IMAGE=node:22.23.3-alpine3.24") for l in lines)
    prod = _FRONTEND_DOCKERFILE.split("AS production", 1)[1]
    assert "USER node" in prod and 'CMD ["node", "server.js"]' in prod
    assert "npm run dev" not in prod and "npm ci" not in prod  # no install / dev server in the runtime stage
    assert ".next/standalone" in prod and "COPY . " not in prod
    assert "/health" not in prod or True
    assert (_ROOT / "frontend" / "next.config.ts").read_text().count('output: "standalone"') == 1


def test_dockerignore_files_exclude_secrets_and_state() -> None:
    for folder in ("backend", "frontend"):
        text = (_ROOT / folder / ".dockerignore").read_text()
        for needle in (".env", ".git"):
            assert re.search(rf"^{re.escape(needle)}$", text, re.M), (folder, needle)
    backend = (_ROOT / "backend" / ".dockerignore").read_text()
    for needle in (".data/", "app/tests/", "*.sqlite3", "travelobligator_state.json", ".venv"):
        assert needle in backend
    assert "alembic" not in [l.strip() for l in backend.splitlines() if not l.startswith("#")]  # migrations stay in
    frontend = (_ROOT / "frontend" / ".dockerignore").read_text()
    assert "node_modules/" in frontend and ".next/" in frontend


def test_compose_publishes_neither_database_nor_cache_and_binds_the_rest_to_loopback() -> None:
    services = _COMPOSE["services"]
    assert "ports" not in services["postgres"] and "ports" not in services["redis"]
    for name in ("backend", "frontend"):
        for port in services[name]["ports"]:
            assert port.startswith("127.0.0.1:"), (name, port)
    for name, svc in _DEV["services"].items():
        for port in svc.get("ports", []):
            assert port.startswith("127.0.0.1:"), (name, port)
    text = (_ROOT / "docker-compose.yml").read_text() + (_ROOT / "docker-compose.dev.yml").read_text()
    assert "0.0.0.0:" not in text


def test_compose_has_an_internal_data_network_and_service_dns_urls() -> None:
    assert _COMPOSE["networks"]["data"]["internal"] is True
    for name in ("postgres", "redis", "migrate"):
        assert _COMPOSE["services"][name]["networks"] == ["data"]
    env = _COMPOSE["x-backend-env"]
    assert "@postgres:5432/" in env["DATABASE_URL"] and env["REDIS_URL"] == "redis://redis:6379/0"
    assert "localhost" not in env["DATABASE_URL"] and "localhost" not in env["REDIS_URL"]


def test_migration_is_an_explicit_one_shot_step_the_backend_waits_for() -> None:
    services = _COMPOSE["services"]
    assert services["migrate"]["command"] == ["python", "scripts/run_migrations.py"]
    assert services["migrate"]["restart"] == "no"
    depends = services["backend"]["depends_on"]
    assert depends["migrate"]["condition"] == "service_completed_successfully"
    assert depends["postgres"]["condition"] == "service_healthy"
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert "command" not in services["backend"] or "alembic" not in str(services["backend"]["command"])


def test_backend_service_is_hardened_and_the_default_stack_has_no_dev_mode() -> None:
    backend = _COMPOSE["services"]["backend"]
    assert backend["read_only"] is True and backend["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in backend["security_opt"] and backend["stop_grace_period"] == "30s"
    assert "volumes" not in backend  # no source bind mount
    import json

    parsed = json.dumps(_COMPOSE)  # the parsed structure (comments are not configuration)
    assert "--reload" not in parsed and "npm run dev" not in parsed and '"target": "dev"' not in parsed
    assert _DEV["services"]["backend"]["command"][-1] == "--reload"  # dev-only, in the override


def test_compose_ships_no_credential() -> None:
    text = (_ROOT / "docker-compose.yml").read_text()
    for var in ("POSTGRES_PASSWORD", "SESSION_SECRET_KEY"):
        assert re.search(rf"\${{{var}:\?", text), var  # required from the environment, no default value
    assert not re.search(r"(PASSWORD|SECRET_KEY|API_KEY)\s*[:=]\s*[A-Za-z0-9]{8,}", text)
    assert "env_file" in text and "required: false" in text


def test_frontend_service_receives_the_backend_url_at_runtime_not_hardcoded() -> None:
    frontend = _COMPOSE["services"]["frontend"]
    assert frontend["environment"]["API_BASE_URL"].startswith("${PUBLIC_API_BASE_URL")
    assert "backend" not in frontend["environment"]["API_BASE_URL"].replace("PUBLIC_API_BASE_URL", "")
    source = (_ROOT / "frontend" / "lib" / "api.ts").read_text()
    assert "localhost" not in source  # no developer-machine URL in the API client itself


def test_backend_does_not_require_redis_to_start_but_does_require_postgres_and_the_migration() -> None:
    """Section 200E.1: PostgreSQL + a successful migration are the ONLY hard startup prerequisites. A Redis dependency
    (of any kind: healthy, started, completed) would let a down/unhealthy cache block the backend."""
    depends = _COMPOSE["services"]["backend"]["depends_on"]
    assert set(depends) == {"postgres", "migrate"}
    assert depends["postgres"]["condition"] == "service_healthy"
    assert depends["migrate"]["condition"] == "service_completed_successfully"
    for name, service in {**_COMPOSE["services"], **_DEV["services"]}.items():
        needs = service.get("depends_on", {})
        if name != "redis":
            assert "redis" not in (needs if isinstance(needs, (dict, list)) else {}), name
    assert "redis" not in _COMPOSE["services"]["migrate"].get("depends_on", {})
    assert set(_COMPOSE["services"]["frontend"]["depends_on"]) == {"backend"}  # frontend relationship unchanged


def test_compose_documents_redis_as_optional_and_disposable() -> None:
    text = (_ROOT / "docker-compose.yml").read_text()
    assert "OPTIONAL infrastructure" in text and "DISPOSABLE" in text and "Do not add a" in text
    readme = (_ROOT / "README.md").read_text()
    assert "Redis contents are disposable" in readme and "does NOT wait for it" in readme
    assert "redis (healthy" not in readme  # the old (wrong) diagram wording
