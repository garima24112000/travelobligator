#!/usr/bin/env bash
# Section 203A: production-style container smoke, run identically by CI ("Containers / Build & Smoke") and by a
# developer. It drives the REAL docker-compose.yml (no override file, no source bind mount, no --reload) with
# throwaway, randomly generated settings written OUTSIDE the repository -- the developer's ./.env is never read.
#
#   scripts/ci/container_smoke.sh            # uses already-built travelobligator-{backend,frontend}:local
#   SMOKE_BUILD=1 scripts/ci/container_smoke.sh   # build both images through compose first
#
# Optional: SMOKE_PROJECT (compose project name), BACKEND_HOST_PORT / FRONTEND_HOST_PORT (loopback ports).
#
# Contracts proven (each is a regression gate, not a convenience):
#   1. Redis ABSENT at startup: migration succeeds, backend starts, /health 200, /ready 200 "degraded".
#   2. Redis appears: /ready becomes "ready" in the SAME backend container (id, start time and restart count
#      unchanged) -- recovery without a restart. Frontend serves / with the runtime API URL; CORS allows it.
#   3. Deterministic application path with no external provider: signup, session, login, trip create.
#   4. PostgreSQL is mandatory: Postgres down -> /health 200 but /ready 503 "not_ready"; a backend pointed at an
#      empty, un-migrated database never reports ready.
# No generation is run: that would make public travel-provider APIs a CI dependency.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT="${SMOKE_PROJECT:-travelob-smoke}"
BACKEND_PORT="${BACKEND_HOST_PORT:-18000}"
FRONTEND_PORT="${FRONTEND_HOST_PORT:-13000}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
# What the BROWSER would use (compose default shape: localhost, never an internal service name).
PUBLIC_API_URL="http://localhost:${BACKEND_PORT}"
FRONTEND_ORIGIN="http://localhost:${FRONTEND_PORT}"

WORK="$(mktemp -d)"
ENV_FILE="${WORK}/stack.env"
PG_USER="travelob_smoke"
PG_DB="travelob_smoke"
PG_PASSWORD="$(openssl rand -hex 24)"

: > "${WORK}/app.env"
{
  echo "POSTGRES_USER=${PG_USER}"
  echo "POSTGRES_DB=${PG_DB}"
  echo "POSTGRES_PASSWORD=${PG_PASSWORD}"
  echo "SESSION_SECRET_KEY=$(openssl rand -hex 32)"
  echo "APP_ENV_FILE=${WORK}/app.env"
  echo "BACKEND_HOST_PORT=${BACKEND_PORT}"
  echo "FRONTEND_HOST_PORT=${FRONTEND_PORT}"
  echo "PUBLIC_API_BASE_URL=${PUBLIC_API_URL}"
  echo "BACKEND_CORS_ORIGINS=${FRONTEND_ORIGIN}"
} > "${ENV_FILE}"

dc() { docker compose --env-file "${ENV_FILE}" -p "${PROJECT}" -f "${ROOT}/docker-compose.yml" "$@"; }
step() { printf '\n== %s\n' "$*"; }
fail() { printf 'SMOKE FAILED: %s\n' "$*" >&2; exit 1; }

cleanup() {
  status=$?
  if [ "${status}" -ne 0 ]; then
    printf '\n---- container state ----\n'
    dc ps -a || true
    printf '\n---- container logs (last 150 lines per service) ----\n'
    dc logs --no-color --tail 150 || true
  fi
  docker rm -f "${PROJECT}-unmigrated" >/dev/null 2>&1 || true
  dc down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "${WORK}"
  exit "${status}"
}
trap cleanup EXIT

http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@" || true; }

# GET <url>; prints "<http code> <data.status>" of the standard envelope.
envelope_status() {
  code="$(curl -s -o "${WORK}/body.json" -w '%{http_code}' --max-time 10 "$1" || true)"
  state="$(python3 -c 'import json,sys
try:
    print(json.load(open(sys.argv[1]))["data"]["status"])
except Exception:
    print("unparseable")' "${WORK}/body.json" 2>/dev/null || echo unparseable)"
  echo "${code} ${state}"
}

# wait_for "<expected output>" <seconds> <command...>: bounded polling, never a blind sleep.
wait_for() {
  expected="$1"; seconds="$2"; shift 2
  deadline=$(( $(date +%s) + seconds ))
  while :; do
    actual="$("$@")"
    [ "${actual}" = "${expected}" ] && return 0
    if [ "$(date +%s)" -ge "${deadline}" ]; then
      fail "expected '${expected}' from '$*' within ${seconds}s, last saw '${actual}'"
    fi
    sleep 2
  done
}

backend_identity() {
  docker inspect -f '{{.Id}} {{.State.StartedAt}} {{.RestartCount}}' "$(dc ps -q backend)"
}

if [ "${SMOKE_BUILD:-0}" = "1" ]; then
  step "Building production images through compose"
  dc build migrate frontend
fi

# ---------------------------------------------------------------- 1. Redis absent at startup
step "1. Start postgres -> migrate -> backend with NO redis container"
dc up -d --no-build postgres migrate backend
migrate_exit="$(docker inspect -f '{{.State.ExitCode}}' "$(dc ps -aq migrate)")"
[ "${migrate_exit}" = "0" ] || fail "migration container exited ${migrate_exit}"
[ -z "$(dc ps -q redis)" ] || fail "redis must not be running in phase 1"
wait_for "200" 90 http_code "${BACKEND_URL}/health"
wait_for "200 degraded" 30 envelope_status "${BACKEND_URL}/ready"
echo "migration ok; /health 200; /ready 200 degraded (Redis absent)"

# ---------------------------------------------------------------- 2. Redis recovery, same backend container
step "2. Start redis; /ready must recover without a backend restart"
identity_before="$(backend_identity)"
dc up -d --no-build --no-deps redis
wait_for "200 ready" 90 envelope_status "${BACKEND_URL}/ready"
identity_after="$(backend_identity)"
[ "${identity_before}" = "${identity_after}" ] \
  || fail "backend container changed while Redis recovered (before: ${identity_before}; after: ${identity_after})"
echo "/ready 200 ready; backend container id/start time/restart count unchanged: ${identity_after%% *}"

step "2b. Start frontend (waits for a healthy backend)"
dc up -d --no-build frontend
[ "${identity_before}" = "$(backend_identity)" ] || fail "starting the frontend recreated or restarted the backend"
wait_for "200" 90 http_code "${FRONTEND_URL}/"
curl -s --max-time 10 "${FRONTEND_URL}/" > "${WORK}/index.html"
grep -q "\"apiBaseUrl\":\"${PUBLIC_API_URL}\"" "${WORK}/index.html" \
  || fail "frontend HTML does not carry the runtime API_BASE_URL"
allowed="$(curl -s -o /dev/null -D - --max-time 10 -X OPTIONS "${BACKEND_URL}/auth/login" \
  -H "Origin: ${FRONTEND_ORIGIN}" -H 'Access-Control-Request-Method: POST' | tr -d '\r' \
  | awk 'tolower($1)=="access-control-allow-origin:" {print $2}')"
[ "${allowed}" = "${FRONTEND_ORIGIN}" ] || fail "backend CORS does not allow the frontend origin"
denied="$(curl -s -o /dev/null -D - --max-time 10 -X OPTIONS "${BACKEND_URL}/auth/login" \
  -H 'Origin: http://not-the-frontend.invalid' -H 'Access-Control-Request-Method: POST' | tr -d '\r' \
  | awk 'tolower($1)=="access-control-allow-origin:" {print $2}')"
[ -z "${denied}" ] || fail "backend CORS allowed an unconfigured origin"
echo "frontend / 200 with runtime API URL; CORS allows only the frontend origin"

# ---------------------------------------------------------------- 3. deterministic application path
step "3. Signup, session, login, trip create (no external provider)"
JAR="${WORK}/cookies.txt"
credentials="{\"email\":\"smoke-$(openssl rand -hex 6)@example.com\",\"password\":\"$(openssl rand -hex 12)\"}"
code="$(http_code -c "${JAR}" -H 'Content-Type: application/json' -d "${credentials}" "${BACKEND_URL}/auth/signup")"
[ "${code}" = "201" ] || fail "signup returned ${code}"
code="$(http_code -b "${JAR}" "${BACKEND_URL}/auth/me")"
[ "${code}" = "200" ] || fail "/auth/me returned ${code}"
code="$(http_code -c "${JAR}" -H 'Content-Type: application/json' -d "${credentials}" "${BACKEND_URL}/auth/login")"
[ "${code}" = "200" ] || fail "login returned ${code}"
code="$(http_code "${BACKEND_URL}/auth/me")"
[ "${code}" = "401" ] || fail "/auth/me without a session returned ${code}, expected 401"
trip='{"primary_destination":"Lisbon","start_date":"2030-05-01","end_date":"2030-05-04","travelers_count":2,"travel_group_type":"couple"}'
code="$(http_code -b "${JAR}" -H 'Content-Type: application/json' -d "${trip}" "${BACKEND_URL}/trips")"
[ "${code}" = "201" ] || fail "trip create returned ${code}"
echo "signup 201; /auth/me 200 (401 without session); login 200; trip create 201 (persisted in PostgreSQL)"

# ---------------------------------------------------------------- 4. PostgreSQL is mandatory
step "4a. A backend on an empty, un-migrated database must never report ready"
dc exec -T postgres psql -U "${PG_USER}" -d "${PG_DB}" -q -c 'CREATE DATABASE smoke_unmigrated' >/dev/null
dc run -d --no-deps --name "${PROJECT}-unmigrated" \
  -e "DATABASE_URL=postgresql://${PG_USER}:${PG_PASSWORD}@postgres:5432/smoke_unmigrated" backend >/dev/null
probe_unmigrated() {
  if [ "$(docker inspect -f '{{.State.Running}}' "${PROJECT}-unmigrated")" != "true" ]; then
    echo "exited:$(docker inspect -f '{{.State.ExitCode}}' "${PROJECT}-unmigrated")"
    return 0
  fi
  docker exec "${PROJECT}-unmigrated" python -c '
import urllib.request, urllib.error
try:
    print("http:%d" % urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=5).status)
except urllib.error.HTTPError as exc:
    print("http:%d" % exc.code)
except Exception:
    print("starting")' 2>/dev/null || echo "starting"
}
deadline=$(( $(date +%s) + 60 ))
while :; do
  unmigrated="$(probe_unmigrated)"
  [ "${unmigrated}" != "starting" ] && break
  [ "$(date +%s)" -ge "${deadline}" ] && fail "un-migrated backend neither exited nor answered /ready within 60s"
  sleep 2
done
case "${unmigrated}" in
  http:200) fail "a backend on an un-migrated database reported /ready 200" ;;
  exited:0) fail "a backend on an un-migrated database exited 0" ;;
esac
echo "un-migrated database -> ${unmigrated} (never ready)"
docker rm -f "${PROJECT}-unmigrated" >/dev/null

step "4b. Stop postgres: liveness stays up, readiness must go 503 not_ready"
dc stop postgres >/dev/null
wait_for "503 not_ready" 60 envelope_status "${BACKEND_URL}/ready"
wait_for "200" 10 http_code "${BACKEND_URL}/health"
echo "/health 200; /ready 503 not_ready (PostgreSQL down)"

step "Container smoke passed"
