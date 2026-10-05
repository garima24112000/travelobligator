from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.core.provider_usage import GenerationProviderContext
from app.models.common import ProviderStatus
from app.models.planning_state import PlanningState, UserLock
from app.models.routing import RouteResult
from app.providers import errors, geoapify_client
from app.providers.errors import ProviderRequestError, classify_bad_request
from app.providers.routing import geoapify_adapter as routing_module
from app.providers.routing.geoapify_adapter import GeoapifyRoutingAdapter
from app.services.grounded_anchors import grounded_anchor_place_ids
from app.services.routability_repair_service import (
    ROUTING_COVERAGE_RELEASE_THRESHOLD,
    RoutabilityRepairService,
    apply_routability_repair_safely,
    routing_coverage,
)
from app.services.route_feasibility_service import RouteFeasibilityService
from app.storage.provider_cache_store import ProviderCacheStore
from app.tests.services.test_batch1_tuning_fixes_3b import _promoted, _promotion
from app.tests.services.test_final_quality_correction_203c2b import _Gateway, _poi, _point, _state

# Section 3C.2: the bounded routability repair, and the safe classification
# of a rejected routing request. A day is routed with ONE multi-waypoint
# request, so a single stop the provider cannot route to fails every leg of
# its day. Every fixture is synthetic: places are named for their role.

# -- fixtures -------------------------------------------------------------------------------------------

# Day 1: three routable stops. Day 2: the second stop cannot be routed.
_A = _poi("a", "Museum Alpha", _point(50.000, 10.000))
_B = _poi("b", "Museum Beta", _point(50.002, 10.002))
_C = _poi("c", "Museum Gamma", _point(50.004, 10.000))
_D = _poi("d", "Museum Delta", _point(50.050, 10.050))
_X = _poi("x", "Museum Unroutable", _point(50.052, 10.052))
_E = _poi("e", "Museum Epsilon", _point(50.054, 10.050))
# Unused candidates near day 2.
_NEAR = _poi("near", "Museum Nearby", _point(50.053, 10.051))
_NEAR_TWO = _poi("near2", "Museum Nearby Two", _point(50.0535, 10.0515))

_X_POINT = (50.052, 10.052)


class _RejectingGateway(_Gateway):
    """A routing provider that rejects the WHOLE request when it contains a
    point it cannot route to -- exactly how a multi-waypoint request fails."""

    def __init__(
        self,
        unroutable: tuple[tuple[float, float], ...] = (_X_POINT,),
        *,
        reason: str | None = errors.REASON_UNROUTABLE_ENDPOINT,
        reject_multi_waypoint: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.unroutable = set(unroutable)
        self.reason = reason
        self.reject_multi_waypoint = reject_multi_waypoint

    def get_route_sequence(self, points: list[tuple[float, float]], provider_context: Any = None) -> list[RouteResult]:
        results = super().get_route_sequence(points, provider_context)
        if any(point in self.unroutable for point in points) or (self.reject_multi_waypoint and len(points) > 2):
            rejected = RouteResult(
                provider="fixture_routing", status=ProviderStatus.FAILED, source="fixture_routing",
                message="The routing provider request failed.", failure_reason=self.reason,
            )
            return [rejected] * len(results)
        return results


def _routed_state(
    gateway: _Gateway,
    days: list[list[dict[str, Any]]] | None = None,
    unused: tuple[dict[str, Any], ...] = (_NEAR,),
    **trip: Any,
) -> tuple[PlanningState, RouteFeasibilityService]:
    """A plan whose legs are what `gateway` returned for the initial routing pass."""
    days = days if days is not None else [[_A, _B, _C], [_D, _X, _E]]
    pois = [poi for day in days for poi in day] + list(unused)
    state = _state(pois, days, **trip)
    service = RouteFeasibilityService(gateway)  # type: ignore[arg-type]
    state.route_feasibility_report = service.build_report(state)
    gateway.calls.clear()
    return state, service


def _names(state: PlanningState, day: int = 1) -> list[str]:
    return [stop.name for stop in state.experience_plan.daily_plans[day].experiences]


def _leg_statuses(state: PlanningState) -> list[str]:
    return [leg.status.value for leg in state.route_feasibility_report.legs]


def _repair(state: PlanningState, service: RouteFeasibilityService, context: Any = None):
    return RoutabilityRepairService(service).repair(state, context)


# =====================================================================================
# The cause: one request per day, so one unroutable stop fails every leg of the day
# =====================================================================================


def test_one_unroutable_stop_fails_every_leg_of_its_day_wherever_it_sits() -> None:
    for days in ([[_A, _B, _C], [_D, _X, _E]], [[_A, _B, _C], [_X, _D, _E]], [[_A, _B, _C], [_D, _E, _X]]):
        state, _ = _routed_state(_RejectingGateway(), days)
        assert _leg_statuses(state) == ["success", "success", "failed", "failed"]
        assert routing_coverage(state) == 0.5
        assert {leg.failure_reason for leg in state.route_feasibility_report.legs[2:]} == {"unroutable_endpoint"}
        # so the failed legs alone cannot say which stop is responsible


# 1. an interior ordinary stop causes both adjacent failures
def test_an_unroutable_interior_stop_is_replaced_and_only_the_affected_legs_are_rerouted() -> None:
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway)

    report = _repair(state, service)

    attempt = report.attempts[0]
    assert (attempt.day_number, attempt.reason, attempt.accepted) == (2, "accepted", True)
    assert (attempt.failed_legs_before, attempt.failed_legs_after, attempt.relocalized_legs) == (2, 0, 2)
    assert (attempt.suspect_place, attempt.suspect_protection) == ("Museum Unroutable", "replaceable")
    assert (attempt.replaced_place, attempt.replacement_place) == ("Museum Unroutable", "Museum Nearby")
    assert attempt.suspect_was_grounded_anchor is False
    # two requests to localise the failure (one per failed leg), one for the replacement's legs
    assert [len(points) for points in gateway.calls] == [2, 2, 3]
    assert gateway.calls[2] == [(50.050, 10.050), (50.053, 10.051), (50.054, 10.050)]
    # factual coverage improved, every leg carries a real provider route, and day 1 was not touched
    assert (report.coverage_before, report.coverage_after) == (0.5, 1.0)
    assert _leg_statuses(state) == ["success"] * 4
    assert all(leg.distance_meters is not None and leg.duration_seconds is not None for leg in state.route_feasibility_report.legs)
    assert _names(state, 0) == ["Museum Alpha", "Museum Beta", "Museum Gamma"]
    assert _names(state) == ["Museum Delta", "Museum Nearby", "Museum Epsilon"]  # same position, same day size
    day = state.experience_plan.daily_plans[1]
    assert [stop.stop_order for stop in day.experiences] == [1, 2, 3]
    replacement = day.experiences[1]
    assert replacement.provider_place_id == "geoapify/near" and replacement.provider_source == "geoapify_places"
    assert replacement.coordinates is not None and "Museum Nearby" in day.goal
    pairs = [(leg.from_experience_name, leg.to_experience_name) for leg in state.route_feasibility_report.legs]
    assert pairs[2:] == [("Museum Delta", "Museum Nearby"), ("Museum Nearby", "Museum Epsilon")]


def test_an_unroutable_first_or_last_stop_is_found_by_localising_the_failed_legs() -> None:
    for days, kept_pair in (
        ([[_A, _B, _C], [_X, _D, _E]], ("Museum Delta", "Museum Epsilon")),
        ([[_A, _B, _C], [_D, _E, _X]], ("Museum Delta", "Museum Epsilon")),
    ):
        gateway = _RejectingGateway()
        state, service = _routed_state(gateway, days)
        report = _repair(state, service)
        attempt = report.attempts[0]
        # the leg between the two routable stops routes once asked for on its own, which
        # proves both of them routable and leaves the end stop as the only suspect
        assert attempt.accepted and attempt.suspect_place == "Museum Unroutable"
        assert "Museum Unroutable" not in _names(state) and "Museum Nearby" in _names(state)
        assert kept_pair in [(leg.from_experience_name, leg.to_experience_name) for leg in state.route_feasibility_report.legs]
        assert report.coverage_after == 1.0 and len(gateway.calls) == 3


def test_legs_that_route_one_at_a_time_are_kept_without_replacing_anything() -> None:
    # the provider rejects the multi-waypoint request but routes each leg on its own
    gateway = _RejectingGateway(unroutable=(), reject_multi_waypoint=True)
    state, service = _routed_state(gateway, [[_A, _B], [_D, _X, _E]])
    assert routing_coverage(state) == pytest.approx(1 / 3)

    report = _repair(state, service)

    attempt = report.attempts[0]
    assert (attempt.reason, attempt.accepted, attempt.replaced_place) == ("localized", True, None)
    assert _names(state) == ["Museum Delta", "Museum Unroutable", "Museum Epsilon"]  # no stop was replaced
    assert report.coverage_after == 1.0 and [len(points) for points in gateway.calls] == [2, 2]


# 2. a grounded anchor causes both adjacent failures
def test_an_unroutable_grounded_anchor_may_be_replaced_for_routability_only() -> None:
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway, interests=["history"])
    state.ai_candidate_promotion_report = _promotion([_promoted("x", "Museum Unroutable", _point(50.052, 10.052))])
    assert grounded_anchor_place_ids(state) == {"geoapify/x"}
    stops_before = sum(len(day.experiences) for day in state.experience_plan.daily_plans)

    report = _repair(state, service)

    attempt = report.attempts[0]
    assert attempt.accepted and attempt.suspect_was_grounded_anchor is True
    assert (attempt.replaced_place, attempt.replacement_place) == ("Museum Unroutable", "Museum Nearby")
    # the plan keeps its size (>= R is untouched) and the requested interest stays covered
    assert sum(len(day.experiences) for day in state.experience_plan.daily_plans) == stops_before
    assert "history" in state.experience_plan.daily_plans[1].experiences[1].matched_interests
    assert report.coverage_after == 1.0

    # an ordinary stop is preferred to an anchor when either could be the cause (a two-stop day)
    gateway = _RejectingGateway(unroutable=((50.050, 10.050), _X_POINT))
    state, service = _routed_state(gateway, [[_D, _X]], unused=(_NEAR, _NEAR_TWO))
    state.ai_candidate_promotion_report = _promotion([_promoted("x", "Museum Unroutable", _point(50.052, 10.052))])
    attempt = _repair(state, service).attempts[0]
    assert attempt.suspect_place == "Museum Delta" and attempt.suspect_was_grounded_anchor is False


# 3. a must-visit causes the failures
def test_an_unroutable_must_visit_is_never_replaced_and_the_limitation_stays_explicit() -> None:
    gateway = _RejectingGateway()
    days = [[_A, _B, _C], [_D, {**_X, "must_visit_term": "The Unroutable One"}, _E]]
    state, service = _routed_state(gateway, days, must_visit=["The Unroutable One"])

    report = _repair(state, service)

    attempt = report.attempts[0]
    assert (attempt.reason, attempt.accepted) == ("suspect_protected", False)
    assert (attempt.suspect_place, attempt.suspect_protection) == ("Museum Unroutable", "must_visit")
    assert attempt.replaced_place is None and attempt.failed_legs_after == 2
    assert _names(state) == ["Museum Delta", "Museum Unroutable", "Museum Epsilon"]
    # nothing is marked routed: the legs stay failed and coverage stays below the threshold
    assert _leg_statuses(state) == ["success", "success", "failed", "failed"]
    assert report.coverage_after == 0.5 < ROUTING_COVERAGE_RELEASE_THRESHOLD
    assert all(leg.distance_meters is None for leg in state.route_feasibility_report.legs[2:])
    assert len(gateway.calls) == 2  # only the localisation; no replacement was routed


# 4. a user-locked stop causes the failures
def test_an_unroutable_user_locked_stop_is_never_replaced() -> None:
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway)
    locked = state.experience_plan.daily_plans[1].experiences[1]
    state.user_locks = [UserLock(locked_item_type="experience", locked_item_id=locked.experience_id)]

    attempt = _repair(state, service).attempts[0]

    assert (attempt.reason, attempt.suspect_protection, attempt.accepted) == ("suspect_protected", "user_lock", False)
    assert _names(state) == ["Museum Delta", "Museum Unroutable", "Museum Epsilon"]
    assert _leg_statuses(state)[2:] == ["failed", "failed"]


# 5. the candidate replacement also fails routing
def test_a_replacement_that_cannot_be_routed_either_is_rolled_back() -> None:
    gateway = _RejectingGateway(unroutable=(_X_POINT, (50.053, 10.051)))
    state, service = _routed_state(gateway)
    before = state.model_dump(mode="json", exclude={"routability_repair_report", "updated_at"})

    report = _repair(state, service)

    attempt = report.attempts[0]
    assert (attempt.reason, attempt.accepted) == ("replacement_unroutable", False)
    assert (attempt.replaced_place, attempt.replacement_place) == ("Museum Unroutable", "Museum Nearby")
    assert attempt.failed_legs_after == 2 and report.coverage_after == 0.5
    # the plan and its legs are exactly what they were
    assert state.model_dump(mode="json", exclude={"routability_repair_report", "updated_at"}) == before
    assert len(gateway.calls) == 3  # bounded: ONE candidate was tried, not every candidate


# 6. the replacement would cause an unreasonable burden
def test_a_replacement_that_makes_the_day_a_long_travel_day_is_rolled_back() -> None:
    # near enough to be a candidate, but each walk to it is longer than the walking-leg limit
    distant = _poi("distant", "Museum Distant", _point(50.073, 10.070))
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway, unused=(distant,))

    attempt = _repair(state, service).attempts[0]

    assert (attempt.reason, attempt.accepted) == ("replacement_route_burden", False)
    assert attempt.replacement_place == "Museum Distant"
    assert _names(state) == ["Museum Delta", "Museum Unroutable", "Museum Epsilon"]
    assert _leg_statuses(state)[2:] == ["failed", "failed"]

    # a candidate far from the day is not even tried
    remote = _poi("remote", "Museum Remote", _point(50.400, 10.400))
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway, unused=(remote,))
    attempt = _repair(state, service).attempts[0]
    assert attempt.reason == "no_suitable_candidate" and len(gateway.calls) == 2


# 7. no compatible replacement
def test_without_a_compatible_replacement_the_itinerary_is_kept_with_its_failure_signal() -> None:
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway, unused=())

    report = _repair(state, service)

    attempt = report.attempts[0]
    assert (attempt.reason, attempt.accepted, attempt.replacement_place) == ("no_suitable_candidate", False, None)
    assert attempt.suspect_place == "Museum Unroutable"
    assert _names(state) == ["Museum Delta", "Museum Unroutable", "Museum Epsilon"]
    assert _leg_statuses(state) == ["success", "success", "failed", "failed"]
    assert report.coverage_after == 0.5 and routing_coverage(state) < ROUTING_COVERAGE_RELEASE_THRESHOLD

    # a candidate of a clearly lower quality tier is not a compatible replacement
    weak = {**_poi("weak", "Old Marker", _point(50.053, 10.051)), "category": "memorial",
            "provider_tags": {"historic": "memorial"}}
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway, unused=(weak,))
    for stop in state.experience_plan.daily_plans[1].experiences:
        stop.quality_tier = "primary_anchor"
    assert _repair(state, service).attempts[0].reason == "no_suitable_candidate"


# 8. already >= 90% factual routing
def test_the_repair_does_not_run_when_coverage_already_meets_the_threshold() -> None:
    # fully routed
    gateway = _RejectingGateway(unroutable=())
    state, service = _routed_state(gateway)
    assert routing_coverage(state) == 1.0 and _repair(state, service) is None and gateway.calls == []

    # exactly 90%: nine routed legs and one failed leg
    grid = [[_poi(f"g{day}{index}", f"Museum {day}{index}", _point(50.2 + 0.1 * day + 0.002 * index, 10.2))
             for index in range(4)] for day in range(3)]
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway, [*grid, [_D, _X]])
    assert routing_coverage(state) == pytest.approx(0.9)
    assert _repair(state, service) is None and gateway.calls == []
    apply_routability_repair_safely(state, service)
    assert state.routability_repair_report is None
    assert _names(state, 3) == ["Museum Delta", "Museum Unroutable"]


# 9. deterministic
def test_identical_input_gives_an_identical_replacement() -> None:
    outcomes = []
    for _ in range(3):
        gateway = _RejectingGateway()
        # two equally good candidates: the choice must not depend on anything but the input
        state, service = _routed_state(gateway, unused=(_NEAR_TWO, _NEAR))
        report = _repair(state, service)
        outcomes.append(
            (
                [_names(state, 0), _names(state)],
                report.attempts[0].model_dump(exclude={"generated_at"}),
                [(leg.from_experience_name, leg.to_experience_name, leg.distance_meters) for leg in state.route_feasibility_report.legs],
                gateway.calls,
            )
        )
    assert outcomes[0] == outcomes[1] == outcomes[2]
    assert outcomes[0][1]["accepted"] is True


# 10. the provider allowance stays enforced
def test_the_route_request_allowance_bounds_the_repair() -> None:
    # nothing left: not a single request is made
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway)
    context = GenerationProviderContext.new(trip_days=2)
    context.route_requests_left = 0
    attempt = _repair(state, service, context).attempts[0]
    assert attempt.reason == "route_budget_exhausted" and gateway.calls == [] and context.route_requests_left == 0
    assert _names(state) == ["Museum Delta", "Museum Unroutable", "Museum Epsilon"]

    # enough to localise, not to route a replacement: nothing is replaced
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway)
    context = GenerationProviderContext.new(trip_days=2)
    context.route_requests_left = 2
    attempt = _repair(state, service, context).attempts[0]
    assert attempt.reason == "route_budget_exhausted" and len(gateway.calls) == 2 and context.route_requests_left == 0
    assert _names(state) == ["Museum Delta", "Museum Unroutable", "Museum Epsilon"]

    # the whole repair of one day costs at most three requests
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway)
    context = GenerationProviderContext.new(trip_days=2)
    available = context.route_requests_left
    assert _repair(state, service, context).attempts[0].accepted
    assert available - context.route_requests_left == len(gateway.calls) == 3


def test_at_most_one_replacement_per_day_and_one_attempt_per_failed_day() -> None:
    y_point = (50.004, 10.000)
    gateway = _RejectingGateway(unroutable=(_X_POINT, y_point))
    near_day_one = _poi("n1", "Museum Close", _point(50.003, 10.001))
    state, service = _routed_state(gateway, unused=(_NEAR, near_day_one))
    assert routing_coverage(state) == 0.0

    report = _repair(state, service)

    assert [(attempt.day_number, attempt.accepted) for attempt in report.attempts] == [(1, True), (2, True)]
    assert [attempt.replaced_place for attempt in report.attempts] == ["Museum Gamma", "Museum Unroutable"]
    assert report.coverage_after == 1.0
    assert len({name for day in (0, 1) for name in _names(state, day)}) == 6  # no place scheduled twice


def test_a_failure_that_is_not_about_a_stop_never_triggers_a_replacement() -> None:
    for reason, expected in (
        ("timeout", "transient_failure"), ("rate_limited", "transient_failure"), ("server", "transient_failure"),
        (errors.REASON_MALFORMED_REQUEST, "malformed_request"),
    ):
        gateway = _RejectingGateway(reason=reason)
        state, service = _routed_state(gateway)
        attempt = _repair(state, service).attempts[0]
        assert (attempt.reason, attempt.accepted) == (expected, False)
        assert gateway.calls == [] and _names(state) == ["Museum Delta", "Museum Unroutable", "Museum Epsilon"]


def test_an_unexpected_repair_error_never_breaks_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _RejectingGateway()
    state, service = _routed_state(gateway)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("a bug")

    monkeypatch.setattr(RoutabilityRepairService, "_repair_day", broken)
    apply_routability_repair_safely(state, service)
    assert state.routability_repair_report is None
    assert _names(state) == ["Museum Delta", "Museum Unroutable", "Museum Epsilon"]


# =====================================================================================
# C1. What an HTTP 400 meant: a fixed reason code, never the provider's text
# =====================================================================================


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("No suitable edges near location", "unroutable_endpoint"),
        ("Location is unreachable", "unroutable_endpoint"),
        ("No path could be found for input", "no_route"),
        ("Path distance exceeds the max distance limit", "no_route"),
        ("Invalid coordinate: latitude out of range", "invalid_coordinates"),
        ("Insufficient number of locations provided", "invalid_coordinates"),
        ('"waypoints" is required', "malformed_request"),
        ('"mode" must be one of [drive, walk]', "malformed_request"),
        ("Something this code has never seen", "provider_bad_request"),
        ("", "provider_bad_request"), (None, "provider_bad_request"), (42, "provider_bad_request"),
    ],
)
def test_a_bad_request_message_is_classified_into_a_fixed_code(message: Any, reason: str) -> None:
    assert classify_bad_request(message) == reason and reason in errors.BAD_REQUEST_REASONS


_SECRET_TEXT = "No suitable edges near location 50.052,10.052 SENTINEL_PROVIDER_TEXT"


def _client(status: int, body: Any) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body) if not isinstance(body, bytes) else httpx.Response(status, content=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _get(client: httpx.Client) -> dict[str, Any]:
    return geoapify_client.geoapify_get(
        client, base_url="https://provider.invalid", path="/v1/routing", params={"waypoints": "1,2|3,4"},
        api_key="SENTINEL_3C2_KEY", timeout=5.0, api="routing", usage=None,
    )


def test_the_client_reports_a_reason_code_and_never_the_response_text() -> None:
    geoapify_client.request_breaker.reset()
    with pytest.raises(ProviderRequestError) as raised:
        _get(_client(400, {"statusCode": 400, "error": "Bad Request", "message": _SECRET_TEXT}))
    error = raised.value
    assert (error.kind, error.reason) == ("bad_request", "unroutable_endpoint")
    assert "SENTINEL" not in str(error) and "50.052" not in str(error) and str(error) == "The provider rejected the request."

    for body, reason in (
        ({"message": "No path could be found for input"}, "no_route"),
        ({"message": ['"waypoints" is required']}, "malformed_request"),
        ({"error": "Bad Request"}, "provider_bad_request"),
        ([1, 2, 3], "provider_bad_request"),
        (b"<html>not json</html>", "provider_bad_request"),
    ):
        with pytest.raises(ProviderRequestError) as raised:
            _get(_client(400, body))
        assert (raised.value.kind, raised.value.reason) == ("bad_request", reason)
    # other failure kinds carry no reason, and an unknown reason is never stored
    with pytest.raises(ProviderRequestError) as raised:
        _get(_client(500, {"message": "No suitable edges near location"}))
    assert (raised.value.kind, raised.value.reason) == ("server", None)
    assert ProviderRequestError("bad_request", "anything the provider said").reason is None


def test_the_routing_adapter_carries_the_reason_onto_each_leg_and_logs_fixed_fields_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GEOAPIFY_API_KEY", "SENTINEL_3C2_KEY")
    get_settings.cache_clear()
    geoapify_client.request_breaker.reset()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"statusCode": 400, "error": "Bad Request", "message": _SECRET_TEXT})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    adapter = GeoapifyRoutingAdapter(cache_store=ProviderCacheStore(tmp_path / "cache.sqlite3"))

    with caplog.at_level(logging.WARNING, logger=routing_module.logger.name):
        results = adapter.get_route_sequence([(50.050, 10.050), (50.052, 10.052), (50.054, 10.050)])

    # the request itself is well formed: latitude,longitude pairs in order, joined by "|", walking mode
    assert seen[0].url.params["waypoints"] == "50.05,10.05|50.052,10.052|50.054,10.05"
    assert seen[0].url.params["mode"] == "walk"
    # ONE rejected request fails BOTH legs, each with the same fixed reason
    assert [(result.status, result.failure_reason) for result in results] == [
        (ProviderStatus.FAILED, "unroutable_endpoint")
    ] * 2
    assert all(result.distance_meters is None and result.duration_seconds is None for result in results)
    messages = [record.getMessage() for record in caplog.records if record.name == routing_module.logger.name]
    assert messages == [
        "Routing request failed (provider=geoapify_routing, kind=bad_request, reason=unroutable_endpoint, "
        "mode=walk, waypoints=3)."
    ]
    serialized = " ".join(messages) + " ".join(result.model_dump_json() for result in results)
    for forbidden in ("SENTINEL", "50.052", "apiKey", "http", "No suitable edges"):
        assert forbidden not in serialized

    # the reason reaches the stored leg through the real feasibility service
    state = _state([_D, _X, _E], [[_D, _X, _E]])

    class _AdapterGateway:
        routing = adapter

        def get_route_sequence(self, points: Any, provider_context: Any = None) -> Any:
            return adapter.get_route_sequence(points)

    report = RouteFeasibilityService(_AdapterGateway()).build_report(state)  # type: ignore[arg-type]
    assert [(leg.status, leg.failure_reason) for leg in report.legs] == [(ProviderStatus.FAILED, "unroutable_endpoint")] * 2
