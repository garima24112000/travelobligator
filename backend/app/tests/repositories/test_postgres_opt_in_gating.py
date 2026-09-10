from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Tests for Step 183E's Docker Compose port hardening and the
# "gated Postgres tests stay skipped by default" guarantee. None require
# Docker/a live Postgres -- the compose/`.env.example` checks are plain
# text inspection (no YAML library dependency added just for this), and
# the skip-by-default checks run the gated test files as real subprocesses
# with a clean environment (no TRAVELOB_RUN_POSTGRES_TESTS/DATABASE_URL),
# proving they skip rather than error or hang without a live database.

_REPO_ROOT = Path(__file__).resolve().parents[4]
_BACKEND_DIR = _REPO_ROOT / "backend"


def _compose_source() -> str:
    return (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def _service_block(compose_source: str, service_name: str) -> str:
    """Returns the text of one top-level service's block (from its
    `  <name>:` line up to the next top-level `  <other-name>:` line or
    end of file) -- good enough for the plain substring checks below
    without needing a YAML parser."""
    pattern = re.compile(
        rf"^  {re.escape(service_name)}:\n(.*?)(?=^  \S|\Z)", re.MULTILINE | re.DOTALL
    )
    match = pattern.search(compose_source)
    assert match is not None, f"service {service_name!r} not found in docker-compose.yml"
    return match.group(1)


def test_docker_compose_postgres_host_port_is_configurable() -> None:
    postgres_block = _service_block(_compose_source(), "postgres")
    assert "POSTGRES_HOST_PORT" in postgres_block
    # Backwards-compatible default: an operator who never sets
    # POSTGRES_HOST_PORT still gets 5432, exactly as before this step.
    assert ":-5432}:5432" in postgres_block


def test_docker_compose_postgres_has_a_healthcheck() -> None:
    postgres_block = _service_block(_compose_source(), "postgres")
    assert "healthcheck:" in postgres_block
    assert "pg_isready" in postgres_block


def test_docker_compose_backend_still_reaches_postgres_by_service_name() -> None:
    """The host-side port mapping must never change how OTHER containers
    in this compose file reach Postgres -- they always use the service
    name and the container's own internal port. `backend` gets
    DATABASE_URL from `.env` (env_file), not a compose-level override, so
    this just confirms compose itself doesn't hardcode a conflicting
    one."""
    backend_block = _service_block(_compose_source(), "backend")
    assert "DATABASE_URL" not in backend_block


def test_env_example_documents_postgres_host_port() -> None:
    content = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "POSTGRES_HOST_PORT" in content
    assert "15432" in content
    assert "localhost" in content
    assert "postgres:5432" in content


def test_env_example_persistence_backend_still_defaults_local_json() -> None:
    content = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "PERSISTENCE_BACKEND=local_json" in content


def _run_pytest_with_clean_env(test_file: str) -> subprocess.CompletedProcess:
    import os

    clean_env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("TRAVELOB_RUN_POSTGRES_TESTS", "DATABASE_URL", "PERSISTENCE_BACKEND")
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", test_file, "-q"],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=60,
        env=clean_env,
    )


def test_postgres_repository_integration_tests_skip_by_default() -> None:
    result = _run_pytest_with_clean_env(
        "app/tests/repositories/test_postgres_repositories_integration.py"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped" in result.stdout
    assert "passed" not in result.stdout


def test_postgres_api_smoke_test_skips_by_default() -> None:
    result = _run_pytest_with_clean_env("app/tests/api/test_postgres_api_smoke.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
