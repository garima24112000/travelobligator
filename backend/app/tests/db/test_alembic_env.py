from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.core.config import Settings

# Tests for Step 183B/183C's alembic/env.py wiring. Never requires a real
# Postgres/Docker: `alembic current` is invoked as a real subprocess (so
# env.py's actual import-time code runs exactly as it would for a real
# operator), but against the default, docker-network-only "postgres"
# hostname -- which fails DNS resolution near-instantly outside a Docker
# network. That failure mode (an OperationalError from psycopg) is the
# proof this test wants: every Python-level import in env.py (app.core.
# config, app.db.base, app.db.models, app.db.session) succeeded, and only
# the actual network connection -- which this test never needs to
# succeed -- was attempted and failed.

_BACKEND_DIR = Path(__file__).resolve().parents[3]


def test_alembic_history_resolves_without_connecting() -> None:
    """`alembic history`/`alembic revision` never open a connection --
    only `upgrade`/`downgrade`/`current`/`stamp` do. This is the
    lowest-risk possible proof that env.py's module-level code (which
    imports app.db.base/app.db.models and calls
    normalize_database_url(get_settings().database_url)) runs cleanly."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "history"],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "create_trips_and_planning_states" in result.stdout


def test_alembic_current_fails_only_at_connection_not_at_import(
    tmp_path: Path,
) -> None:
    """`alembic current` DOES try to connect (unlike `history` above) --
    proving env.py imports cleanly even when a real command is run, while
    never requiring a real database to actually exist for this test."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current"],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "ImportError" not in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert "AttributeError" not in result.stderr
    assert "OperationalError" in result.stderr


def test_persistence_backend_default_is_still_local_json() -> None:
    """183C never changes the Step 183B config gate's default -- this
    migration/schema work is entirely opt-in and unwired."""
    settings = Settings(_env_file=None)
    assert settings.persistence_backend == "local_json"
