# Local stand-in for the GitHub-hosted `ubuntu-24.04` runner of .github/workflows/ci.yml, used ONLY by
# scripts/ci/local_ci.py (docs/38_local_verification.md). It is never deployed and never pushed.
#
# What CI's runner gives the jobs, and what this image gives instead:
#   Python 3.11 (actions/setup-python)   -> python:3.11 (Debian trixie: GnuPG 2.4, as Ubuntu 24.04)
#   Node 22 (actions/setup-node)          -> node:22's own binaries, copied in
#   pg_dump/pg_restore >= 16              -> postgresql-client-16 from the PostgreSQL project's signed archive
#   gpg, git, curl, openssl               -> the Debian packages
#   a non-root user ("runner")            -> uid 1001 "runner" (tests that check file permissions behave as on CI)
#   Chromium's system libraries           -> installed for the Playwright version the commit pins (build arg)
ARG PYTHON_IMAGE=python:3.11-trixie
ARG NODE_IMAGE=node:22-trixie

FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE}
ARG PLAYWRIGHT_VERSION
ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && node --version && npm --version

RUN set -eux; \
    apt-get update -qq; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg git openssl; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update -qq; \
    apt-get install -y --no-install-recommends postgresql-client-16; \
    ln -sf /usr/lib/postgresql/16/bin/pg_dump /usr/local/bin/pg_dump; \
    ln -sf /usr/lib/postgresql/16/bin/pg_restore /usr/local/bin/pg_restore; \
    ln -sf /usr/lib/postgresql/16/bin/psql /usr/local/bin/psql; \
    test -n "${PLAYWRIGHT_VERSION}"; \
    npx -y "playwright@${PLAYWRIGHT_VERSION}" install-deps chromium; \
    rm -rf /var/lib/apt/lists/* /root/.npm

RUN useradd --create-home --uid 1001 runner \
 && install -d -o runner -g runner /work /results
USER runner
ENV VIRTUAL_ENV=/home/runner/venv \
    PATH=/home/runner/venv/bin:/usr/local/bin:/usr/bin:/bin
RUN python -m venv /home/runner/venv && python -m pip install -q --upgrade pip
WORKDIR /work
