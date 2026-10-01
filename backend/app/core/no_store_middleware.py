"""`Cache-Control: no-store` on every backend response (Section 203B).

In the portfolio deployment the browser reaches this service through a CDN rewrite (Vercel `/api/*` ->
this backend), so a response without an explicit cache directive could be stored by an intermediary.
Every response this service produces is either session-scoped (auth, trips, jobs, feedback,
regeneration, branches, revisions) or operational (`/health`, `/ready`, `/metrics`) -- none of it is
safely shareable -- so the rule is global rather than a per-route allowlist that a new route could
forget. Static frontend assets are served by the frontend host, never by this service.

A handler that sets its own `Cache-Control` is overridden on purpose: there is no cacheable backend
response today, and making one cacheable must be a deliberate change here.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

NO_STORE = "no-store"


class NoStoreMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = NO_STORE
        return response
