from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models.common import ReadinessStatus, ValidationSeverity
from app.models.planning_state import PlanningState
from app.providers.places.geoapify_categories import taxonomy_tags_from_categories
from app.repositories.planning_state_repository import PlanningStateRepository
from app.services import review_classification as classification
from app.services.plan_validator_service import PlanValidatorService
from app.storage.local_json_store import LocalJsonStore
from app.tests.services.test_candidate_usefulness_q2 import _museum, _planned, _state
from app.tests.services.test_final_quality_correction_203c2b import _poi, _point

# Remediation R2a: a grounded must-visit whose provider tags EXPLICITLY mark it
# private / not open to the public is kept (a must-visit is never removed) and
# the traveller is told so on its day. Synthetic, provider-shaped fixtures; no
# city or landmark name.

_CATEGORY = "must_visit_access_restricted"
_CODE = _CATEGORY.upper()


def _pool(**must_visit_tags: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Twelve ordinary museums and one requested place with the given extra tags."""
    pool = [_museum(f"m{index}", index) for index in range(12)]
    requested = _museum("req", 12, **must_visit_tags)
    return [*pool, requested], requested


def _validated(**must_visit_tags: str) -> tuple[PlanningState, dict[str, Any]]:
    pois, requested = _pool(**must_visit_tags)
    state = _planned(_state(pois, must_visit={"the requested museum": requested}))
    PlanValidatorService().run(state)
    return state, requested


def _day_of(state: PlanningState, place_id: str) -> int | None:
    for day in state.experience_plan.daily_plans:
        if any(experience.provider_place_id == place_id for experience in day.experiences):
            return day.day_number
    return None


def _findings(state: PlanningState) -> list[Any]:
    return [issue for issue in state.validation_report.warnings if issue.category == _CATEGORY]


@pytest.mark.parametrize(
    ("access", "wording"),
    [("private", "is marked in the place data as private"), ("no", "is marked in the place data as not open to the public")],
)
def test_an_explicitly_inaccessible_must_visit_is_kept_and_disclosed_on_its_day(access: str, wording: str) -> None:
    state, requested = _validated(access=access)

    day = _day_of(state, requested["place_id"])
    assert day is not None  # kept, by its own provider identity -- nothing substituted
    findings = _findings(state)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity == ValidationSeverity.WARNING
    assert finding.affected_section == f"experience_plan.daily_plans[{day}]"
    assert finding.message.startswith(f"Day {day}: Museum req {wording}.")
    assert "do not rely on being able to visit it" in finding.message
    assert "provider" not in finding.message.lower()  # traveller wording
    report = state.validation_report
    assert _CODE in report.review_codes and report.readiness_status == ReadinessStatus.NEEDS_REVIEW
    assert report.review_code_classification[_CODE] == classification.MATERIAL


def test_the_provider_no_access_category_reaches_the_disclosure() -> None:
    """The adapter's rename of Geoapify's own `no_access` category is enough."""
    tags = taxonomy_tags_from_categories(["entertainment", "entertainment.museum", "no_access"])
    assert tags["access"] == "no"
    state, requested = _validated(access=tags["access"])
    assert _day_of(state, requested["place_id"]) is not None and len(_findings(state)) == 1


def test_an_ordinary_inaccessible_place_is_still_excluded_and_raises_nothing() -> None:
    pois = [_museum(f"m{index}", index) for index in range(12)]
    private = _museum("priv", 12, access="private")
    state = _planned(_state([*pois, private]))
    PlanValidatorService().run(state)

    assert _day_of(state, private["place_id"]) is None
    score = next(s for s in state.candidate_quality_report.attraction_scores if s.candidate_id == private["place_id"])
    assert score.reject_reasons  # unchanged: still unsuitable
    assert _findings(state) == []


@pytest.mark.parametrize("tags", [{}, {"access": "yes"}, {"access": "permissive"}, {"access": "customers"}, {"access": ""}])
def test_missing_or_other_access_information_raises_nothing(tags: dict[str, str]) -> None:
    state, requested = _validated(**tags)
    assert _day_of(state, requested["place_id"]) is not None
    assert _findings(state) == []
    assert _CODE not in state.validation_report.review_codes


def test_nothing_is_inferred_from_a_name_or_a_category() -> None:
    pois = [_museum(f"m{index}", index) for index in range(12)]
    requested = _museum("closed", 12)
    requested["name"] = "Private Closed No Access Collection"
    requested["category"] = "private"
    state = _planned(_state([*pois, requested], must_visit={"the collection": requested}))
    PlanValidatorService().run(state)

    assert _day_of(state, requested["place_id"]) is not None
    assert _findings(state) == []


def test_the_disclosure_needs_the_must_visit_identity_not_just_the_tag() -> None:
    """A scheduled stop that is not a grounded must-visit never gets this
    finding, even if its stored tags say private (only must-visits are kept)."""
    state, requested = _validated()
    other = next(
        experience
        for day in state.experience_plan.daily_plans
        for experience in day.experiences
        if experience.provider_place_id != requested["place_id"]
    )
    for poi in state.destination_context.candidate_pois:
        if poi["place_id"] == other.provider_place_id:
            poi["provider_tags"] = {**poi["provider_tags"], "access": "private"}
    PlanValidatorService().run(state)
    assert _findings(state) == []


def test_the_plan_is_the_same_with_and_without_the_access_tag() -> None:
    """Disclosure only: the scheduled places and days do not change."""
    with_tag, _ = _validated(access="private")
    without_tag, _ = _validated()

    def layout(state: PlanningState) -> list[list[str | None]]:
        return [[e.provider_place_id for e in day.experiences] for day in state.experience_plan.daily_plans]

    assert layout(with_tag) == layout(without_tag)


def _clustered(key: str, cluster: int, index: int, **tags: str) -> dict[str, Any]:
    """A museum in one of three compact clusters about 4.5 km apart."""
    point = _point(50.0 + 0.04 * cluster + 0.002 * index, 10.0 + 0.002 * index)
    return _poi(key, f"Museum {key}", point, provider_tags={"tourism": "museum", **tags})


def _three_must_visits(spread: bool) -> tuple[PlanningState, list[dict[str, Any]]]:
    """Must-visits alpha (private), beta (no access) and gamma (untagged):
    one per cluster when `spread`, otherwise side by side."""
    if spread:
        pois = [_clustered(f"c{cluster}m{index}", cluster, index) for cluster in range(3) for index in range(1, 4)]
        alpha, gamma, beta = (
            _clustered("alpha", 0, 0, access="private"), _clustered("gamma", 1, 0), _clustered("beta", 2, 0, access="no"),
        )
    else:
        pois = [_museum(f"m{index}", index) for index in range(12)]
        alpha, beta, gamma = _museum("alpha", 12, access="private"), _museum("beta", 13, access="no"), _museum("gamma", 14)
    state = _planned(
        _state(
            [*pois, alpha, beta, gamma],
            must_visit={"the alpha museum": alpha, "the beta museum": beta, "the gamma museum": gamma},
        )
    )
    PlanValidatorService().run(state)
    return state, [alpha, beta, gamma]


@pytest.mark.parametrize("spread", [True, False], ids=["different-days", "same-day"])
def test_several_must_visits_are_named_on_their_own_days(spread: bool) -> None:
    """Each restricted must-visit is named on exactly the day that holds it
    (one finding per day, naming every restricted stop of that day), and the
    untagged one is never mentioned."""
    state, (private, closed, open_) = _three_must_visits(spread)

    days = {poi["name"]: _day_of(state, poi["place_id"]) for poi in (private, closed, open_)}
    assert None not in days.values()  # all three kept
    # the fixture really exercises what it claims
    assert (days["Museum alpha"] != days["Museum beta"]) is spread
    expected: dict[int, list[str]] = {}
    for name, wording in (
        ("Museum alpha", "is marked in the place data as private"),
        ("Museum beta", "is marked in the place data as not open to the public"),
    ):
        expected.setdefault(days[name], []).append(f"{name} {wording}")

    findings = _findings(state)
    assert sorted(f.affected_section for f in findings) == sorted(
        f"experience_plan.daily_plans[{day}]" for day in expected
    )
    for finding in findings:
        day = int(finding.affected_section.split("[")[1].rstrip("]"))
        assert finding.message.startswith(f"Day {day}: ")
        for stop in expected[day]:
            assert stop in finding.message
        assert "Museum gamma" not in finding.message
        others = [stop for other, stops in expected.items() if other != day for stop in stops]
        assert not any(stop in finding.message for stop in others)


def test_the_finding_survives_save_and_reload(tmp_path: Path) -> None:
    state, requested = _validated(access="private")
    path = tmp_path / "state.json"
    PlanningStateRepository(store=LocalJsonStore(path)).save(state)

    reloaded = PlanningStateRepository(store=LocalJsonStore(path)).get_by_trip_id(state.trip_id)
    assert reloaded is not None and reloaded is not state
    assert [issue.model_dump() for issue in _findings(reloaded)] == [issue.model_dump() for issue in _findings(state)]
    assert _CODE in reloaded.validation_report.review_codes
    assert reloaded.validation_report.review_code_classification[_CODE] == classification.MATERIAL
