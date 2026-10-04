"""Grounded semantic anchors (Section 3B).

A grounded anchor is an AI-proposed place that a provider grounded AND the
deterministic promotion rules approved
(`ai_candidate_promotion_report.promoted_candidates`). It is identified by
its provider place id only -- never by name -- so an anchor that was already
in the broad pool (and therefore keeps its pool identity) is recognised
exactly like one that arrived through a targeted lookup.

Read-only: nothing here calls a provider or a model.
"""

from __future__ import annotations

from app.models.planning_state import PlanningState

# Section 3C.1: when a plan under-uses its grounded anchors. A multi-day plan
# that had SEVERAL compatible grounded anchors to choose from (at least
# `LOW_ANCHOR_UTILIZATION_MIN_GROUNDED`) is expected to schedule more than
# one; at most `LOW_ANCHOR_UTILIZATION_MAX_SCHEDULED` is low utilization.
# The same two numbers raise the benchmark's LOW_GROUNDED_ANCHOR_UTILIZATION
# flag and trigger the planner's bounded post-reasoning anchor pass.
LOW_ANCHOR_UTILIZATION_MIN_GROUNDED = 4
LOW_ANCHOR_UTILIZATION_MAX_SCHEDULED = 1
LOW_ANCHOR_UTILIZATION_MIN_DAYS = 2


def low_anchor_utilization(available: int, scheduled: int, num_days: int) -> bool:
    return (
        num_days >= LOW_ANCHOR_UTILIZATION_MIN_DAYS
        and available >= LOW_ANCHOR_UTILIZATION_MIN_GROUNDED
        and scheduled <= LOW_ANCHOR_UTILIZATION_MAX_SCHEDULED
    )


def grounded_anchor_place_ids(planning_state: PlanningState) -> set[str]:
    report = planning_state.ai_candidate_promotion_report
    if report is None:
        return set()
    return {
        str(candidate.provider_place_id)
        for candidate in report.promoted_candidates
        if candidate.provider_place_id
    }
