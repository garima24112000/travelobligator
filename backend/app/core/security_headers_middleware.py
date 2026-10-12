"""Security headers on every backend response (P1-B).

The browser reaches this service through a rewrite on the frontend host (`/api/*` -> this backend), and a
proxy does not reliably add headers to a response it merely forwards: the frontend's own production server
passes a proxied response through untouched. So, like `Cache-Control: no-store`
(`no_store_middleware`), these are set HERE, on the response itself, whatever stands in front:

  X-Content-Type-Options: nosniff     a JSON answer is never sniffed into something executable.
  X-Frame-Options: DENY               no response of this API may be framed.
  Referrer-Policy: no-referrer        nothing here navigates anywhere; never leak a path with a trip id.
  Content-Security-Policy             `default-src 'none'; frame-ancestors 'none'` -- ENFORCED (unlike the
                                      frontend's Report-Only page policy): an API response loads nothing,
                                      so if one were ever rendered as a document it could run nothing.

The policy is left off the two interactive documentation PAGES (`/docs`, `/redoc`), which load their
viewer from a CDN and exist only outside production; where those paths answer 404 they get the policy
like any other response. No `Strict-Transport-Security` is sent from here:
HSTS is a promise about a HOSTNAME, and that is the frontend host's to make (see
`frontend/lib/security-headers.ts`).

A handler's own value for one of these headers is overridden on purpose, exactly as for `no-store`.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

API_CONTENT_SECURITY_POLICY = "default-src 'none'; frame-ancestors 'none'"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

# The interactive API documentation (development only; 404 in production) needs its CDN-hosted viewer.
_DOCUMENTATION_PATHS = frozenset({"/docs", "/redoc"})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        serves_documentation_page = request.url.path in _DOCUMENTATION_PATHS and response.headers.get(
            "content-type", ""
        ).startswith("text/html")
        if not serves_documentation_page:
            # includes those two paths when they answer 404 (production)
            response.headers["Content-Security-Policy"] = API_CONTENT_SECURITY_POLICY
        return response
