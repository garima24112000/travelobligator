#!/usr/bin/env python3
"""Non-destructive availability check of a deployment, from the outside. Standard library only.

    python backend/scripts/public_check.py                      # the public frontend only
    python backend/scripts/public_check.py --backend            # ... and the backend, through the frontend
    python backend/scripts/public_check.py --backend --ready    # ... and readiness (needs OPS_TOKEN + --backend-origin)

Used by hand (deployment smoke, incident checklist) and by `.github/workflows/uptime.yml`.
Procedure: docs/25_operations_runbook.md, "Monitoring".

What it does -- GET requests only; it never signs up, signs in, creates or deletes anything, and never
starts a generation:

  frontend   GET <frontend>/                    200, an HTML page of this application. Served by the frontend
                                                host alone: it does NOT wake the backend.
  backend    GET <frontend>/api/health          200 with `data.status` "ok", through the same-origin proxy. This
                                                DOES wake a sleeping free-tier backend, so it waits for a cold
                                                start (`--cold-start-seconds`) and must not be scheduled
                                                often: it is a check, not a keepalive.
             GET <frontend>/api/ready           must be 404 \\  the operational endpoints are not public
             GET <frontend>/api/metrics         must be 404 /
             GET <frontend>/api/auth/me         must be 401, uncacheable (no session: nothing is disclosed)
  ready      GET <backend-origin>/ready         with `Authorization: Bearer $OPS_TOKEN`: 200 and `ready` or
                                                `degraded`. The token is read from the environment, is sent
                                                only to `--backend-origin`, and is never printed.

Exit code: 0 every check passed, 1 at least one failed, 2 bad usage. Output: one line per check, with a
status code and a duration -- never a response body, a header value, a cookie or the token.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_FRONTEND = "https://travelobligator.vercel.app"
USER_AGENT = "travelobligator-public-check"
# Present in the served page whoever is (not) signed in: the product name in the document title.
PAGE_MARKER = "TravelObligator"


@dataclass
class Response:
    status: int  # 0 = no HTTP response at all (DNS, TLS, refused, timed out)
    headers: dict[str, str]
    body: bytes
    seconds: float
    error: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *arguments, **keywords):  # a redirect is reported, not followed
        return None


def fetch(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> Response:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})})
    opener = urllib.request.build_opener(_NoRedirect)
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as reply:
            return Response(reply.status, {key.lower(): value for key, value in reply.headers.items()}, reply.read(200_000), time.monotonic() - started)
    except urllib.error.HTTPError as error:
        return Response(error.code, {key.lower(): value for key, value in error.headers.items()}, error.read(200_000), time.monotonic() - started)
    except Exception as error:  # URLError, timeout, TLS, connection reset
        return Response(0, {}, b"", time.monotonic() - started, type(error).__name__)


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.lines: list[str] = []

    def record(self, name: str, passed: bool, detail: str) -> bool:
        self.lines.append(f"{'ok  ' if passed else 'FAIL'} {name}: {detail}")
        print(self.lines[-1], flush=True)
        if not passed:
            self.failures.append(name)
        return passed


def _describe(response: Response) -> str:
    if response.status == 0:
        return f"no response ({response.error}) after {response.seconds:.1f} s"
    return f"HTTP {response.status} in {response.seconds:.1f} s"


def check_frontend(checker: Checker, frontend: str, *, timeout: float, expect_security_headers: bool) -> None:
    response = fetch(f"{frontend}/", timeout=timeout)
    page_ok = response.status == 200 and "text/html" in response.headers.get("content-type", "") and PAGE_MARKER.encode() in response.body
    checker.record("frontend page", page_ok, _describe(response) + ("" if page_ok or response.status != 200 else "; not this application's page"))
    if expect_security_headers and response.status == 200:
        present = (
            response.headers.get("x-content-type-options") == "nosniff"
            and response.headers.get("x-frame-options") == "DENY"
            and "content-security-policy-report-only" in response.headers
            and "content-security-policy" not in response.headers  # still Report-Only, not silently enforcing
        )
        checker.record("frontend security headers", present, "present, policy is Report-Only" if present else "missing or different from the released configuration")


def check_backend(checker: Checker, frontend: str, *, timeout: float, cold_start_seconds: float) -> None:
    # A sleeping free-tier backend answers the first request slowly or with a gateway error while it starts.
    deadline = time.monotonic() + cold_start_seconds
    attempts = 0
    while True:
        attempts += 1
        response = fetch(f"{frontend}/api/health", timeout=timeout)
        healthy = response.status == 200 and _status(response) == "ok"
        if healthy or time.monotonic() >= deadline:
            break
        time.sleep(min(10.0, max(0.0, deadline - time.monotonic())))
    checker.record("backend liveness through the frontend", healthy, _describe(response) + (f" (attempt {attempts}; a cold start was waited for)" if attempts > 1 else ""))
    if not healthy:
        return  # the checks below would only repeat the same failure
    for path in ("ready", "metrics"):
        response = fetch(f"{frontend}/api/{path}", timeout=timeout)
        checker.record(f"/api/{path} is not public", response.status == 404, _describe(response))
    response = fetch(f"{frontend}/api/auth/me", timeout=timeout)
    private = response.status == 401 and response.headers.get("cache-control") == "no-store"
    checker.record("an unauthenticated API request is refused and uncacheable", private, _describe(response))


def check_ready(checker: Checker, backend_origin: str, token: str, *, timeout: float) -> None:
    response = fetch(f"{backend_origin}/ready", timeout=timeout, headers={"Authorization": f"Bearer {token}"})
    state = _status(response)
    checker.record(
        "backend readiness (database reachable, schema at the code's head)",
        response.status == 200 and state in {"ready", "degraded"},
        _describe(response) + (f"; status {state}" if state in {"ready", "degraded", "not_ready"} else ""),
    )
    if response.status == 200 and state == "degraded":
        print("note backend readiness: degraded = the cache is unavailable; requests still work, provider calls run uncached", flush=True)


def _status(response: Response) -> str:
    """`data.status` of the backend's response envelope, or "" when the body is anything else."""
    try:
        value = json.loads(response.body.decode("utf-8", "replace"))["data"]["status"]
        return value if isinstance(value, str) else ""
    except Exception:
        return ""


def _origin(value: str, name: str) -> str:
    value = value.strip().rstrip("/")
    if not (value.startswith("https://") or value.startswith("http://127.0.0.1") or value.startswith("http://localhost")):
        raise SystemExit(f"{name} must be an https:// origin (plain http is accepted for 127.0.0.1 only).")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Non-destructive availability check of a deployment (GET requests only).")
    parser.add_argument("--frontend", default=DEFAULT_FRONTEND, help=f"frontend origin (default: {DEFAULT_FRONTEND})")
    parser.add_argument("--backend", action="store_true", help="also check the backend through the frontend's /api proxy (wakes a sleeping backend)")
    parser.add_argument("--ready", action="store_true", help="also check /ready on --backend-origin with OPS_TOKEN from the environment")
    parser.add_argument("--backend-origin", help="the backend's own origin, used only for --ready")
    parser.add_argument("--expect-security-headers", action="store_true", help="require the released security headers on the page")
    parser.add_argument("--timeout", type=float, default=20.0, help="seconds per request (default: 20)")
    parser.add_argument("--cold-start-seconds", type=float, default=120.0, help="how long to wait for a sleeping backend (default: 120)")
    arguments = parser.parse_args(argv)

    try:
        frontend = _origin(arguments.frontend, "--frontend")
        token = os.environ.get("OPS_TOKEN", "")
        backend_origin = _origin(arguments.backend_origin, "--backend-origin") if arguments.backend_origin else ""
        if arguments.ready and not (token and backend_origin):
            raise SystemExit("--ready needs OPS_TOKEN in the environment and --backend-origin.")
    except SystemExit as refusal:
        print(str(refusal), file=sys.stderr)
        return 2

    checker = Checker()
    check_frontend(checker, frontend, timeout=arguments.timeout, expect_security_headers=arguments.expect_security_headers)
    if arguments.backend:
        check_backend(checker, frontend, timeout=arguments.timeout, cold_start_seconds=arguments.cold_start_seconds)
    if arguments.ready:
        check_ready(checker, backend_origin, token, timeout=arguments.timeout)

    if checker.failures:
        print(f"{len(checker.failures)} check(s) FAILED: {', '.join(checker.failures)}", flush=True)
        return 1
    print("All checks passed.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
