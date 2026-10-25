"""Final Batch 1 (D): a heritage site and the collection housed at it are one
visit. Synthetic fixtures only (no place or destination name is read)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.models.planning_state import DestinationContext, PlanningState, TravelGroupType, TripPace, TripRequest
from app.services import experience_planner_service as planner
from app.services import same_site
from app.services.candidate_quality_service import CandidateQualityService
from app.services.experience_planner_service import ExperiencePlannerService

START = date(2026, 8, 11)  # a Tuesday
METRE = 1 / 111_000  # about one metre of latitude


def _poi(pid: str, tags: dict[str, str], lat: float = 0.0, lng: float = 0.0, **extra: Any) -> dict[str, Any]:
    return {"place_id": pid, "name": f"Place {pid}", "category": None, "provider_tags": tags,
            "coordinates": {"lat": lat, "lng": lng}, "source": "fixture", "data_status": "live",
            "confidence": 0.6, **extra}


CASTLE = {"historic": "castle"}
CHURCH = {"amenity": "place_of_worship"}
MUSEUM = {"tourism": "museum"}


@pytest.mark.parametrize(
    ("first", "second", "metres", "expected"),
    [
        (CASTLE, MUSEUM, 9, True),  # a collection at the site
        (CHURCH, MUSEUM, 19, True),
        (MUSEUM, MUSEUM, 5, False),  # two museums in one building stay two experiences
        (CASTLE, CHURCH, 5, False),  # two sites side by side stay two
        (CASTLE, MUSEUM, 200, False),  # down the street: not co-located
        ({"historic": "castle", "tourism": "museum"}, MUSEUM, 5, False),  # a record that is both has no role
        ({"historic": "monument"}, MUSEUM, 5, False),  # a monument does not house a collection
    ],
)
def test_one_site_needs_both_roles_and_co_location(first: dict, second: dict, metres: float, expected: bool) -> None:
    assert same_site.one_site(_poi("a", first), _poi("b", second, metres * METRE)) is expected


def test_groups_are_written_on_both_records_and_are_deterministic() -> None:
    pool = [_poi("site", CASTLE), _poi("coll", MUSEUM, 9 * METRE), _poi("other", MUSEUM, 0.01)]
    same_site.assign_same_site_groups(pool)
    assert pool[0][same_site.SAME_SITE_KEY] == pool[1][same_site.SAME_SITE_KEY] == "site:site"
    assert same_site.SAME_SITE_KEY not in pool[2]
    promoted = _poi("promoted", MUSEUM, 5 * METRE)
    assert same_site.group_for(promoted, pool) == "site:site"  # read against the pool, which is not changed


_TIER_NAMES = {4: "primary_anchor", 3: "good_candidate", 2: "secondary_candidate"}


def _profiles(pool: list[dict], tiers: dict[str, int]) -> dict[int, Any]:
    """Profiles built from each record's quality tier, so the usefulness
    ordering and the tier guard both see it."""
    for poi in pool:
        poi["quality_tier"] = _TIER_NAMES[tiers.get(poi["place_id"], 3)]
    return {id(poi): planner._candidate_profile(poi, None, []) for poi in pool}


def _separate(days: list[list[dict]], pool: list[dict], *, tiers: dict[str, int] | None = None,
              must: set[str] = frozenset()) -> list[list[str]]:
    same_site.assign_same_site_groups(pool)
    profiles = _profiles(pool, tiers or {})
    must_ids = {id(poi) for poi in pool if poi["place_id"] in must}
    separated, _ = planner._separate_suspected_duplicates(days, pool, profiles, must_ids,
                                                          group_key=same_site.SAME_SITE_KEY)
    return [[poi["place_id"] for poi in day] for day in separated]


def test_a_model_chosen_pair_keeps_one_record_and_refills_the_slot() -> None:
    site, coll = _poi("site", CASTLE), _poi("coll", MUSEUM, 9 * METRE)  # the site is the higher tier
    near = _poi("near", MUSEUM, 0.002)
    other = _poi("other", MUSEUM, 0.001)
    pool = [site, coll, near, other]
    days = _separate([[site, other], [coll]], pool, tiers={"site": 4, "coll": 3, "near": 3})
    assert days == [["site", "other"], ["near"]]  # the more useful site stays; the slot gets a real place


def test_a_must_visit_record_is_the_one_kept() -> None:
    site, coll, other = _poi("site", CASTLE), _poi("coll", MUSEUM, 9 * METRE), _poi("other", MUSEUM, 0.001)
    days = _separate([[site, coll, other]], [site, coll, other], tiers={"site": 4, "coll": 2}, must={"coll"})
    assert days == [["coll", "other"]]


def test_no_filler_and_no_empty_day() -> None:
    site, coll = _poi("site", CASTLE), _poi("coll", MUSEUM, 9 * METRE)
    weak = _poi("weak", MUSEUM, 0.002)
    # the only refill is two tiers lower: not used; the record alone on its day stays
    days = _separate([[site], [coll]], [site, coll, weak], tiers={"site": 4, "coll": 4, "weak": 2})
    assert days == [["site"], ["coll"]]


def test_the_deterministic_planner_schedules_one_record_of_a_site() -> None:
    pool = [_poi("site", CASTLE), _poi("coll", MUSEUM, 9 * METRE)]
    pool += [_poi(f"m{i}", MUSEUM, (i + 1) * 0.001) for i in range(5)]
    same_site.assign_same_site_groups(pool)
    state = PlanningState(trip_request=TripRequest(
        primary_destination="Testville", start_date=START, end_date=START + timedelta(days=1), travelers_count=2,
        travel_group_type=TravelGroupType.COUPLE, pace=TripPace.BALANCED,
    ))
    state.destination_context = DestinationContext(destination_name="Testville", candidate_pois=pool)
    state.candidate_quality_report = CandidateQualityService().build_report(state)
    ExperiencePlannerService().run(state)
    scheduled = {e.provider_place_id for d in state.experience_plan.daily_plans for e in d.experiences}
    assert len(scheduled) >= 4 and not {"site", "coll"} <= scheduled
