from __future__ import annotations

from pathlib import Path
from typing import Any

from app.models import TripPace
from app.models.planning_state import InterestCoverageEntry, ValidationReport
from app.services import plan_quality_findings as findings_module
from app.services.experience_planner_service import ExperiencePlannerService
from app.services.interest_coverage import interest_coverage
from app.services.plan_quality_findings import interest_coverage_entries
from app.services.plan_validator_service import PlanValidatorService
from app.tests.services.test_itinerary_content_quality import findings, museum, poi, state, validated

# V1 disclosure contract: every validation report says, per requested interest, whether the
# final itinerary covers it (`ValidationReport.interest_coverage`). It restates what the
# existing coverage findings are raised from; it changes no finding, code or readiness.
# Synthetic fixtures only.

_SERVICES = Path(__file__).resolve().parents[2] / "services"


def _statuses(st: Any) -> dict[str, str]:
    return {entry.interest: entry.status for entry in st.validation_report.interest_coverage}


def _readiness_inputs(st: Any) -> tuple[Any, ...]:
    report = st.validation_report
    return (
        report.readiness_status, list(report.review_codes), list(report.blocking_codes),
        [(issue.severity, issue.category, issue.message) for issue in (*report.critical_issues, *report.warnings)],
    )


def test_every_requested_interest_covered() -> None:
    st = validated(state([museum(i, i * 0.001) for i in range(3)], days=1, interests=["museums"]))
    assert _statuses(st) == {"museum": "covered"}
    assert st.validation_report.interest_coverage[0].requested_as == ["museums"]
    assert findings(st, "interest_undercoverage") == [] and findings(st, "interest_supply_limited") == []


def test_an_interest_without_any_verified_supply_is_reported_as_such() -> None:
    st = validated(state([museum(i) for i in range(2)], days=1, interests=["museums", "nightlife"]))
    assert _statuses(st) == {"museum": "covered", "nightlife": "not_covered_no_supply"}
    # exactly the case the existing (suggestion-level) finding is raised for -- which is unchanged
    limited = findings(st, "interest_supply_limited")
    assert len(limited) == 1 and limited[0].severity.value == "suggestion" and "'nightlife'" in limited[0].message
    assert "INTEREST_SUPPLY_LIMITED" not in st.validation_report.review_codes
    assert "INTEREST_UNDERCOVERAGE" not in st.validation_report.review_codes


def test_an_interest_left_out_although_supply_existed_is_told_apart() -> None:
    st = state(
        [museum(1), poi("mk", "Market", {"amenity": "marketplace", "shop": "greengrocer"}, 0.2)],
        days=1, pace=TripPace.RELAXED, interests=["food"],
    )
    ExperiencePlannerService().run(st)
    day = st.experience_plan.daily_plans[0]
    day.experiences = [stop for stop in day.experiences if "food" not in stop.matched_interests]
    PlanValidatorService().run(st)
    assert _statuses(st) == {"food": "not_covered_supply_available"}
    assert findings(st, "interest_undercoverage") and "INTEREST_UNDERCOVERAGE" in st.validation_report.review_codes


def test_a_term_the_taxonomy_does_not_know_is_not_tracked_never_covered() -> None:
    st = validated(state([museum(i) for i in range(2)], days=1, interests=["museums", "zorbing"]))
    entries = {entry.interest: entry for entry in st.validation_report.interest_coverage}
    assert entries["museum"].status == "covered"
    assert entries["zorbing"].status == "not_tracked" and entries["zorbing"].requested_as == ["zorbing"]
    assert "covered" not in {entry.status for entry in st.validation_report.interest_coverage if entry.interest == "zorbing"}


def test_several_terms_for_one_interest_are_one_entry() -> None:
    st = validated(state([museum(i) for i in range(2)], days=1, interests=["museums", "museum exhibits", "piers"]))
    entries = {entry.interest: entry for entry in st.validation_report.interest_coverage}
    assert entries["museum"].requested_as == ["museums", "museum exhibits"]
    assert entries["waterfront"].status == "not_covered_no_supply" and entries["waterfront"].requested_as == ["piers"]


def test_without_a_scheduled_plan_coverage_is_unknown_not_empty() -> None:
    st = state([museum(1)], days=1, interests=["museums"])
    assert interest_coverage_entries(st) is None  # no plan yet
    ExperiencePlannerService().run(st)
    for day in st.experience_plan.daily_plans:
        day.experiences = []
    assert interest_coverage_entries(st) is None  # nothing scheduled: nothing to judge
    # a report stored before the field has none, and is still valid
    old = ValidationReport.model_validate({"readiness_status": "needs_review", "review_codes": ["WEATHER"]})
    assert old.interest_coverage is None


def test_the_entries_restate_the_existing_coverage_and_nothing_else() -> None:
    st = validated(state([museum(i) for i in range(3)], days=1, interests=["museums", "nightlife", "food"]))
    coverage = interest_coverage(st)
    for entry in st.validation_report.interest_coverage:
        assert (entry.status == "covered") is coverage[entry.interest], entry
    uncovered = {entry.interest: entry.status for entry in st.validation_report.interest_coverage if entry.status != "covered"}
    by_warning = {issue.message.split("'")[1] for issue in findings(st, "interest_undercoverage")}
    by_suggestion = {issue.message.split("'")[1] for issue in findings(st, "interest_supply_limited")}
    assert {k for k, v in uncovered.items() if v == "not_covered_supply_available"} == by_warning
    assert {k for k, v in uncovered.items() if v == "not_covered_no_supply"} == by_suggestion


def test_readiness_codes_and_findings_do_not_depend_on_the_new_field(monkeypatch: Any) -> None:
    def run() -> Any:
        return validated(state([museum(i) for i in range(2)], days=1, interests=["museums", "nightlife"]))

    with_field = run()
    monkeypatch.setattr("app.services.plan_validator_service.interest_coverage_entries", lambda _state: None)
    without_field = run()
    assert without_field.validation_report.interest_coverage is None
    assert _readiness_inputs(with_field) == _readiness_inputs(without_field)

    # a failure producing it never breaks validation: coverage is then unknown
    def broken(_state: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.plan_validator_service.interest_coverage_entries", broken)
    failed = run()
    assert failed.validation_report.interest_coverage is None
    assert _readiness_inputs(failed) == _readiness_inputs(with_field)


def test_nothing_that_plans_or_judges_reads_the_field() -> None:
    for name in ("experience_planner_service", "day_composition", "candidate_usefulness", "interest_coverage",
                 "route_recomposition_service", "usefulness_contract", "review_classification"):
        source = (_SERVICES / f"{name}.py").read_text()
        assert "interest_coverage_entries" not in source and "InterestCoverageEntry" not in source, name
    validator = (_SERVICES / "plan_validator_service.py").read_text()
    # produced once, after readiness is decided
    assert validator.count("_interest_coverage_safely(planning_state)") == 1
    assert validator.index("interest_coverage=_interest_coverage_safely(") > validator.index("readiness_status = ReadinessStatus.READY")
    # the findings themselves were not rewritten around it
    assert "interest_coverage_entries" not in findings_module.build_plan_quality_findings.__code__.co_names
    # Phase 2 added `basis`: presentation only, like the rest of the entry
    assert InterestCoverageEntry.model_fields.keys() == {"interest", "requested_as", "status", "basis"}


def test_the_api_returns_the_coverage_entries(client: Any, generated_trip_id: str) -> None:
    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    report = response.json()["data"]["validation_report"]
    assert "interest_coverage" in report
    for entry in report["interest_coverage"] or []:
        assert set(entry) == {"interest", "requested_as", "status"}
        assert entry["status"] in {"covered", "not_covered_supply_available", "not_covered_no_supply", "not_tracked"}
