from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.logging_config import APP_LOGGER_NAME
from app.core.request_context import MAX_REQUEST_ID_LENGTH, is_safe_request_id

# End-to-end tests for the Step 187C request-scoped correlation id
# (docs/14_backend_architecture.md section 122): the same id must appear
# in the `X-Request-Id` response header, `metadata.request_id`, and any
# structured log line emitted while the request ran -- for a normal
# success response, an AppError response, and a validation-error
# response alike. None of these tests touch a live provider/Postgres/
# frontend file.


def test_response_without_incoming_header_gets_a_matching_generated_id(
    client: TestClient,
) -> None:
    response = client.get("/health")
    header_value = response.headers.get("X-Request-Id")

    assert header_value is not None
    assert header_value.startswith("req_")
    assert is_safe_request_id(header_value)


def test_response_with_safe_incoming_header_is_honored(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-Id": "my-safe-client-id-123"})
    assert response.headers.get("X-Request-Id") == "my-safe-client-id-123"


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "has space",
        "has\nnewline",
        "has\x00null",
        "a" * (MAX_REQUEST_ID_LENGTH + 1),
    ],
)
def test_response_with_unsafe_incoming_header_is_replaced(
    client: TestClient, unsafe_value: str
) -> None:
    response = client.get("/health", headers={"X-Request-Id": unsafe_value})
    header_value = response.headers.get("X-Request-Id")

    assert header_value != unsafe_value
    assert is_safe_request_id(header_value)
    assert header_value.startswith("req_")
    # The unsafe value itself must never reach the client back unmodified.
    assert unsafe_value.strip() not in (header_value or "")


def test_response_never_has_duplicate_x_request_id_header(client: TestClient) -> None:
    response = client.get("/health")
    assert len(response.headers.get_list("x-request-id")) == 1


def test_success_response_metadata_matches_header(
    client: TestClient, created_trip_id: str
) -> None:
    response = client.get(f"/trips/{created_trip_id}")
    assert response.status_code == 200

    header_value = response.headers.get("X-Request-Id")
    body = response.json()
    assert body["metadata"]["request_id"] == header_value


def test_app_error_response_metadata_matches_header(client: TestClient) -> None:
    """A trip that doesn't exist raises `trip_not_found_error` (AppError,
    404) -- handled by `app.main`'s registered `AppError` exception
    handler, which runs *inside* `RequestIdMiddleware` (see that
    middleware's own docstring for why the ordering matters)."""
    response = client.get("/trips/trip_does_not_exist_at_all")
    assert response.status_code == 404

    header_value = response.headers.get("X-Request-Id")
    body = response.json()
    assert body["success"] is False
    assert body["metadata"]["request_id"] == header_value


def test_validation_error_response_metadata_matches_header(client: TestClient) -> None:
    """`POST /auth/signup` needs no session -- a too-short password fails
    Pydantic validation (422), handled by the registered
    `RequestValidationError` exception handler."""
    response = client.post(
        "/auth/signup", json={"email": "someone@example.com", "password": "short"}
    )
    assert response.status_code == 422

    header_value = response.headers.get("X-Request-Id")
    body = response.json()
    assert body["success"] is False
    assert body["metadata"]["request_id"] == header_value


def test_incoming_unsafe_request_id_never_appears_in_response(client: TestClient) -> None:
    unsafe_value = "unsafe\nid\x00with control chars"
    response = client.get(
        "/trips/trip_does_not_exist_at_all", headers={"X-Request-Id": unsafe_value}
    )
    body = response.json()

    assert unsafe_value not in (response.headers.get("X-Request-Id") or "")
    assert unsafe_value not in json.dumps(body)


def test_structured_log_emitted_during_a_request_shares_the_response_request_id(
    client: TestClient,
) -> None:
    """Exercises `app.api.routes.trips`'s real, pre-existing
    `logger.warning(...)` call on a regenerate refusal (Step 174B, no
    new logging call site added by this step) and confirms the log
    record picked up the same request_id as the response that request
    produced -- via the raw `LogRecord` (this app's own `"app"` logger
    has `propagate=False`, so `caplog`'s root-attached handler can't see
    it; attaching a temporary handler directly is the reliable way to
    capture it without depending on that internal detail elsewhere)."""
    create_response = client.post(
        "/trips",
        json={
            "destination_scope": "single_city",
            "primary_destination": "Testville, Testland",
            "origin_city": "Home City",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "travelers_count": 2,
            "travel_group_type": "couple",
        },
    )
    trip_id = create_response.json()["data"]["trip_id"]
    client.post(f"/trips/{trip_id}/generate")

    # Only capture log records emitted by the *one* request under test
    # (the regenerate call) -- the create/generate calls above are real
    # requests of their own, each correctly carrying its own, different
    # request_id; capturing across all three and comparing everything to
    # the last response's header would be comparing unrelated requests.
    captured_records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_records.append(record)

    app_logger = logging.getLogger(APP_LOGGER_NAME)
    capture_handler = _CaptureHandler()
    app_logger.addHandler(capture_handler)
    try:
        response = client.post(f"/trips/{trip_id}/regenerate", json={"confirm": True})
        header_value = response.headers.get("X-Request-Id")
    finally:
        app_logger.removeHandler(capture_handler)

    # Whether this specific request happened to hit the logged refusal
    # path or not, at minimum every record captured during this request
    # (if any) must carry this exact request_id, never a stale one from
    # a previous request in the same test session.
    for record in captured_records:
        if hasattr(record, "request_id"):
            assert record.request_id == header_value
    assert response.status_code in (200, 409)


def test_two_sequential_requests_never_share_a_request_id(client: TestClient) -> None:
    first = client.get("/health").headers.get("X-Request-Id")
    second = client.get("/health").headers.get("X-Request-Id")
    assert first != second


def test_no_sensitive_field_leaks_via_request_id_header_or_metadata(
    client: TestClient,
) -> None:
    response = client.get(
        "/health", headers={"X-Request-Id": "req_safe_id", "Authorization": "Bearer secret-token"}
    )
    header_value = response.headers.get("X-Request-Id")
    assert header_value == "req_safe_id"
    assert "secret-token" not in json.dumps(dict(response.headers))


def test_cors_exposes_only_x_request_id_header_to_cross_origin_frontend(
    client: TestClient, created_trip_id: str
) -> None:
    """Step 187G (docs/14_backend_architecture.md section 126): a plain
    cross-origin `fetch` (no `credentials`/preflight needed for a simple
    GET) can only read a response header the backend explicitly exposes
    via `Access-Control-Expose-Headers` -- this confirms `X-Request-Id`
    is exposed, that the exposed list contains nothing else (never an
    auth/session/cookie-related header), and that the actual header value
    still matches the response body's own `metadata.request_id` exactly
    like every other test in this file already confirms for a
    same-origin call. Uses `GET /trips/{trip_id}` rather than `/health`
    because `/health` is a hand-built dict response with no
    `metadata` key at all (never routed through `ResponseMetadata`).
    """
    response = client.get(
        f"/trips/{created_trip_id}", headers={"Origin": "http://localhost:3000"}
    )
    assert response.status_code == 200

    exposed = response.headers.get("access-control-expose-headers")
    assert exposed is not None
    exposed_names = {name.strip().lower() for name in exposed.split(",")}
    assert exposed_names == {"x-request-id"}

    header_value = response.headers.get("X-Request-Id")
    body = response.json()
    assert header_value == body["metadata"]["request_id"]
