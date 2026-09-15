#!/usr/bin/env python3
"""Manual, operator-invoked Alembic migration runner (Step 188F,
docs/14_backend_architecture.md section 131).

This script exists purely so an operator who has explicitly opted into
`PERSISTENCE_BACKEND=postgres` (still NOT the default -- see
`.env.example`/`docs/14_backend_architecture.md` section 102) has one
clear, documented command to bring that Postgres database's schema up
to date before starting the backend against it: `alembic upgrade
head`, the exact same operation README's own opt-in Postgres
walkthrough has always documented, just wrapped in one script instead
of typed by hand.

**This script is never invoked automatically.** It is not called by
`app.main`, `backend/Dockerfile`'s `CMD`/`HEALTHCHECK`, or
`docker-compose.yml`'s `command:` for any service -- running it is
always a deliberate, manual operator action, run by hand (`python
backend/scripts/run_migrations.py` from the repo root, or
`python scripts/run_migrations.py` from `backend/`) exactly when the
operator decides to run it, never on app startup.

**Never re-parses `DATABASE_URL` itself.** This script only points
Alembic at this project's own, real `alembic.ini`/`alembic/env.py`
(unchanged by this step) -- the same files `alembic upgrade head` run
directly from `backend/` would use. `alembic/env.py` is what reads
`Settings.database_url` (via `get_settings()`/`normalize_database_url`)
at the moment a real Alembic command actually runs; this script
duplicates none of that logic.

**Safety contract, enforced by what this script does NOT do:**
  * Only ever runs `alembic upgrade head` -- never `downgrade`, never
    any other revision target.
  * Never calls `Base.metadata.create_all(...)` (SQLAlchemy's own
    schema-creation shortcut) -- the schema is created exclusively via
    the real Alembic migrations under `backend/alembic/versions/`.
  * Never creates a database, role, or user -- Postgres itself (and
    its credentials) must already exist; see this project's own
    `docker-compose.yml`/`.env.example` for how the local opt-in
    Postgres container's database/user are provisioned.
  * Never prints `DATABASE_URL`, any other `Settings` field, or any
    raw environment variable -- only a small number of fixed,
    hardcoded status strings ("Running Alembic migrations...",
    "Migrations applied successfully.", a generic failure line) ever
    reach stdout/stderr. A real exception (which could, in principle,
    echo connection details depending on the underlying driver) is
    never printed verbatim -- only Alembic's own built-in revision
    logging (via `alembic.ini`'s `[loggers]` config, unchanged by this
    step) may print revision ids/messages, never a connection string.
  * Has no side effects on import -- `python -c "import
    scripts.run_migrations"` (or importing this module from a test)
    never runs a migration; only calling `main()` (guarded by
    `if __name__ == "__main__":` below) does.
  * Adds no new dependency -- `alembic` is already a pinned
    `requirements.txt` dependency (Step 183B), used here via its own
    public Python API (`alembic.config.Config`/`alembic.command`)
    rather than a `subprocess` call to the `alembic` console script,
    so this works identically inside the backend Docker image (which
    never installs `requirements-dev.txt`) and on a developer's host.

Run from the repo root:
    python backend/scripts/run_migrations.py

Or from `backend/` directly:
    python scripts/run_migrations.py

Or, against a real opt-in Postgres (matching README's own walkthrough):
    DATABASE_URL=postgresql://travelobligator_user:change_me@localhost:15432/travelobligator \\
    python backend/scripts/run_migrations.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

# backend/scripts/run_migrations.py -> parents[1] is backend/, the same
# directory alembic.ini lives in regardless of the current working
# directory this script is invoked from, and regardless of whether it's
# running from a real checkout or from inside the backend Docker image
# (WORKDIR /app there mirrors backend/ exactly -- see backend/Dockerfile).
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_ALEMBIC_INI_PATH = _BACKEND_ROOT / "alembic.ini"


def _build_alembic_config() -> Config:
    """Points Alembic at this project's own `alembic.ini` -- equivalent
    to `alembic -c backend/alembic.ini upgrade head` on the CLI. Reads
    no settings/env itself; `alembic/env.py` (loaded by Alembic once a
    real command runs below) is the one place `Settings.database_url`
    is read, exactly as it already is for a manually-typed `alembic
    upgrade head`.
    """
    return Config(str(_ALEMBIC_INI_PATH))


def run_upgrade_head() -> None:
    """Runs the real `alembic upgrade head` migration path via
    Alembic's own Python API. Never downgrades, never calls
    `Base.metadata.create_all`, never creates a database/user/role.
    Raises on failure (a connection error, a migration script error,
    etc.) -- `main()` below is the one place that's caught and turned
    into a safe, generic message and a non-zero exit code.
    """
    config = _build_alembic_config()
    command.upgrade(config, "head")


def main() -> int:
    print("[run_migrations] Running Alembic migrations (upgrade -> head)...")
    try:
        run_upgrade_head()
    except Exception:
        # Deliberately never prints str(exc)/a traceback -- a connection
        # or config error could, depending on the underlying driver,
        # embed the DSN/host/credentials in its own message. Only this
        # fixed, generic line ever reaches stdout/stderr for a failure.
        print("[run_migrations] Migration failed. See operator's own Alembic/Postgres logs.")
        return 1

    print("[run_migrations] Migrations applied successfully (now at head).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
