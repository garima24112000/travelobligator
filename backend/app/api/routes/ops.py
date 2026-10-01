"""Operational endpoints (Section 200D): `/health`, `/ready`, `/metrics`.

  * `/health`  liveness ONLY -- answers 200 whenever the process can answer; performs NO PostgreSQL,
    Redis, provider, AI or network call. Deliberately tiny: no environment, hostname, config or
    version dump.
  * `/ready`   authoritative readiness (see `app.core.readiness`): 200 READY / 200 DEGRADED / 503 NOT_READY.
  * `/metrics` Prometheus text exposition of the low-cardinality operational metrics
    (`app.core.metrics`). Independent of `/health` and `/ready`; disabled with `METRICS_ENABLED=false`.

Exposure policy (Section 203B) -- `/health` is always public and dependency-free (it is the hosting
platform's liveness path); `/ready` and `/metrics` are operational detail and follow `_ops_access_allowed`:

  * `OPS_TOKEN` set            -> both require `Authorization: Bearer <OPS_TOKEN>` (constant-time compare);
  * `OPS_TOKEN` unset, production (`APP_ENV=production`) -> both answer 404 (not publicly exposed);
  * `OPS_TOKEN` unset, otherwise -> open, as before (local Compose / CI probes on 127.0.0.1).

A refused request gets the same 404 whether the token is missing, wrong or not configured, so the
response never says which. Neither endpoint ever returns a secret or a URL.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from app.core.config import get_settings
from app.core.metrics import registry
from app.core.operational_config import is_production
from app.core.readiness import evaluate_readiness
from app.schemas.readiness import ReadinessStatus

router = APIRouter()

_MESSAGES = {
    ReadinessStatus.READY: "The service is ready.",
    ReadinessStatus.DEGRADED: "The service is ready but a non-authoritative dependency is degraded.",
    ReadinessStatus.NOT_READY: "The service is not ready to serve authoritative requests.",
}


def _ops_access_allowed(request: Request) -> bool:
    settings = get_settings()
    if not settings.ops_token:
        return not is_production(settings)
    scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(
        presented.strip().encode("utf-8"), settings.ops_token.encode("utf-8")
    )


def _not_found() -> PlainTextResponse:
    return PlainTextResponse("Not Found\n", status_code=404)


@router.get("/health")
def health_check() -> dict:
    return {
        "success": True,
        "data": {"status": "ok", "service": get_settings().app_name},
        "message": "TravelObligator backend is running.",
        "errors": [],
    }


@router.get("/ready")
def readiness_check(request: Request) -> Response:
    if not _ops_access_allowed(request):
        return _not_found()
    result = evaluate_readiness()
    body = {
        "success": result.status != ReadinessStatus.NOT_READY,
        "data": jsonable_encoder(result),
        "message": _MESSAGES[result.status],
        "errors": [],
    }
    return JSONResponse(
        status_code=503 if result.status == ReadinessStatus.NOT_READY else 200, content=body
    )


@router.get("/metrics")
def metrics_endpoint(request: Request) -> PlainTextResponse:
    if not _ops_access_allowed(request):
        return _not_found()
    if not get_settings().metrics_enabled:
        return PlainTextResponse("metrics are disabled\n", status_code=404)
    return PlainTextResponse(
        registry.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
    )
