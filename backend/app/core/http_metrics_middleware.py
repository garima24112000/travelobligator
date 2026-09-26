"""HTTP request metrics (Section 200D).

Counts requests and times them, labelled ONLY by method, ROUTE TEMPLATE (e.g. `/trips/{trip_id}`,
never `/trips/trip_123`) and status class. An unmatched path (404) is labelled `unmatched`, so a
scanner cannot create unbounded label values; request ids, trip ids, users and query strings are
never labels. Recording never raises and never changes the response.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.metrics import registry

_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "unmatched"


def _record(request: Request, status_code: int, started: float) -> None:
    method = request.method if request.method in _ALLOWED_METHODS else "OTHER"
    route = route_template(request)
    registry.inc(
        "travelobligator_http_requests_total",
        {"method": method, "route": route, "status_class": f"{status_code // 100}xx"},
    )
    registry.observe(
        "travelobligator_http_request_duration_seconds",
        time.perf_counter() - started,
        {"method": method, "route": route},
    )


class HttpMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _record(request, 500, started)
            raise
        _record(request, response.status_code, started)
        return response
