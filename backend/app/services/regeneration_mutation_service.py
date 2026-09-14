from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.itinerary_narrative import ItineraryNarrativeStatus
from app.models.planning_state import FeedbackEvent, PlanningStage, PlanningState
from app.services.feedback_service import feedback_service
from app.services.itinerary_narrative_service import itinerary_narrative_service
from app.services.plan_diff_preview_service import plan_diff_preview_service
from app.services.planning_orchestrator import planning_orchestrator
from app.services.regeneration_readiness_service import regeneration_readiness_service
from app.services.versioning_service import versioning_service

# Step 186C: the one real regeneration mutation path (Section 174's MVP
# scope), extracted unchanged from `POST /trips/{trip_id}/regenerate`'s
# "outcome 5" body (Step 174C/174D) so both the synchronous route
# (`ASYNC_GENERATION_ENABLED=false`, today's default) and the async job
# runner (`app.services.generation_job_service`, `ASYNC_GENERATION_ENABLED=
# true`) call exactly the same logic -- no regeneration semantics were
# rewritten, only relocated. Every guard/refusal check (confirm/locks/
# pending feedback/derivable affected stage) must already have passed
# before this is called -- this module performs no refusal checks itself
# and assumes the caller already validated eligibility exactly as
# `POST /regenerate` always has.


@dataclass
class RegenerationMutationResult:
    planning_state: PlanningState
    previous_version: str | None
    new_version_label: str
    changed_sections: list[str] = field(default_factory=list)
    preserved_sections: list[str] = field(default_factory=list)
    applied_feedback_event_ids: list[str] = field(default_factory=list)


class RegenerationMutationError(Exception):
    """Raised when `PlanningOrchestrator.rerun_affected_stages` fails
    unexpectedly during a regeneration mutation -- mirrors the
    `except Exception` branch `POST /trips/{trip_id}/regenerate` has had
    since Step 174C. Carries the `planning_state` as it stood at the
    moment of failure (already possibly partially re-run and saved by
    `rerun_affected_stages`'s own per-stage save cadence) so the caller
    doesn't need to re-fetch it before recording a failed
    `RegenerationAttempt` -- but the caller never creates a new version or
    marks any feedback applied for this outcome.
    """

    def __init__(self, planning_state: PlanningState) -> None:
        self.planning_state = planning_state
        super().__init__("Regeneration mutation failed unexpectedly.")


def apply_regeneration_mutation(
    planning_state: PlanningState,
    affected_stages: list[PlanningStage],
    pending_events: list[FeedbackEvent],
) -> RegenerationMutationResult:
    """Reruns exactly `affected_stages` via the existing, unmodified
    `PlanningOrchestrator.rerun_affected_stages` (never
    `LangGraphPlanningService`, never a fresh `generate_full_plan`),
    refreshes the optional read-only itinerary narrator, records a new
    `VersionHistoryItem`, marks `pending_events` applied, and recomputes
    `pending_feedback_summary`/`plan_diff_preview`/`regeneration_readiness`
    -- byte-for-byte the same steps, in the same order, `POST /regenerate`
    has always performed for its "outcome 5" success case.

    Does **not** record a `RegenerationAttempt` or save `planning_state` to
    any repository -- both callers (the sync route, the async job runner)
    do that themselves afterward, since each needs to build a different
    response/job-status shape around the same result. Raises
    `RegenerationMutationError` (never swallows it) if the rerun itself
    fails -- the caller is responsible for recording that as a failed
    attempt and never creating a version or marking feedback applied.
    """
    previous_version = planning_state.metadata.current_version
    applied_feedback_event_ids = [event.feedback_event_id for event in pending_events]

    try:
        planning_state = planning_orchestrator.rerun_affected_stages(
            planning_state, affected_stages
        )
    except Exception:
        raise RegenerationMutationError(planning_state) from None

    changed_sections = [stage.value for stage in affected_stages]

    # Step 182F: refresh the optional, read-only LLM narrator after the
    # real affected-stage rerun above -- ItineraryNarrativeService.generate
    # never raises, so a disabled or failing narrator never blocks
    # regeneration. Only reported in changed_sections when it actually
    # produced a new narrative (status == success).
    planning_state = itinerary_narrative_service.generate(planning_state)
    if (
        planning_state.itinerary_narrative_report is not None
        and planning_state.itinerary_narrative_report.status == ItineraryNarrativeStatus.SUCCESS
    ):
        changed_sections.append("itinerary_narrative")

    # Locks are disallowed entirely for this MVP scope (guarded by the
    # caller before this function is ever reached), so there is never a
    # locked item for this regeneration to have preserved.
    preserved_sections: list[str] = []

    planning_state = versioning_service.create_version_after_feedback(
        planning_state,
        # VersionHistoryItem.feedback_event_id is a single reference field;
        # the most recent pending event is recorded there as the one that
        # most directly triggered this regeneration, while every pending
        # event's id is still reported via applied_feedback_event_ids
        # below (all of them were considered, since this MVP scope has no
        # per-event partial selection).
        feedback_event_id=applied_feedback_event_ids[-1],
        changed_sections=changed_sections,
        preserved_sections=preserved_sections,
        summary=(
            "Plan regenerated from pending feedback. Rerun stages: "
            f"{', '.join(changed_sections)}."
        ),
    )

    # Step 174D: mark exactly the feedback events used in this run as
    # applied, using the version just created -- re-looked-up by id on the
    # `planning_state` returned by the calls above rather than relying on
    # object identity surviving `rerun_affected_stages`/
    # `create_version_after_feedback`. This is the only place
    # `FeedbackEvent.applied_at`/`applied_in_version` are ever set.
    new_version_label = planning_state.metadata.current_version
    applied_at = datetime.now(timezone.utc)
    applied_event_id_set = set(applied_feedback_event_ids)
    for event in planning_state.feedback_history:
        if event.feedback_event_id in applied_event_id_set:
            event.applied_at = applied_at
            event.applied_in_version = new_version_label
            event.handling_status = "applied"

    # Recomputed from scratch, in this order, so each reflects the
    # just-created version and the feedback just marked applied -- same
    # recompute-from-scratch pattern every other write path in this
    # codebase already follows.
    planning_state = feedback_service.recompute_pending_feedback_summary(planning_state)
    planning_state = plan_diff_preview_service.recompute(planning_state)
    planning_state = regeneration_readiness_service.recompute(planning_state)

    return RegenerationMutationResult(
        planning_state=planning_state,
        previous_version=previous_version,
        new_version_label=new_version_label,
        changed_sections=changed_sections,
        preserved_sections=preserved_sections,
        applied_feedback_event_ids=applied_feedback_event_ids,
    )
