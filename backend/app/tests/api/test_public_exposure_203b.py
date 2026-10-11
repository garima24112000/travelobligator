from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

# Section 203B: what a PUBLIC backend behind a CDN rewrite exposes -- ops endpoint policy, response
# cacheability, the session-cookie attributes and CORS. Hermetic (Local JSON, no Redis, no network).

_OPS_TOKEN = "SENTINEL_203B_OPS_TOKEN_" + "z" * 16
_BEARER = {"Authorization": f"Bearer {_OPS_TOKEN}"}


def _env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture()
def http() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# -- /health, /ready, /metrics -------------------------------------------------------------------------------------


def test_ops_endpoints_stay_open_outside_production_without_a_token(http: TestClient) -> None:
    assert http.get("/health").status_code == 200
    assert http.get("/ready").status_code == 200
    assert http.get("/metrics").status_code == 200


def test_production_without_a_token_exposes_only_health(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, APP_ENV="production")
    assert http.get("/health").status_code == 200
    for path in ("/ready", "/metrics"):
        response = http.get(path)
        assert response.status_code == 404 and response.text == "Not Found\n"
        # a guessed credential changes nothing while no token is configured
        assert http.get(path, headers={"Authorization": "Bearer "}).status_code == 404


@pytest.mark.parametrize("app_env", ["production", "development"])
def test_a_configured_token_is_required_in_every_environment(
    http: TestClient, monkeypatch: pytest.MonkeyPatch, app_env: str
) -> None:
    _env(monkeypatch, APP_ENV=app_env, OPS_TOKEN=_OPS_TOKEN)
    assert http.get("/health").status_code == 200  # never gated
    for path in ("/ready", "/metrics"):
        assert http.get(path).status_code == 404
        for wrong in (
            {"Authorization": "Bearer wrong-token"},
            {"Authorization": _OPS_TOKEN},  # no scheme
            {"Authorization": f"Basic {_OPS_TOKEN}"},
            {"Authorization": f"Bearer {_OPS_TOKEN}x"},
            {"X-Ops-Token": _OPS_TOKEN},
        ):
            refused = http.get(path, headers=wrong)
            assert refused.status_code == 404 and _OPS_TOKEN not in refused.text
        assert http.get(path, headers=_BEARER).status_code == 200


def test_the_token_does_not_override_the_metrics_kill_switch(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, APP_ENV="production", OPS_TOKEN=_OPS_TOKEN, METRICS_ENABLED="false")
    assert http.get("/metrics", headers=_BEARER).status_code == 404
    assert http.get("/ready", headers=_BEARER).status_code == 200


def test_ops_responses_never_contain_the_token(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, OPS_TOKEN=_OPS_TOKEN)
    for path in ("/health", "/ready", "/metrics"):
        assert _OPS_TOKEN not in http.get(path, headers=_BEARER).text


# -- Cache-Control -------------------------------------------------------------------------------------------------


def _assert_no_store(response) -> None:
    assert response.headers.get("cache-control") == "no-store", (response.request.method, response.request.url.path)


def test_every_kind_of_response_is_no_store(client: TestClient, generated_trip_id: str, http: TestClient) -> None:
    trip = f"/trips/{generated_trip_id}"
    authenticated = [
        client.get("/auth/me"),
        client.get("/trips"),
        client.get(trip),
        client.get(f"{trip}/summary"),
        client.get(f"{trip}/experience-plan"),
        client.get(f"{trip}/jobs"),
        client.get(f"{trip}/generation-progress"),
        client.get(f"{trip}/regeneration-readiness"),
        client.get(f"{trip}/regeneration-attempts"),
        client.get(f"{trip}/branches"),
        client.get(f"{trip}/revisions/compare", params={"base_revision_id": "a", "target_revision_id": "b"}),
        client.post(f"{trip}/feedback", json={"feedback_text": "Slower mornings please."}),
        client.post(f"{trip}/regenerate", json={"confirm": False}),
    ]
    for response in authenticated:
        assert response.status_code != 401, response.request.url.path
        _assert_no_store(response)
    for response in (
        http.get("/auth/me"),  # 401
        http.get(trip),  # 401
        http.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong-password"}),
        http.post("/auth/login", json={}),  # 422 validation handler
        http.post("/auth/logout"),
        http.get("/no-such-route"),  # 404
        http.get("/health"),
        http.get("/ready"),
        http.get("/metrics"),
        http.options(  # CORS preflight answered by the middleware, never reaching a route
            "/trips",
            headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
        ),
    ):
        _assert_no_store(response)


# -- session cookie ------------------------------------------------------------------------------------------------


def _attributes(set_cookie: str) -> set[str]:
    return {part.strip().split("=", 1)[0].lower() for part in set_cookie.split(";")[1:]}


def test_production_session_cookie_is_secure_httponly_lax_and_host_only(
    http: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch, SESSION_COOKIE_SECURE="true", SESSION_COOKIE_SAMESITE="lax")
    credentials = {"email": f"test-203b-{uuid.uuid4().hex}@example.com", "password": "longenough1"}
    for response in (
        http.post("/auth/signup", json=credentials),
        http.post("/auth/login", json=credentials),
        http.post("/auth/logout"),
    ):
        assert response.status_code in (200, 201)
        set_cookie = response.headers["set-cookie"]
        assert set_cookie.startswith("travelobligator_session=")
        assert "samesite=lax" in set_cookie.lower()
        attributes = _attributes(set_cookie)
        assert {"secure", "httponly", "path"} <= attributes
        # host-only: with no Domain attribute the browser binds the cookie to the host it called (the
        # frontend origin, through the same-origin /api rewrite) and never shares it with sibling hosts.
        assert "domain" not in attributes
        assert "path=/" in set_cookie.lower().replace(" ", "")


# -- CORS ----------------------------------------------------------------------------------------------------------


def test_cors_allows_only_the_configured_origin_and_never_a_wildcard(http: TestClient) -> None:
    allowed = http.get("/health", headers={"Origin": "http://localhost:3000"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert allowed.headers["access-control-allow-credentials"] == "true"

    for origin in ("https://unrelated.example.test", "http://localhost:3001", "null"):
        simple = http.get("/health", headers={"Origin": origin})
        assert "access-control-allow-origin" not in simple.headers
        preflight = http.options(
            "/trips", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}
        )
        assert preflight.status_code == 400
        assert "access-control-allow-origin" not in preflight.headers


# -- P0: development-only endpoints do not exist in production ------------------------------------------------------

_DOCUMENTATION_PATHS = ("/docs", "/redoc", "/openapi.json")


def test_the_api_documentation_is_served_outside_production(http: TestClient) -> None:
    schema = http.get("/openapi.json")
    assert schema.status_code == 200 and "/trips/{trip_id}/generate" in schema.json()["paths"]
    for path in ("/docs", "/redoc"):
        page = http.get(path)
        assert page.status_code == 200 and "/openapi.json" in page.text
    # the documentation routes do not list themselves
    assert not set(_DOCUMENTATION_PATHS) & set(schema.json()["paths"])


def test_production_serves_no_api_documentation(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    unknown = http.get("/no-such-path")
    _env(monkeypatch, APP_ENV="production")
    for path in _DOCUMENTATION_PATHS:
        response = http.get(path)
        # exactly what a path that does not exist answers: nothing to tell them apart
        assert (response.status_code, response.json()) == (404, unknown.json()) == (404, {"detail": "Not Found"})
        assert response.headers["cache-control"] == "no-store"


def test_fastapi_serves_no_second_copy_of_the_documentation() -> None:
    assert (app.docs_url, app.redoc_url, app.openapi_url) == (None, None, None)
    served = [route.path for route in app.routes if getattr(route, "path", None) in _DOCUMENTATION_PATHS]
    assert sorted(served) == sorted(_DOCUMENTATION_PATHS)  # each exactly once


def _signed_in() -> tuple[TestClient, str]:
    from app.tests.conftest import create_trip_payload, new_authenticated_client

    owner = new_authenticated_client()
    trip_id = owner.post("/trips", json=create_trip_payload()).json()["data"]["trip_id"]
    return owner, trip_id


def test_the_shadow_run_works_for_the_owner_outside_production() -> None:
    owner, trip_id = _signed_in()
    response = owner.post(f"/trips/{trip_id}/langgraph-shadow-run")
    assert response.status_code == 200 and response.json()["data"]["persisted"] is False


def test_the_shadow_run_does_not_exist_in_production(http: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.gateway import provider_gateway
    from app.services.langgraph_planning_service import LangGraphPlanningService

    owner, trip_id = _signed_in()
    runs: list[str] = []
    monkeypatch.setattr(LangGraphPlanningService, "run", lambda self, *args, **kwargs: runs.append("ran"))
    monkeypatch.setattr(
        provider_gateway.places, "search_attractions", lambda *args, **kwargs: runs.append("provider")
    )
    _env(monkeypatch, APP_ENV="production")

    for caller in (owner, http):  # the trip's owner, and a visitor with no session at all
        for target in (trip_id, "trip_that_does_not_exist"):
            response = caller.post(f"/trips/{target}/langgraph-shadow-run")
            # the same 404 before the session or the trip is looked at -- never 401 / 403 / TRIP_NOT_FOUND
            assert (response.status_code, response.json()) == (404, {"detail": "Not Found"})
    assert runs == []  # the planning graph never ran and no provider was asked anything


def test_only_known_routes_can_start_planning_work() -> None:
    """P0 inventory. Every route handler that can reach a provider or a model does so through one of
    these names. Exactly four handlers may: generate and regenerate (admitted as jobs in production,
    where the synchronous branch is rejected at startup), the targeted-regeneration helper they share,
    and the shadow run (404 in production). A new handler that plans anything fails this test until
    it is put behind the job admission limits."""
    import ast
    import inspect

    from app.api.routes import auth as auth_routes
    from app.api.routes import ops as ops_routes
    from app.api.routes import trips as trip_routes

    planning_entry_points = {
        "generate_full_plan",
        "generate_full_plan_via_langgraph",
        "rerun_affected_stages",
        "apply_regeneration_mutation",
        "targeted_regeneration_application_service",
        "LangGraphPlanningService",
        "start_generate_job",
        "start_regenerate_job",
        "start_targeted_regenerate_job",
    }

    def handlers_that_plan(module) -> set[str]:
        found = set()
        for node in ast.parse(inspect.getsource(module)).body:
            if not isinstance(node, ast.FunctionDef):
                continue
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            if names & planning_entry_points:
                found.add(node.name)
        return found

    assert handlers_that_plan(trip_routes) == {
        "generate_trip_plan",
        "regenerate_trip_plan",
        "_regenerate_targeted",
        "run_langgraph_shadow",
    }
    assert handlers_that_plan(auth_routes) == set() and handlers_that_plan(ops_routes) == set()

    shadow = next(route for route in app.routes if getattr(route, "path", "").endswith("/langgraph-shadow-run"))
    assert [dependency.call.__name__ for dependency in shadow.dependant.dependencies][0] == "_development_only"
