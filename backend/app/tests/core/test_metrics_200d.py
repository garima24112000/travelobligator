from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.metrics import MAX_LABEL_SETS, OVERFLOW_VALUE, MetricsRegistry, registry
from app.main import app

# Section 200D: metrics registry semantics and cardinality controls.

_FORBIDDEN_LABELS = {
    "trip_id", "job_id", "user_id", "owner_id", "request_id", "revision_id", "branch_id", "destination",
    "query", "path", "url", "email", "feedback", "lease_owner", "coordinates", "date",
}


def _fresh() -> MetricsRegistry:
    reg = MetricsRegistry()
    reg.counter("c_total", "counter", ("kind",))
    reg.gauge("g", "gauge", ())
    reg.histogram("h_seconds", "hist", ("route",), buckets=(0.1, 1.0))
    return reg


def test_counters_gauges_and_histograms_render_in_prometheus_text_format() -> None:
    reg = _fresh()
    reg.inc("c_total", {"kind": "a"})
    reg.inc("c_total", {"kind": "a"}, 2)
    reg.set("g", 3)
    reg.observe("h_seconds", 0.05, {"route": "/x"})
    reg.observe("h_seconds", 5.0, {"route": "/x"})

    text = reg.render()

    assert "# TYPE c_total counter" in text and 'c_total{kind="a"} 3' in text
    assert "# TYPE g gauge" in text and "\ng 3" in text
    assert 'h_seconds_bucket{route="/x",le="0.1"} 1' in text
    assert 'h_seconds_bucket{route="/x",le="+Inf"} 2' in text
    assert 'h_seconds_count{route="/x"} 2' in text and 'h_seconds_sum{route="/x"} 5.05' in text


def test_an_undeclared_label_name_is_dropped_never_recorded() -> None:
    reg = _fresh()
    reg.inc("c_total", {"kind": "a", "trip_id": "trip_123"})  # extra label name -> dropped entirely
    reg.inc("c_total", {"trip_id": "trip_123"})
    assert reg.total("c_total") == 0.0 and "trip_123" not in reg.render()


def test_label_cardinality_is_capped_with_an_overflow_series() -> None:
    reg = _fresh()
    for n in range(MAX_LABEL_SETS + 50):
        reg.inc("c_total", {"kind": f"k{n}"})
    assert len(reg.label_sets("c_total")) == MAX_LABEL_SETS + 1  # capped + one overflow series
    assert reg.value("c_total", {"kind": OVERFLOW_VALUE}) == 50


def test_recording_never_raises_even_for_unknown_metrics_or_bad_values() -> None:
    reg = _fresh()
    reg.inc("nope")
    reg.set("c_total", 1)  # wrong kind
    reg.observe("h_seconds", float("nan"), {"route": "/x"})
    reg.observe("h_seconds", "x", {"route": "/x"})  # type: ignore[arg-type]
    reg.inc("c_total", {"kind": object()})
    assert reg.render().startswith("# HELP")


def test_disabled_registry_records_nothing() -> None:
    reg = _fresh()
    reg.enabled = False
    reg.inc("c_total", {"kind": "a"})
    assert reg.total("c_total") == 0.0


def test_label_values_are_escaped_and_bounded() -> None:
    reg = _fresh()
    reg.inc("c_total", {"kind": 'a"b\nc' + "x" * 200})
    out = reg.render()
    assert '\\"' in out and "\\n" in out and "x" * 100 not in out


def test_catalogue_never_declares_a_high_cardinality_label() -> None:
    for name, metric in registry._metrics.items():  # noqa: SLF001
        assert not (set(metric.labels) & _FORBIDDEN_LABELS), (name, metric.labels)
        assert name.startswith("travelobligator_")


# -- HTTP: route templates, not raw paths --------------------------------------------------------------------


@pytest.fixture()
def http_client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _routes(client: TestClient) -> set[str]:
    text = client.get("/metrics").text
    return set(re.findall(r'travelobligator_http_requests_total\{[^}]*route="([^"]+)"', text))


def test_http_metrics_use_route_templates_never_raw_ids(http_client: TestClient) -> None:
    before = registry.value(
        "travelobligator_http_requests_total", {"method": "GET", "route": "/trips/{trip_id}", "status_class": "4xx"}
    )
    for trip_id in ("trip_1", "trip_2", "trip_ab7f3c9d0e1122334455667788990011"):
        http_client.get(f"/trips/{trip_id}")
    http_client.get("/trips/trip_9/jobs/job_deadbeef")

    routes = _routes(http_client)
    assert "/trips/{trip_id}" in routes and "/trips/{trip_id}/jobs/{job_id}" in routes
    assert not any(re.search(r"trip_[0-9a-f]", r) or "job_dead" in r for r in routes), routes
    after = registry.value(
        "travelobligator_http_requests_total", {"method": "GET", "route": "/trips/{trip_id}", "status_class": "4xx"}
    )
    assert after - before == 3  # three different ids -> ONE series


def test_unmatched_paths_share_one_label_and_a_request_id_is_never_a_label(http_client: TestClient) -> None:
    for n in range(20):
        http_client.get(f"/no/such/route/{n}", headers={"X-Request-Id": f"req_custom_{n}"})
    routes = _routes(http_client)
    assert "unmatched" in routes and not any("no/such" in r for r in routes)
    assert "req_custom" not in http_client.get("/metrics").text


def test_a_5xx_response_is_counted_and_the_duration_histogram_is_recorded(
    http_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.routes.ops as ops

    monkeypatch.setattr(ops, "evaluate_readiness", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    before = registry.value("travelobligator_http_requests_total", {"method": "GET", "route": "/ready", "status_class": "5xx"})
    assert http_client.get("/ready").status_code == 500
    assert registry.value("travelobligator_http_requests_total", {"method": "GET", "route": "/ready", "status_class": "5xx"}) == before + 1
    assert registry.value("travelobligator_http_request_duration_seconds", {"method": "GET", "route": "/ready"}) >= 1


# -- /metrics endpoint ----------------------------------------------------------------------------------------------


def test_metrics_endpoint_content_type_and_disable_switch(http_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    response = http_client.get("/metrics")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/plain; version=0.0.4")
    monkeypatch.setenv("METRICS_ENABLED", "false")
    get_settings.cache_clear()
    assert http_client.get("/metrics").status_code == 404


def test_metrics_output_contains_no_secret_or_content_shaped_values(http_client: TestClient) -> None:
    http_client.get("/trips/trip_secretish")
    text = http_client.get("/metrics").text
    for needle in ("postgresql://", "redis://", "password", "@", "trip_secretish", "Lisbon"):
        assert needle not in text, needle


def test_metrics_failure_cannot_break_health_or_ready(http_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_lock", None)  # every metrics operation now errors internally
    assert http_client.get("/health").status_code == 200
    assert http_client.get("/ready").status_code == 200
