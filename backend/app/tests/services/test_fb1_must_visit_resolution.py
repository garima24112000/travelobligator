"""Final Batch 1 (A): must-visit reliability.

Synthetic fixtures only (no destination or landmark name, no provider call):
the named lookup reads several results under the unchanged acceptance rule and
says why it accepted none; a pool place is found by any name of its own record;
a place outside the destination is planned only as a VERIFIED excursion;
satisfaction is reconciled against the final itinerary by identity; and every
requested place has one explicit status."""

from __future__ import annotations

from typing import Any

import pytest

from app.models.common import DataStatus, GeoPoint, ProviderStatus
from app.models.planning_state import MustVisitLookup
from app.models.providers import NormalizedPlace, ProviderResponse
from app.models.routing import RouteResult, RoutingProfile
from app.providers.base import PlacesProvider, failed_response, unavailable_response
from app.providers.geocoding import base as geocoding
from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
from app.providers.places.destination_resolution import ExcursionLookup
from app.services import must_visit_matching as matching
from app.services import must_visit_status as status
from app.services.destination_context_service import DestinationContextService
from app.services.plan_validator_service import PlanValidatorService
from app.tests.services.test_final_quality_correction_203c2b import _NEAR_A, _NEAR_B, _poi, _point, _state

DESTINATION = "Fixtureville, Fixtureland"
CENTRE = GeoPoint(lat=50.0, lng=10.0)


# -- the named lookup: several results, the same strict rule, a reason when nothing is taken ----


def _result(result_type: str, *, match: str = "full_match", confidence: float = 0.9, key: str = "x") -> dict:
    return {"place_id": key, "lat": 50.0, "lon": 10.0, "name": f"Name {key}", "result_type": result_type,
            "rank": {"match_type": match, "confidence": confidence}, "category": "entertainment.museum"}


def _geocoder(results: list[dict]) -> GeoapifyGeocoder:
    geocoder = GeoapifyGeocoder.__new__(GeoapifyGeocoder)
    geocoder._results = lambda client, params, limit: results[:limit]  # type: ignore[method-assign]
    return geocoder


def test_a_specific_place_below_a_street_fallback_is_taken() -> None:
    lookup = _geocoder([_result("street", key="s"), _result("amenity", key="m")]).lookup_named_place(None, "q")
    assert lookup.hit is not None and lookup.hit.provider_place_id == "geoapify/m" and lookup.rejection is None


@pytest.mark.parametrize(
    ("results", "reason"),
    [
        ([], geocoding.NAMED_PLACE_NO_RESULT),
        ([_result("street"), _result("city")], geocoding.NAMED_PLACE_NOT_SPECIFIC),
        ([_result("amenity", match="match_by_street")], geocoding.NAMED_PLACE_WEAK_MATCH),
        ([_result("amenity", confidence=0.2)], geocoding.NAMED_PLACE_LOW_CONFIDENCE),
    ],
)
def test_nothing_acceptable_names_the_reason_of_the_best_answer(results: list[dict], reason: str) -> None:
    lookup = _geocoder(results).lookup_named_place(None, "q")
    assert lookup.hit is None and lookup.rejection == reason


def test_the_acceptance_rule_itself_is_unchanged() -> None:
    # a low-confidence or weakly matched answer is still never taken, wherever it ranks
    weak = [_result("amenity", confidence=0.49, key="a"), _result("amenity", match="partial", key="b")]
    assert _geocoder(weak).lookup_named_place(None, "q").hit is None


# -- the pool: any name of the record's own, equality only -------------------------------------


def test_a_pool_place_is_found_by_another_name_of_its_own_record() -> None:
    local = _poi("loc", "Galleria Locale", _point(50.0, 10.0))
    found = matching.exact_name_candidates([local], "Local Gallery", {"geoapify/loc": ["Local Gallery"]})
    assert found == [local]
    assert matching.exact_name_candidates([local], "Local Gallery") == []  # not without the record's names
    # still equality: a longer name never matches
    assert matching.exact_name_candidates([local], "Local", {"geoapify/loc": ["Local Gallery"]}) == []


class _Places:
    """A places provider: a targeted lookup that answers what it is told, and
    the wider lookup; records what it was asked."""

    provider_name = "fixture_places"
    provider_type = PlacesProvider.provider_type

    def __init__(self, lookup: dict[str, ProviderResponse[Any]] | None = None,
                 wide: dict[str, ExcursionLookup] | None = None) -> None:
        self.lookup, self.wide = lookup or {}, wide or {}
        self.calls: list[tuple[str, str]] = []

    def search_must_visit_place(self, term: str, destination: str, filters: Any = None) -> ProviderResponse[Any]:
        self.calls.append(("lookup", term))
        return self.lookup.get(term) or unavailable_response(self.provider_name, self.provider_type).model_copy(
            update={"failure_reason": geocoding.NAMED_PLACE_NO_RESULT}
        )

    def search_must_visit_excursion(self, term: str, destination: str) -> ExcursionLookup:
        self.calls.append(("wide", term))
        return self.wide.get(term, ExcursionLookup(rejection=geocoding.NAMED_PLACE_NO_RESULT, origin=CENTRE))


class _Routing:
    def __init__(self, seconds: float | None) -> None:
        self.seconds, self.profiles = seconds, []

    def get_route_sequence(self, points: list, profile: Any = None) -> list[RouteResult]:
        self.profiles.append(profile)
        if self.seconds is None:
            return [RouteResult(provider="fixture", status=ProviderStatus.UNAVAILABLE, source="fixture")]
        return [RouteResult(provider="fixture", status=ProviderStatus.SUCCESS, duration_seconds=self.seconds,
                            distance_meters=self.seconds * 15, source="fixture")]


class _Gateway:
    def __init__(self, routing: _Routing) -> None:
        self.routing = routing

    def routing_for(self, provider_context: Any = None) -> _Routing:
        return self.routing


def _place(key: str, lat: float = 50.0, lng: float = 10.0) -> NormalizedPlace:
    return NormalizedPlace(place_id=f"geoapify/{key}", name=f"Place {key}", category="museum",
                           coordinates=GeoPoint(lat=lat, lng=lng), source="geoapify", data_status=DataStatus.LIVE,
                           provider_tags={"tourism": "museum"})


def _ground(pool: list[dict], terms: list[str], places: _Places, *, routing: _Routing | None = None,
            other_names: dict | None = None) -> tuple[list[dict], list[str], list[MustVisitLookup]]:
    pois = [dict(poi) for poi in pool]
    state = _state(pois, [[]], must_visit=terms)
    records: list[MustVisitLookup] = []
    service = DestinationContextService(gateway=_Gateway(routing or _Routing(None)))  # type: ignore[arg-type]
    pois, ungrounded = service._append_must_visit_candidates(
        state, DESTINATION, pois, places=places, other_names=other_names, lookups=records
    )
    return pois, ungrounded, records


def test_a_pool_match_by_another_name_needs_no_lookup() -> None:
    local = _poi("loc", "Galleria Locale", _point(50.0, 10.0))
    places = _Places()
    pois, ungrounded, records = _ground([local], ["Local Gallery"], places,
                                        other_names={"geoapify/loc": ["Local Gallery"]})
    assert ungrounded == [] and places.calls == []
    assert matching.grounded_terms(pois[0]) == ["Local Gallery"]
    assert records == [MustVisitLookup(term="Local Gallery", outcome=matching.FOUND_BY_POOL_OTHER_NAME,
                                       place_id="geoapify/loc")]


def test_two_pool_places_with_that_name_are_ambiguous_and_the_lookup_decides() -> None:
    a, b = _poi("a", "Twin", _point(50.0, 10.0)), _poi("b", "Twin", _point(50.01, 10.0))
    places = _Places()
    _, ungrounded, _ = _ground([a, b], ["Twin"], places)
    assert ("lookup", "Twin") in places.calls and ungrounded == ["Twin"]


def test_a_wider_lookup_inside_the_destination_grounds_the_place() -> None:
    places = _Places(wide={"Hidden": ExcursionLookup(place=_place("h"), origin=CENTRE, outside_destination=False)})
    pois, ungrounded, records = _ground([_NEAR_A], ["Hidden"], places)
    assert ungrounded == [] and matching.grounded_terms(pois[-1]) == ["Hidden"]
    assert records[0].outcome == matching.FOUND_BY_WIDE_LOOKUP
    assert matching.OUTSIDE_DESTINATION_KEY not in pois[-1]


def _outside(seconds: float | None) -> tuple[list[dict], list[str], list[MustVisitLookup], _Routing]:
    routing = _Routing(seconds)
    far = ExcursionLookup(place=_place("far", 50.3, 10.0), origin=CENTRE, outside_destination=True, distance_km=33.4)
    pois, ungrounded, records = _ground([_NEAR_A], ["Far Site"], _Places(wide={"Far Site": far}), routing=routing)
    return pois, ungrounded, records, routing


def test_an_outside_place_with_a_verified_route_within_the_limit_is_a_planned_excursion() -> None:
    pois, ungrounded, records, routing = _outside(1800.0)  # the limit is ROUTE_BURDEN_MAX_DRIVE_LEG_SECONDS (2700)
    assert ungrounded == [] and routing.profiles == [RoutingProfile.DRIVING]
    excursion = pois[-1]
    assert excursion[matching.OUTSIDE_DESTINATION_KEY] is True and matching.grounded_terms(excursion) == ["Far Site"]
    assert excursion["excursion_drive_seconds"] == 1800.0 and excursion["excursion_distance_km"] == 33.4
    assert records[0].outcome == matching.EXCURSION_VERIFIED


@pytest.mark.parametrize(("seconds", "outcome"),
                         [(3600.0, matching.EXCURSION_TOO_FAR), (None, matching.EXCURSION_ROUTE_UNVERIFIED)])
def test_an_outside_place_without_a_feasible_verified_route_is_not_planned(seconds: float | None, outcome: str) -> None:
    pois, ungrounded, records, _ = _outside(seconds)
    assert ungrounded == ["Far Site"] and len(pois) == 1  # nothing appended, nothing substituted
    assert records[0].outcome == outcome and records[0].distance_km == 33.4


def test_a_provider_failure_never_triggers_the_wider_lookup() -> None:
    failed = failed_response("fixture_places", PlacesProvider.provider_type)
    places = _Places(lookup={"X": failed})
    _, ungrounded, records = _ground([_NEAR_A], ["X"], places)
    assert ("wide", "X") not in places.calls and ungrounded == ["X"]
    assert records[0].outcome == matching.UNRESOLVED


# -- reconciliation against the final itinerary ---------------------------------------------------


def test_a_scheduled_record_of_the_same_entity_satisfies_the_term() -> None:
    grounded = _poi("g", "Record One", _point(50.0, 10.0), provider_tags={"tourism": "museum", "wikidata": "Q1"},
                    must_visit_terms=["Requested"], must_visit_term="Requested")
    other = _poi("o", "Record Two", _point(50.003, 10.0), provider_tags={"tourism": "museum", "wikidata": "Q1"})
    state = _state([grounded, other, _NEAR_B], [[other, _NEAR_B]], must_visit=["Requested"])
    state.destination_context.must_visit_grounding_version = matching.MUST_VISIT_GROUNDING_VERSION
    (resolution,) = matching.resolve_must_visits(state)
    assert resolution.scheduled and resolution.scheduled_place_ids == frozenset({"geoapify/o"})
    PlanValidatorService().run(state)
    assert not [i for i in state.validation_report.warnings if i.category == "must_visit"]  # never reported missing
    assert state.validation_report.must_visit_status[0].status == status.SCHEDULED


@pytest.mark.parametrize(("wikidata", "lat"), [("Q2", 50.003), ("Q1", 50.05)])  # another entity / too far apart
def test_a_different_or_distant_entity_does_not(wikidata: str, lat: float) -> None:
    grounded = _poi("g", "Record One", _point(50.0, 10.0), provider_tags={"tourism": "museum", "wikidata": "Q1"},
                    must_visit_terms=["Requested"], must_visit_term="Requested")
    other = _poi("o", "Record Two", _point(lat, 10.0), provider_tags={"tourism": "museum", "wikidata": wikidata})
    state = _state([grounded, other], [[other]], must_visit=["Requested"])
    state.destination_context.must_visit_grounding_version = matching.MUST_VISIT_GROUNDING_VERSION
    assert not matching.resolve_must_visits(state)[0].scheduled


# -- one explicit status per requested place, in traveller wording -----------------------------


def _statuses(pois: list[dict], days: list[list[dict]], terms: list[str], lookups: list[MustVisitLookup]):
    state = _state(pois, days, must_visit=terms)
    state.destination_context.must_visit_grounding_version = matching.MUST_VISIT_GROUNDING_VERSION
    state.destination_context.must_visit_lookups = lookups
    PlanValidatorService().run(state)
    return state


def test_each_requested_place_has_a_status_and_only_what_is_missing_is_a_finding() -> None:
    on_plan = _poi("p", "On Plan", _point(50.0, 10.0), must_visit_terms=["On Plan"], must_visit_term="On Plan")
    closed = _poi("c", "Closed One", _point(50.001, 10.0), venue_tags={"opening_hours": "off"},
                  must_visit_terms=["Closed One"], must_visit_term="Closed One")
    excursion = _poi("e", "Far Site", _point(50.3, 10.0), must_visit_terms=["Far Site"], must_visit_term="Far Site",
                     outside_destination=True, excursion_drive_seconds=1800.0, excursion_distance_km=33.4)
    lookups = [
        MustVisitLookup(term="Far Site", outcome=matching.EXCURSION_VERIFIED, place_id="geoapify/e",
                        distance_km=33.4, excursion_drive_seconds=1800.0),
        MustVisitLookup(term="Too Far", outcome=matching.EXCURSION_TOO_FAR, distance_km=140.0,
                        excursion_drive_seconds=7300.0),
        MustVisitLookup(term="Unseen", outcome=matching.EXCURSION_ROUTE_UNVERIFIED, distance_km=40.0),
        MustVisitLookup(term="Nowhere", outcome=matching.UNRESOLVED, rejection=geocoding.NAMED_PLACE_NOT_SPECIFIC),
    ]
    state = _statuses([on_plan, closed, excursion, _NEAR_B], [[on_plan, closed], [excursion]],
                      ["On Plan", "Closed One", "Far Site", "Too Far", "Unseen", "Nowhere"], lookups)
    report = state.validation_report
    assert [(e.term, e.status) for e in report.must_visit_status] == [
        ("On Plan", status.SCHEDULED), ("Closed One", status.FOUND_INACCESSIBLE),
        ("Far Site", status.SCHEDULED_EXCURSION), ("Too Far", status.OUTSIDE_NOT_FEASIBLE),
        ("Unseen", status.OUTSIDE_UNVERIFIED), ("Nowhere", status.UNRESOLVED),
    ]
    excursion_entry = report.must_visit_status[2]
    assert (excursion_entry.day_number, excursion_entry.drive_minutes) == (2, 30)
    (finding,) = [i for i in report.warnings if i.category == "must_visit"]
    for text in ("Too Far is outside Fixtureville, about 140 km away", "about 122 minutes each way by road",
                 "Unseen is outside Fixtureville", "could not be checked", "no matching place was found for Nowhere",
                 "not replaced with unrelated attractions"):
        assert text in finding.message
    for scheduled in ("On Plan;", "Closed One", "Far Site"):
        assert scheduled not in finding.message  # a scheduled request is never reported missing
    for word in ("provider", "grounded", "configured", "inventory"):
        assert word not in finding.message
    assert "MUST_VISIT" in report.review_codes


def test_the_excursion_day_states_the_verified_road_time_and_nothing_else() -> None:
    excursion = _poi("e", "Far Site", _point(50.3, 10.0), must_visit_terms=["Far Site"], must_visit_term="Far Site",
                     outside_destination=True, excursion_drive_seconds=1800.0, excursion_distance_km=33.4)
    pool = [_poi(f"n{i}", f"Near {i}", _point(50.0 + i * 0.001, 10.0)) for i in range(6)]
    lookups = [MustVisitLookup(term="Far Site", outcome=matching.EXCURSION_VERIFIED, place_id="geoapify/e",
                               distance_km=33.4, excursion_drive_seconds=1800.0)]
    state = _statuses([*pool, excursion], [pool[:2], [excursion]], ["Far Site"], lookups)
    (remote,) = [i for i in state.validation_report.warnings if i.category == "remote_day_access"]
    assert "Far Site is outside Fixtureville, about 33 km away" in remote.message
    assert "about 30 minutes each way from the centre of Fixtureville" in remote.message
    assert "how you travel there is not arranged or checked" in remote.message
    assert "MUST_VISIT" not in state.validation_report.review_codes


def test_reports_stored_before_this_load_unchanged() -> None:
    from app.models.planning_state import DestinationContext, ValidationReport

    assert ValidationReport.model_validate({"readiness_status": "needs_review"}).must_visit_status is None
    assert DestinationContext(destination_name="x").must_visit_lookups == []
