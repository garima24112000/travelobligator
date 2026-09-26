"""Operational endpoints (Section 200D): `/health`, `/ready`, `/metrics`.

  * `/health`  liveness ONLY -- answers 200 whenever the process can answer; performs NO PostgreSQL,
    Redis, provider, AI or network call. Deliberately tiny: no environment, hostname, config or
    version dump.
  * `/ready`   authoritative readiness (see `app.core.readiness`): 200 READY / 200 DEGRADED / 503 NOT_READY.
  * `/metrics` Prometheus text exposition of the low-cardinality operational metrics
    (`app.core.metrics`). Independent of `/health` and `/ready`; disabled with `METRICS_ENABLED=false`.

No authentication exists in this application, so the deployment must keep these operational
endpoints on a private network / behind the platform's health-check path (Section 200E+).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.config import get_settings
from app.core.metrics import registry
from app.core.readiness import evaluate_readiness
from app.schemas.readiness import ReadinessStatus

router = APIRouter()

_MESSAGES = {
    ReadinessStatus.READY: "The service is ready.",
    ReadinessStatus.DEGRADED: "The service is ready but a non-authoritative dependency is degraded.",
    ReadinessStatus.NOT_READY: "The service is not ready to serve authoritative requests.",
}


@router.get("/health")
def health_check() -> dict:
    return {
        "success": True,
        "data": {"status": "ok", "service": get_settings().app_name},
        "message": "TravelObligator backend is running.",
        "errors": [],
    }


@router.get("/ready")
def readiness_check() -> JSONResponse:
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
def metrics_endpoint() -> PlainTextResponse:
    if not get_settings().metrics_enabled:
        return PlainTextResponse("metrics are disabled\n", status_code=404)
    return PlainTextResponse(
        registry.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
    )
