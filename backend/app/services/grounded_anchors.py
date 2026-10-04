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


def grounded_anchor_place_ids(planning_state: PlanningState) -> set[str]:
    report = planning_state.ai_candidate_promotion_report
    if report is None:
        return set()
    return {
        str(candidate.provider_place_id)
        for candidate in report.promoted_candidates
        if candidate.provider_place_id
    }
