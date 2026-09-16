from __future__ import annotations

from pathlib import Path

from app.models.planning_state import (
    PlanDiffPreview,
    PlanningState,
    RegenerationReadiness,
    TravelGroupType,
    TripRequest,
)
from app.services.feedback_service import _BLOCKED_BY as FEEDBACK_BLOCKED_BY
from app.services.feedback_service import feedback_service
from app.services.plan_diff_preview_service import plan_diff_preview_service
from app.services.regeneration_readiness_service import regeneration_readiness_service

# Step 189C (docs/14_backend_architecture.md section 133): regression
# guard for the exact class of bug Section 189 found -- a backend string
# that was accurate before Step 174C/174D implemented real, deterministic
# regeneration ("Feedback regeneration is not implemented yet." and its
# three siblings across `feedback_service.py`/
# `regeneration_readiness_service.py`/`plan_diff_preview_service.py`/
# `planning_state.py`) but became false the moment that implementation
# landed, and was never updated. These tests assert the replacement,
# state-based wording stays in place, and that `can_regenerate`/`status`/
# every other readiness field, `blocked_by`'s existence, and every
# model's field shape are all byte-for-byte unchanged from before this
# step -- this is a pure copy fix, never a behavior change.

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CHECKED_SOURCE_FILES = (
    _REPO_ROOT / "backend" / "app" / "services" / "feedback_service.py",
    _REPO_ROOT / "backend" / "app" / "services" / "regeneration_readiness_service.py",
    _REPO_ROOT / "backend" / "app" / "services" / "plan_diff_preview_service.py",
    _REPO_ROOT / "backend" / "app" / "models" / "planning_state.py",
)

_STALE_NEEDLES = (
    "regeneration engine is not implemented",
    "regeneration is not implemented",
    "feedback regeneration is not implemented",
    "feedback-driven regeneration is not implemented",
    "will be added later",
)


def _assert_no_stale_phrase(*texts: str) -> None:
    for text in texts:
        lowered = text.lower()
        for needle in _STALE_NEEDLES:
            assert needle not in lowered, f"stale phrase {needle!r} found in {text!r}"


def _fresh_planning_state() -> PlanningState:
    return PlanningState(
        trip_request=TripRequest(
            primary_destination="Testville, Testland",
            start_date="2026-08-10",
            end_date="2026-08-12",
            travelers_count=2,
            travel_group_type=TravelGroupType.COUPLE,
        )
    )


# ---------------------------------------------------------------------------
# 1. Static regression guard -- catches a reintroduced stale phrase even in
#    a docstring/comment no runtime test below would ever exercise.
# ---------------------------------------------------------------------------


def test_no_stale_regeneration_implementation_claim_in_source() -> None:
    for path in _CHECKED_SOURCE_FILES:
        lowered = path.read_text().lower()
        for needle in _STALE_NEEDLES:
            assert needle not in lowered, f"{path} contains stale phrase {needle!r}"


# ---------------------------------------------------------------------------
# 2. feedback_service.py -- the shared `_BLOCKED_BY` tuple, and both real
#    call sites that render it (per-event change_preview, plan-level
#    pending_feedback_summary).
# ---------------------------------------------------------------------------


def test_feedback_service_blocked_by_constant_has_no_stale_claim() -> None:
    _assert_no_stale_phrase(*FEEDBACK_BLOCKED_BY)
    # Still 3 honest blockers, still describing what this endpoint itself
    # does/doesn't do -- never a claim about whether regeneration exists.
    assert len(FEEDBACK_BLOCKED_BY) == 3
    assert "does not itself trigger regeneration" in FEEDBACK_BLOCKED_BY[0]
    assert "No AI interpretation provider is connected." in FEEDBACK_BLOCKED_BY
    assert "No plan sections are modified by the feedback capture endpoint." in FEEDBACK_BLOCKED_BY


def test_apply_feedback_change_preview_blocked_by_has_no_stale_claim() -> None:
    planning_state = _fresh_planning_state()
    updated = feedback_service.apply_feedback(planning_state, "Make this less packed")
    change_preview = updated.feedback_history[-1].interpretation["change_preview"]

    _assert_no_stale_phrase(*change_preview["blocked_by"])
    # Structure/eligibility fields all unchanged by this copy-only fix.
    assert change_preview["preview_status"] == "not_applied"
    assert change_preview["would_require_regeneration"] is True
    assert len(change_preview["blocked_by"]) == 3


def test_pending_feedback_summary_blocked_by_has_no_stale_claim() -> None:
    planning_state = _fresh_planning_state()
    feedback_service.apply_feedback(planning_state, "Make this less packed")
    summary = planning_state.pending_feedback_summary

    _assert_no_stale_phrase(*summary.blocked_by)
    assert summary.status == "captured_not_applied"
    assert summary.total_feedback_items == 1


# ---------------------------------------------------------------------------
# 3. regeneration_readiness_service.py + RegenerationReadiness model --
#    both the "no plan generated yet" recompute branch and the model's own
#    pre-recompute default (same content, kept in sync deliberately).
# ---------------------------------------------------------------------------


def test_regeneration_readiness_model_default_has_no_stale_claim() -> None:
    readiness = RegenerationReadiness()
    _assert_no_stale_phrase(*readiness.blocked_by)
    assert readiness.can_regenerate is False
    assert readiness.status == "blocked"
    assert len(readiness.blocked_by) == 2
    assert readiness.blocked_by[0] == "No plan has been generated for this trip yet."


def test_regeneration_readiness_no_plan_branch_has_no_stale_claim() -> None:
    planning_state = _fresh_planning_state()  # no version_history yet
    updated = regeneration_readiness_service.recompute(planning_state)
    readiness = updated.regeneration_readiness

    _assert_no_stale_phrase(*readiness.blocked_by)
    assert readiness.can_regenerate is False
    assert readiness.status == "blocked"
    assert len(readiness.blocked_by) == 2
    assert readiness.blocked_by[0] == "No plan has been generated for this trip yet."
    # required_inputs/missing_capabilities (the symbolic capability-name
    # lists, not prose) are deliberately untouched by this copy-only fix.
    assert readiness.required_inputs == [
        "generated_plan",
        "pending_feedback",
        "regeneration_engine",
    ]
    assert readiness.missing_capabilities == ["generated_plan", "regeneration_engine"]


# ---------------------------------------------------------------------------
# 4. plan_diff_preview_service.py + PlanDiffPreview model -- same pattern.
# ---------------------------------------------------------------------------


def test_plan_diff_preview_model_default_has_no_stale_claim() -> None:
    preview = PlanDiffPreview()
    _assert_no_stale_phrase(*preview.blocked_by)
    assert preview.regeneration_available is False
    assert len(preview.blocked_by) == 3


def test_plan_diff_preview_service_no_plan_branch_has_no_stale_claim() -> None:
    planning_state = _fresh_planning_state()
    updated = plan_diff_preview_service.recompute(planning_state)
    preview = updated.plan_diff_preview

    _assert_no_stale_phrase(*preview.blocked_by)
    assert preview.regeneration_available is False
    assert preview.preview_status == "not_available"
    assert len(preview.blocked_by) == 2
    assert preview.blocked_by[0] == "No plan has been generated for this trip yet."
