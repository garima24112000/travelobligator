# Section 200E: production-style backend image.
#
#   builder  -- installs the pinned Python dependencies into a virtualenv (wheels only: psycopg[binary],
#               bcrypt, ... so no compiler is needed) -- cached independently of the application source.
#   runtime  -- the same slim base + that virtualenv + ONLY the application code needed at runtime
#               (app/, alembic/, alembic.ini, scripts/run_migrations.py). No tests, no .git, no .env, no
#               local state, no package-manager caches, no build tools.
#
# Python 3.11.14 is pinned to the interpreter the whole test suite runs under locally. (CI and the previous
# image used 3.13; aligning CI is Section 203A -- `ARG PYTHON_IMAGE` lets a build target another version.)
#
# The process model is ONE Uvicorn worker per container, started by `python -m app.serve` in exec form
# (no shell, so SIGTERM from `docker stop` reaches Uvicorn directly). Scale by running more containers.
# The database schema is NEVER migrated by this container: run the one-shot migration step
# (`python scripts/run_migrations.py`, see docker-compose.yml) before starting it; the app only VERIFIES
# the schema head at startup and reports `not_ready` on /ready if it is behind.
#
# No credential is baked in: every secret/URL is supplied at RUN time by the environment.

ARG PYTHON_IMAGE=python:3.11.14-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PORT=8000

# Dedicated unprivileged user (no login shell, no home directory).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
# root-owned, world-readable, not writable by the runtime user: the application cannot modify its own code.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
COPY scripts/run_migrations.py ./scripts/run_migrations.py

USER 10001:10001
EXPOSE 8000

# LIVENESS ONLY: /health touches no database, cache or provider, so a Postgres/Redis outage never causes the
# platform to kill (and restart-loop) a live process. Traffic readiness is /ready (used by the orchestrator /
# load balancer, not by this healthcheck). Python's stdlib is used because the slim image has no curl/wget.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=3)"]

CMD ["python", "-m", "app.serve"]
