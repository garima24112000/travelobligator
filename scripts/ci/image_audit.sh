#!/usr/bin/env bash
# Section 203A: static + runtime-config audit of the BUILT production images (complements the Dockerfile/compose
# text audit in backend/app/tests/core/test_container_config_audit_200e.py, which cannot see a built image).
#
#   scripts/ci/image_audit.sh [backend-image] [frontend-image]
#
# Defaults: travelobligator-backend:local travelobligator-frontend:local. Optional EXPECT_REVISION asserts the OCI
# `org.opencontainers.image.revision` label on both images (set by CI). No secret is read or needed.
set -euo pipefail

BACKEND_IMAGE="${1:-travelobligator-backend:local}"
FRONTEND_IMAGE="${2:-travelobligator-frontend:local}"
NAME_PREFIX="travelob-audit-$$"

fail() { printf 'IMAGE AUDIT FAILED: %s\n' "$*" >&2; exit 1; }
expect() { [ "$2" = "$3" ] || fail "$1: expected '$3', got '$2'"; }
cleanup() { docker rm -f "${NAME_PREFIX}-a" "${NAME_PREFIX}-b" >/dev/null 2>&1 || true; }
trap cleanup EXIT

inspect() { docker inspect -f "$2" "$1"; }

# ---------------------------------------------------------------- backend
echo "== backend image: ${BACKEND_IMAGE}"
expect "backend user" "$(inspect "${BACKEND_IMAGE}" '{{.Config.User}}')" "10001:10001"
expect "backend command" "$(inspect "${BACKEND_IMAGE}" '{{json .Config.Cmd}}')" '["python","-m","app.serve"]'
expect "backend entrypoint" "$(inspect "${BACKEND_IMAGE}" '{{json .Config.Entrypoint}}')" "null"
case "$(inspect "${BACKEND_IMAGE}" '{{json .Config}}')" in
  *--reload*) fail "backend image config mentions --reload" ;;
esac

docker run --rm --network none -e PORT=9123 --entrypoint python "${BACKEND_IMAGE}" -c '
import importlib.util, os, pathlib, sys

problems = []
if os.getuid() != 10001:
    problems.append("runs as uid %d, not 10001" % os.getuid())
app = pathlib.Path("/app")
for forbidden in (".env", ".data", "app/tests", "requirements-dev.txt", ".git"):
    if (app / forbidden).exists():
        problems.append("/app/%s is present in the image" % forbidden)
for pattern in (".env*", "*.sqlite3", "travelobligator_state.json", "manual_*.py"):
    found = [str(p) for p in app.rglob(pattern)]
    if found:
        problems.append("%s present: %s" % (pattern, found[:3]))
if importlib.util.find_spec("pytest") is not None:
    problems.append("pytest is installed in the runtime image")
if os.access("/app/app", os.W_OK):
    problems.append("application code is writable by the runtime user")

from app.serve import uvicorn_options

options = uvicorn_options()
if options["port"] != 9123:
    problems.append("PORT is not honoured (got %r)" % options["port"])
if options["workers"] != 1 or options["reload"] is not False:
    problems.append("not exactly one non-reloading worker: %r" % options)

for problem in problems:
    print("  - " + problem)
sys.exit(1 if problems else 0)
' || fail "backend image contents/runtime options"
echo "backend: uid 10001, exec-form python -m app.serve, one worker, no reload, PORT honoured, no .env/tests/local state"

# ---------------------------------------------------------------- frontend
echo "== frontend image: ${FRONTEND_IMAGE}"
expect "frontend user" "$(inspect "${FRONTEND_IMAGE}" '{{.Config.User}}')" "node"
expect "frontend command" "$(inspect "${FRONTEND_IMAGE}" '{{json .Config.Cmd}}')" '["node","server.js"]'

docker run --rm --network none --entrypoint sh "${FRONTEND_IMAGE}" -c '
set -eu
[ "$(id -u)" != "0" ] || { echo "  - runs as root"; exit 1; }
[ -f /app/server.js ] || { echo "  - no standalone server.js"; exit 1; }
[ ! -e /app/package-lock.json ] || { echo "  - source tree (package-lock.json) copied into the image"; exit 1; }
if [ -n "$(find /app -name ".env*" -not -path "*/node_modules/*" | head -n 1)" ]; then echo "  - .env file in image"; exit 1; fi
# Build-machine paths must not be baked into the compiled output (the image builds under /app).
if grep -rlE "/Users/|/home/runner/" /app/server.js /app/.next 2>/dev/null | head -n 3 | grep -q .; then
  echo "  - developer/runner home path baked into the build output"; exit 1
fi
' || fail "frontend image contents"

# One image, two different runtime API URLs: each container must serve ITS OWN value (nothing baked at build time
# may win over the runtime environment).
URL_A="http://runtime-a.example.test:8001"
URL_B="https://runtime-b.example.test"
docker run -d --name "${NAME_PREFIX}-a" -e "API_BASE_URL=${URL_A}" "${FRONTEND_IMAGE}" >/dev/null
docker run -d --name "${NAME_PREFIX}-b" -e "API_BASE_URL=${URL_B}" "${FRONTEND_IMAGE}" >/dev/null

# Prints "ok" when / is 200 and carries exactly the expected runtime URL, "wrong" on a mismatch, "starting" otherwise.
probe_frontend() {
  docker exec -e "WANT=$2" -e "NOT_WANT=$3" "$1" node -e '
fetch("http://127.0.0.1:3000/").then(async (r) => {
  const html = await r.text();
  const want = JSON.stringify({ apiBaseUrl: process.env.WANT });
  console.log(r.status === 200 && html.includes(want) && !html.includes(process.env.NOT_WANT) ? "ok" : "wrong");
}).catch(() => console.log("starting"));' 2>/dev/null || echo "starting"
}
for pair in "a|${URL_A}|${URL_B}" "b|${URL_B}|${URL_A}"; do
  suffix="${pair%%|*}"; rest="${pair#*|}"; want="${rest%%|*}"; not_want="${rest#*|}"
  deadline=$(( $(date +%s) + 60 ))
  while :; do
    result="$(probe_frontend "${NAME_PREFIX}-${suffix}" "${want}" "${not_want}")"
    [ "${result}" = "ok" ] && break
    [ "${result}" = "wrong" ] && fail "frontend did not serve its runtime API_BASE_URL (${want})"
    [ "$(date +%s)" -ge "${deadline}" ] && fail "frontend container did not answer within 60s"
    sleep 2
  done
done
echo "frontend: user node, node server.js (standalone), no .env/source/home paths, API_BASE_URL is runtime-configurable"

# ---------------------------------------------------------------- provenance labels (CI only)
if [ -n "${EXPECT_REVISION:-}" ]; then
  for image in "${BACKEND_IMAGE}" "${FRONTEND_IMAGE}"; do
    expect "${image} revision label" \
      "$(inspect "${image}" '{{index .Config.Labels "org.opencontainers.image.revision"}}')" "${EXPECT_REVISION}"
  done
  echo "OCI revision label matches ${EXPECT_REVISION}"
fi

echo "== Image audit passed"
