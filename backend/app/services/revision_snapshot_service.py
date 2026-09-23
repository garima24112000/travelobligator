from __future__ import annotations

from typing import Any

from app.models.planning_state import PlanningState

# Section 199A (Task 10): the ONE canonical boundary between a live
# `PlanningState` and its immutable, storable revision-snapshot payload
# -- every other 199A call site (the repository, `RevisionLineageService`,
# tests) goes through these two functions instead of scattering
# `model_dump()`/manual dict surgery across several places.
#
# `serialize_planning_state_snapshot` deliberately does not remove,
# rename, or reshape any field -- Task 9's own instruction is to preserve
# historical state faithfully in the stored snapshot; any "reset this
# counter/field for a fresh working copy" semantic belongs to Section
# 199B's future fork-creation step, never to this read-only foundation.
# `model_dump(mode="json")` is the same serialization every other
# repository in this codebase already uses for a `PlanningState` (see
# `PlanningStateRepository.save`, `PlanningStateRow.state`) -- reusing it
# here means a revision snapshot round-trips through JSON exactly like
# the live planning-state document already does, with no second,
# divergent serialization format to keep in sync.


def serialize_planning_state_snapshot(planning_state: PlanningState) -> dict[str, Any]:
    """`PlanningState` -> a plain JSON-compatible dict, safe to store as
    `ItineraryRevision.snapshot` (local-JSON collection or Postgres
    JSONB). A deep copy by construction -- `model_dump` never returns
    objects shared with `planning_state`'s own live, still-mutable
    fields, so storing this dict can never let a later in-place mutation
    of the working `PlanningState` reach back into an already-persisted
    revision (Task 11's immutability requirement)."""
    return planning_state.model_dump(mode="json")


def deserialize_planning_state_snapshot(snapshot: dict[str, Any]) -> PlanningState:
    """The inverse of `serialize_planning_state_snapshot` -- a snapshot
    payload -> a fresh, independent `PlanningState` clone. Read-only
    verification/loading only in 199A (Task 10); Section 199B is what
    will actually use a clone like this as the seed for a new fork's
    working copy."""
    return PlanningState.model_validate(snapshot)


# Section 199B.1 (Task 1/2): the canonical REVISION-CONTENT projection --
# a live `PlanningState` reduced to exactly the fields that must agree
# with its active branch's head revision snapshot under the same
# `metadata.current_version` label. Everything else on `PlanningState`
# was individually audited (never a blanket "ignore metadata"/"ignore
# audit-like fields" rule) and falls into one of two excluded
# categories:
#
#   GUARDED TRANSIENT USER STATE -- may legitimately differ from the
#   snapshot under the same version label, but already has its own
#   explicit guard elsewhere, so this projection does not need to (and
#   must not) re-enforce it as a content mismatch:
#     - `feedback_history` / `pending_feedback_summary`: pending feedback
#       is expected to differ (that is the whole point of it existing
#       between revisions) -- branch switching already blocks on it
#       separately (`RevisionLineageService.activate_branch`'s own
#       pending-feedback guard), and regeneration intentionally consumes
#       it as input, mutating it, before the NEXT revision captures the
#       result. Task 12/3.
#     - `user_locks`: active locks already separately block branch
#       switching (Task 14/3); an inactive lock does not (199B already
#       proved this), so locks are never compared as content either way.
#
#   AUDIT / DERIVED TRANSIENT STATE -- recomputed from scratch or
#   appended to independently of any specific revision's content, so
#   comparing it would produce false conflicts for completely normal
#   activity:
#     - `regeneration_attempts`: purely an audit trail (see
#       `RegenerationAttempt`'s own docstring: "never a snapshot of plan
#       content... purely bookkeeping proving an attempt was requested
#       and refused") -- a BLOCKED/FAILED attempt can be appended
#       without ever creating a new version, so this legitimately grows
#       independently of revision content (Task 13).
#     - `regeneration_readiness` / `plan_diff_preview`: both are
#       recomputed from scratch from `version_history`/`feedback_history`/
#       `user_locks` on nearly every write (see
#       `RegenerationReadinessService.recompute`/
#       `PlanDiffPreviewService.recompute`'s own docstrings) -- derived
#       values, never independent content.
#     - `generation_progress`: real backend pipeline stage-progress
#       bookkeeping (`GenerationProgress.is_real_backend_stage_progress`
#       exists specifically to mark it as never travel content), reset
#       and mutated throughout a single generation run, never part of
#       what a revision snapshot is "about."
#     - `metadata.active_branch_id`: MUST be excluded (Task 3/4) -- a
#       freshly forked branch's head snapshot may be a revision Main
#       recorded (so the STORED snapshot's own `active_branch_id` reads
#       "Main" or `None`), while the just-activated LIVE clone correctly
#       has the fork's own id written onto it. Branch identity is
#       verified separately, through "resolve the active branch -> read
#       ITS head_revision_id" (Task 4) -- never by comparing this field.
#     - `metadata.active_stage`: mid-pipeline stage-tracking, mutated
#       only during an in-progress generation run (never independently
#       afterward) -- ephemeral, like `generation_progress`.
#     - `metadata.updated_at`: `PlanningState.touch()` sets this on
#       EVERY mutation helper call (`set_active_stage`,
#       `set_pipeline_status`, both `VersioningService` methods) --
#       confirmed by reading `touch()`'s own call sites, not assumed --
#       so it changes constantly and carries no content meaning.
#     - `metadata.extra`: confirmed (by repo-wide search) to be written
#       by no code path anywhere in this codebase -- an inert
#       extensibility field with nothing to compare.
#
# Every other field -- `trip_request`, `traveler_profile`,
# `destination_context`, `weather_context`, `holiday_context`,
# `currency_context`, `trip_strategy`, `stay_transport`,
# `experience_plan`, `candidate_quality_report`,
# `route_feasibility_report`, `route_aware_sequencing_report`,
# `travel_time_buffer_report`, `accommodation_inventory_report`,
# `flight_inventory_report`, `ai_candidate_proposal_batch`,
# `candidate_grounding_batch`, `ai_provider_discovery_result`,
# `ai_candidate_promotion_report`, `ai_itinerary_reasoning_result`,
# `ai_itinerary_repair_result`, `ai_itinerary_repair_attempt_count`,
# `decision_cards`, `experience_cards`, `validation_cards`,
# `provider_status`, `provider_coverage`, `unavailable_data`,
# `data_sources_used`, `itinerary_narrative_report`,
# `validation_report`, `version_history`, `planning_state_id`,
# `trip_id`, and `metadata.current_version`/`pipeline_status`/
# `created_at` -- is REVISION CONTENT: none of it is mutated by any code
# path except a real generation/regeneration run that also advances
# `metadata.current_version` (and therefore also creates a new
# revision), so under an UNCHANGED version label every one of these
# fields must agree with the branch head snapshot exactly, nested
# content included (Task 11) -- this is a deliberate full-`model_dump`
# comparison with an explicit exclude-list, never a hand-picked
# allow-list, precisely so a newly added `PlanningState` field is
# compared by default instead of silently skipped.

_EXCLUDED_TOP_LEVEL_PROJECTION_FIELDS = frozenset(
    {
        "feedback_history",
        "pending_feedback_summary",
        "user_locks",
        "regeneration_attempts",
        "regeneration_readiness",
        "plan_diff_preview",
        "generation_progress",
    }
)

_EXCLUDED_METADATA_PROJECTION_FIELDS = frozenset(
    {
        "active_branch_id",
        "active_stage",
        "updated_at",
        "extra",
    }
)


def build_workspace_consistency_projection(planning_state: PlanningState) -> dict[str, Any]:
    """The canonical REVISION-CONTENT view of `planning_state`, with
    every guarded-transient/audit-derived field above removed -- two
    `PlanningState`s (a live working copy and a deserialized historical
    snapshot) correspond to "the same revision content" if and only if
    their two projections compare equal (Task 2/5). Never mutates
    `planning_state` itself -- `model_dump` already returns a fresh
    dict."""
    projection = planning_state.model_dump(mode="json")
    for field in _EXCLUDED_TOP_LEVEL_PROJECTION_FIELDS:
        projection.pop(field, None)

    metadata = projection.get("metadata")
    if isinstance(metadata, dict):
        for field in _EXCLUDED_METADATA_PROJECTION_FIELDS:
            metadata.pop(field, None)

    return projection
