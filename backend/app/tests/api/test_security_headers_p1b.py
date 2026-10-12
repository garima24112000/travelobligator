from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security_headers_middleware import API_CONTENT_SECURITY_POLICY, SECURITY_HEADERS
from app.main import app
from app.tests.conftest import create_trip_payload, new_authenticated_client

# P1-B: every backend response carries its own security headers. The browser reaches this service through
# a rewrite on the frontend host, and a proxy is not relied on to add headers to what it forwards, so --
# exactly like `Cache-Control: no-store` -- they are set here, on the response itself. Hermetic.

_EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "cache-control": "no-store",
}


def _assert_protected(response, *, policy: bool = True) -> None:
    where = (response.request.method, response.request.url.path, response.status_code)
    for name, value in _EXPECTED.items():
        if name == "content-security-policy" and not policy:
            continue
        assert response.headers.get(name) == value, (name, where)
    # one value each: never a duplicated or comma-joined header from two layers
    for name in _EXPECTED:
        assert len(response.headers.get_list(name)) <= 1, (name, where)
    # the API never sends a Report-Only policy (that is the frontend's page policy) and never HSTS
    assert "content-security-policy-report-only" not in response.headers
    assert "strict-transport-security" not in response.headers


@pytest.fixture()
def http() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_the_values_are_the_documented_ones() -> None:
    assert SECURITY_HEADERS == {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }
    assert API_CONTENT_SECURITY_POLICY == "default-src 'none'; frame-ancestors 'none'"


def test_successful_responses_are_protected(client: TestClient) -> None:
    created = client.post("/trips", json=create_trip_payload())
    trip_id = created.json()["data"]["trip_id"]
    for response in (
        created,
        client.get("/auth/me"),
        client.get("/trips"),
        client.get(f"/trips/{trip_id}"),
        client.get(f"/trips/{trip_id}/summary"),
        client.post(f"/trips/{trip_id}/generate"),
        client.delete(f"/trips/{trip_id}"),
        client.get("/health"),
    ):
        assert response.status_code < 400, response.text
        _assert_protected(response)


def test_the_response_that_sets_the_session_cookie_is_protected(http: TestClient) -> None:
    signup = http.post("/auth/signup", json={"email": "p1b-headers@example.com", "password": "testpassword123"})
    login = http.post("/auth/login", json={"email": "p1b-headers@example.com", "password": "testpassword123"})
    logout = http.post("/auth/logout")
    for response in (signup, login, logout):
        assert "set-cookie" in response.headers
        _assert_protected(response)


def test_every_kind_of_error_response_is_protected(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = new_authenticated_client()
    other = new_authenticated_client()
    trip_id = owner.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    monkeypatch.setenv("MAX_TRIPS_PER_USER", "1")
    get_settings.cache_clear()

    responses = {
        401: http.get("/trips"),  # no session
        403: other.get(f"/trips/{trip_id}"),  # someone else's trip
        404: owner.get("/trips/trip_that_does_not_exist"),  # AppError
        405: http.put("/health"),  # method not allowed (framework)
        422: owner.post("/trips", json={"primary_destination": ""}),  # request validation
        429: owner.post("/trips", json=create_trip_payload()),  # limit
        409: owner.post(f"/trips/{trip_id}/regenerate"),  # refusal
    }
    for status, response in responses.items():
        assert response.status_code == status, (status, response.status_code, response.text)
        _assert_protected(response)
    unknown = http.get("/no-such-route")  # the framework's own 404
    assert unknown.status_code == 404
    _assert_protected(unknown)


def test_an_unhandled_failure_is_protected_too(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.exc import OperationalError

    from app.repositories import factory

    owner = new_authenticated_client()

    def broken(*args, **kwargs):
        raise OperationalError("stmt", {}, Exception("host=db user=secret"))

    monkeypatch.setattr(factory._local_trip_repository, "list_by_owner_id", broken)
    outage = owner.get("/trips")
    assert outage.status_code == 503 and "secret" not in outage.text
    _assert_protected(outage)


def test_cors_preflight_and_cross_origin_responses_are_protected(http: TestClient) -> None:
    preflight = http.options(
        "/auth/login", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"  # CORS is untouched
    _assert_protected(preflight)
    refused = http.options(
        "/auth/login", headers={"Origin": "https://not-the-frontend.invalid", "Access-Control-Request-Method": "POST"}
    )
    assert refused.status_code == 400 and "access-control-allow-origin" not in refused.headers
    _assert_protected(refused)
    simple = http.get("/health", headers={"Origin": "http://localhost:3000"})
    assert simple.headers["access-control-expose-headers"] == "X-Request-Id"
    _assert_protected(simple)


def test_operational_endpoints_are_protected(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    for path in ("/health", "/ready", "/metrics"):
        _assert_protected(http.get(path))
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    for path in ("/health", "/ready", "/metrics", "/docs", "/redoc", "/openapi.json"):
        response = http.get(path)
        assert response.status_code == (200 if path == "/health" else 404)
        _assert_protected(response)  # in production the documentation paths are plain 404s, policy included


def test_the_development_documentation_pages_keep_working(http: TestClient) -> None:
    for path in ("/docs", "/redoc"):
        page = http.get(path)
        assert page.status_code == 200 and "text/html" in page.headers["content-type"]
        # the viewer is loaded from a CDN, so the API's deny-all policy is not put on these two pages...
        assert "content-security-policy" not in page.headers
        # ...but everything else is
        _assert_protected(page, policy=False)
    _assert_protected(http.get("/openapi.json"))


def test_a_handler_cannot_weaken_a_header(http: TestClient) -> None:
    from fastapi import Response

    @app.get("/__p1b_weak_headers", include_in_schema=False)
    def weak(response: Response) -> dict:
        response.headers["X-Frame-Options"] = "ALLOWALL"
        response.headers["Content-Security-Policy"] = "default-src *"
        response.headers["Cache-Control"] = "public, max-age=3600"
        response.headers["Referrer-Policy"] = "unsafe-url"
        return {"ok": True}

    try:
        _assert_protected(http.get("/__p1b_weak_headers"))
    finally:
        app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", "") != "/__p1b_weak_headers"]


def test_every_registered_route_answers_with_the_headers(http: TestClient) -> None:
    """A sweep over the whole API, unauthenticated: whatever a route answers (401, 404, 405, 422, 200), the
    response is protected. A route added later is covered without touching this test."""
    seen = 0
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or path in ("/docs", "/redoc"):
            continue
        url = path.replace("{trip_id}", "trip_x").replace("{job_id}", "job_x").replace("{lock_id}", "lock_x")
        url = url.replace("{branch_id}", "branch_x").replace("{revision_id}", "revision_x")
        assert "{" not in url, path
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            _assert_protected(http.request(method, url))
            seen += 1
    assert seen >= 30


def test_the_headers_do_not_disturb_the_existing_ones(client: TestClient) -> None:
    response = client.get("/trips")
    assert response.headers["x-request-id"].startswith("req_")
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["metadata"]["request_id"] == response.headers["x-request-id"]
