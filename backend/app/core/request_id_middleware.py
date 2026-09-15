"""Request-scoped correlation ID middleware (Step 187C,
docs/14_backend_architecture.md section 122).

Wired into `app.main` (added via `app.add_middleware(...)` *after*
`CORSMiddleware`, so it sits inside CORS but outside Starlette's own
`ExceptionMiddleware` -- see this module's own placement note below):
on every HTTP request, reads an incoming `X-Request-Id` header, honors
it only if `app.core.request_context.is_safe_request_id` accepts it
(otherwise generates a fresh one), makes that id the current request's
`app.core.request_context` value for the duration of the route
(including any registered `AppError`/`RequestValidationError` exception
handler -- both run *inside* this middleware, not outside it), and
echoes the same id back as the response's `X-Request-Id` header.

This never reads or forwards cookies, the `Authorization` header, or
the request body -- only the one `X-Request-Id` header value, and only
after validating it is a short, safe string (see `request_context.
is_safe_request_id`). It never crashes a request: a missing/unsafe
incoming id is silently replaced, never rejected with an error.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.request_context import (
    normalize_request_id,
    reset_current_request_id,
    set_current_request_id,
)

REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """One request-scoped correlation id, shared by the response header,
    `ResponseMetadata.request_id`, and every structured log line emitted
    while the route runs.

    Placement matters: this must be added to `app` *after*
    `CORSMiddleware` (see `app/main.py`) so Starlette's middleware stack
    nests it *inside* CORS but *outside* `ExceptionMiddleware` -- that
    ordering is what makes the same id available to `AppError`/
    `RequestValidationError`'s own registered handlers (which
    `ExceptionMiddleware` dispatches to), not just to a fully successful
    route body. A truly unhandled exception (no registered handler)
    still propagates past this middleware unchanged -- this class never
    swallows an exception, and the context is always reset via
    `finally` regardless of which path a given request took.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = normalize_request_id(incoming)

        token = set_current_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_current_request_id(token)

        # `MutableHeaders.__setitem__` replaces any existing value for
        # this header name rather than appending a second one -- a
        # response never ends up with two X-Request-Id header lines.
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
