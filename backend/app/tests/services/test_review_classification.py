from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

from app.models.common import ReadinessStatus
from app.models.planning_state import ValidationReport
from app.services import review_classification as classification
from app.services.plan_validator_service import _ferry_in_route_issues, _geographic_dispersion_issue
from app.services import geographic_dispersion as dispersion
from app.tests.services.test_quality_tuning_corrections import _ferry_network, _run_canary  # noqa: F401

# V1 disclosure contract: one backend-owned classification of validation findings
# (informational / material), exposed on the validation report. Presentation only.

_SERVICES = Path(__file__).resolve().parents[2] / "services"
_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"

# The words the traveller page treats as implementation / diagnostic prose
# (`frontend/lib/display-labels.ts`, DIAGNOSTIC_WORDING).
_IMPLEMENTATION_WORDING = [
    r"scheduled leg\(s\)|\bof \d+ (?:scheduled )?legs?\b", r"route-aware|sequencing|reorder", r"route geometry",
    r"\bprovider\b|\bproviders\b|configur", r"\binventory\b|scraped|\bHTML\b|adapter|integration",
    r"regenerat|deterministic",
]


def _emitted_categories() -> set[str]:
    """Every finding category named in the services' source, plus the named constants."""
    found: set[str] = {dispersion.CATEGORY}
    for path in _SERVICES.glob("*.py"):
        found.update(re.findall(r'category="([a-z_]+)"', path.read_text()))
    return found


def test_every_known_finding_category_is_classified_exactly_once() -> None:
    codes = {category.upper() for category in _emitted_categories()}
    assert len(codes) > 25
    known = classification.INFORMATIONAL_CODES | classification.MATERIAL_CODES
    assert codes <= known, sorted(codes - known)
    assert not classification.INFORMATIONAL_CODES & classification.MATERIAL_CODES
    for code in ("UNDERFILLED_PLAN", "INSUFFICIENT_VERIFIED_INVENTORY"):  # outcome codes without a category literal
        assert code in classification.MATERIAL_CODES


def test_what_a_traveller_must_check_is_material() -> None:
    for code in ("LONG_TRAVEL_DAY", "FEASIBILITY", "MOVEMENT_DATA", "GEOGRAPHIC_DISPERSION", "GEOGRAPHIC_SPREAD",
                 "ROUTE_INCLUDES_FERRY", "INTEREST_UNDERCOVERAGE", "UNDERFILLED_PLAN", "MUST_VISIT"):
        assert classification.classify(code) == classification.MATERIAL, code
    for code in ("WEATHER", "HOLIDAYS", "BUDGET", "HOTEL_RATINGS", "ACCOMMODATION_INVENTORY", "FLIGHT_INVENTORY"):
        assert classification.classify(code) == classification.INFORMATIONAL, code
    # a category is classified as its code, whatever its case
    assert classification.classify("long_travel_day") == classification.MATERIAL
    assert classification.classify(" weather ") == classification.INFORMATIONAL


def test_an_unknown_code_is_material_so_it_stays_visible() -> None:
    assert classification.classify("A_FINDING_ADDED_NEXT_YEAR") == classification.MATERIAL
    assert classification.classify(None) == classification.MATERIAL
    assert classification.classification_for(["weather", "SOMETHING_NEW", None, ""]) == {
        "SOMETHING_NEW": "material", "WEATHER": "informational",
    }


def test_the_classification_is_not_the_benchmark_acceptance_list() -> None:
    # The evaluation tooling's accepted codes are all informational here, but the two lists are
    # different things: a ferry is material to a traveller even where the benchmark accepts it,
    # and the tooling does not read this module.
    spec = importlib.util.spec_from_file_location("canary_city_for_classification", _SCRIPTS / "canary_city.py")
    canary = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(canary)
    assert canary._ACCEPTED_REVIEW_CODES <= classification.INFORMATIONAL_CODES
    assert canary._CONDITIONAL_INFORMATIONAL_CODES <= classification.MATERIAL_CODES
    assert canary._ACCEPTANCE_POLICY_VERSION == 2
    assert "review_classification" not in (_SCRIPTS / "canary_city.py").read_text()


def test_ferry_and_dispersion_findings_are_written_for_a_traveller() -> None:
    messages = [
        _geographic_dispersion_issue(4, 26.25, cause).message
        for cause in (dispersion.CAUSE_DISCRETIONARY_DISPERSION, dispersion.CAUSE_LIMITED_COMPATIBLE_INVENTORY,
                      dispersion.CAUSE_MANDATORY_DESTINATION, dispersion.CAUSE_UNVERIFIED)
    ]
    assert all("spread across a large area" in m and "Review the travel required" in m and "26.2 km" in m for m in messages)
    for message in messages:
        assert not any(re.search(pattern, message, re.IGNORECASE) for pattern in _IMPLEMENTATION_WORDING), message
        assert not any(word in message.lower() for word in ("impossible", "infeasible", "cannot be travelled"))


def test_the_ferry_finding_states_what_is_not_verified(monkeypatch: Any, tmp_path: Path, inventory_sufficiency_gate_enabled: None) -> None:
    report = _run_canary(monkeypatch, tmp_path, _ferry_network(with_steps=True), ["history"])
    disclosure = report["routing"]["ferry_disclosure"]
    # acceptance policy 2 still finds its disclosure, worded for a traveller
    assert disclosure["provider_confirmed_ferry_days"] == disclosure["days_with_traveller_facing_ferry_warning"] != []
    assert disclosure["warnings_state_nothing_about_the_service_is_verified"] is True
    assert report["acceptance"]["policy_version"] == 2


def test_the_ferry_wording_itself() -> None:
    from app.models.planning_state import PlanningState  # noqa: F401  (documentation of the type)

    source = (_SERVICES / "plan_validator_service.py").read_text()
    start = source.index("def _ferry_in_route_issues")
    body = source[start : source.index("\ndef ", start + 10)]
    text = " ".join(re.findall(r'"([^"\n]*)"', body[body.index("message=(") : body.index("affected_section", body.index("message=("))]))
    assert "involves a ferry crossing" in text and "No ferry timetable, fare, ticket or availability is known" in text
    assert "route estimate only" in text and "not a verified ferry schedule" in text
    assert not any(re.search(pattern, text, re.IGNORECASE) for pattern in _IMPLEMENTATION_WORDING), text
    assert _ferry_in_route_issues is not None


def test_the_report_carries_the_classification_and_readiness_does_not_read_it() -> None:
    # An older stored report has no classification and is still valid.
    old = ValidationReport.model_validate({"readiness_status": "needs_review", "review_codes": ["WEATHER"]})
    assert old.review_code_classification == {} and old.readiness_status == ReadinessStatus.NEEDS_REVIEW
    # Readiness and the codes are decided before, and without, the classification.
    source = (_SERVICES / "plan_validator_service.py").read_text()
    decided = source.index("readiness_status = ReadinessStatus.READY")
    assert source.index("review_code_classification=classification_for(") > decided
    assert source.count("classification_for(") == 1 and "classify(" not in source
    for name in ("experience_planner_service", "day_composition", "candidate_usefulness", "route_recomposition_service",
                 "routability_repair_service", "route_feasibility_service", "usefulness_contract"):
        assert "review_classification" not in (_SERVICES / f"{name}.py").read_text(), name


def test_the_api_returns_the_classification_and_the_ferry_flag_field(client: Any, generated_trip_id: str) -> None:
    from app.models.routing import RouteLegFeasibility

    response = client.get(f"/trips/{generated_trip_id}/validation-report")
    assert response.status_code == 200
    report = response.json()["data"]["validation_report"]
    # additive: the existing contract is all still there
    for key in ("readiness_status", "critical_issues", "warnings", "blocking_codes", "review_codes"):
        assert key in report
    sent = report["review_code_classification"]
    assert set(report["review_codes"]) | set(report["blocking_codes"]) <= set(sent)
    assert {warning["category"].upper() for warning in report["warnings"]} <= set(sent)
    assert all(sent[code] == classification.classify(code) for code in sent)
    # readiness is exactly what the codes alone give, whatever their class
    expected = "blocked" if report["critical_issues"] else "needs_review" if report["review_codes"] else "ready"
    assert report["readiness_status"] == expected
    # the leg model the page reads carries the provider's ferry flag (never a default guess)
    assert "includes_ferry" in RouteLegFeasibility.model_fields
    assert RouteLegFeasibility.model_fields["includes_ferry"].default is None


def test_a_generated_plan_classifies_every_code_it_reports(monkeypatch: Any, tmp_path: Path, inventory_sufficiency_gate_enabled: None) -> None:
    from app.tests.services.test_day_composition_q3 import _ai_state, _core
    from app.services.experience_planner_service import ExperiencePlannerService
    from app.services.plan_validator_service import PlanValidatorService

    state = _ai_state(_core(8), [["c0", "c1", "c2"], ["c3", "c4", "c5"]])
    ExperiencePlannerService().run(state)
    PlanValidatorService().run(state)
    report = state.validation_report
    assert report.review_codes and set(report.review_codes) <= set(report.review_code_classification)
    assert {issue.category.upper() for issue in report.warnings if issue.category} <= set(report.review_code_classification)
    assert set(report.review_code_classification.values()) <= {"informational", "material"}
    # the same readiness the codes alone give
    expected = "blocked" if report.critical_issues else "needs_review" if report.review_codes else "ready"
    assert report.readiness_status.value == expected
