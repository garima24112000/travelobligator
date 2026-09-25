from __future__ import annotations

from app.models.candidate_quality import CandidateQualityScore, CandidateQualityTier
from app.models.common import ValidationSeverity
from app.models.planning_state import PlanningState, TripPace, ValidationIssue
from app.services import place_taxonomy as taxonomy

# Section 202B.2 (Tasks 23, 26-29, 31): factual, case-specific itinerary
# quality findings. Pure read of `PlanningState` -- never repairs, drops or
# schedules anything, never calls a provider or AI, and never states a
# finding the candidate data does not support. A finding is raised only
# when it is about THIS plan and (where relevant) provider-backed supply
# actually existed, so a provider that simply had nothing appropriate is
# reported as a supply limitation, never blamed on the itinerary.

_VIABLE_TIERS = frozenset(
    {
        CandidateQualityTier.PRIMARY_ANCHOR,
        CandidateQualityTier.GOOD_CANDIDATE,
        CandidateQualityTier.SECONDARY_CANDIDATE,
    }
)
# Thin-day rule: fewer scheduled stops than this, for the trip's pace,
# while viable unscheduled candidates remain. (The per-day maximum is
# 2/3/4; a relaxed day of one stop is acceptable, a packed day of two is
# thin.)
_MIN_STOPS_PER_DAY: dict[TripPace, int] = {TripPace.RELAXED: 1, TripPace.BALANCED: 2, TripPace.PACKED: 3}
_CONCENTRATION_MIN_EXPERIENCES = 4
_CONCENTRATION_SHARE = 0.5


def _viable_scores(planning_state: PlanningState) -> list[CandidateQualityScore]:
    report = planning_state.candidate_quality_report
    if report is None:
        return []
    return [
        score
        for score in (*report.attraction_scores, *report.ai_directed_scores)
        if score.quality_tier in _VIABLE_TIERS
    ]


def build_plan_quality_findings(planning_state: PlanningState) -> tuple[list[ValidationIssue], list[str]]:
    """Returns `(issues, provider_coverage_notes)`."""
    plan = planning_state.experience_plan
    if plan is None or not plan.daily_plans:
        return [], []
    scheduled = [experience for day in plan.daily_plans for experience in day.experiences]
    if not scheduled:
        return [], []  # the plan-wide "nothing scheduled" critical issue already covers this

    profile = planning_state.traveler_profile
    pace = profile.pace if profile else planning_state.trip_request.pace
    interests = profile.interests if profile else planning_state.trip_request.interests
    canonical = taxonomy.canonical_interests(interests)

    viable = _viable_scores(planning_state)
    scheduled_place_ids = {e.provider_place_id for e in scheduled if e.provider_place_id}
    # Extra sub-features of a complex that already has one scheduled are
    # deliberately not scheduled (parent/child collapse) -- not a supply gap.
    scheduled_sub_kinds = {
        s.sub_feature_kind for s in viable if s.sub_feature_kind and s.candidate_id in scheduled_place_ids
    }
    art_focused = "art" in canonical
    # Section 202C.1A: low-value single objects (plain statues, plaques...)
    # are deliberately held back from general itineraries; they are not a
    # "selection gap" when a day is left light.
    held_back_junk = [
        s
        for s in viable
        if s.low_value_object and not art_focused and s.candidate_id not in scheduled_place_ids
    ]
    held_back_ids = {s.candidate_id for s in held_back_junk}
    unused_viable = [
        s
        for s in viable
        if s.candidate_id not in scheduled_place_ids
        and s.sub_feature_kind not in scheduled_sub_kinds
        and s.candidate_id not in held_back_ids
    ]

    issues: list[ValidationIssue] = []
    notes: list[str] = []

    # -- empty / thin days ----------------------------------------------------------
    minimum = _MIN_STOPS_PER_DAY[pace]
    for day in plan.daily_plans:
        count = len(day.experiences)
        section = f"experience_plan.daily_plans[{day.day_number}]"
        if count == 0:
            if unused_viable:
                cause = (
                    f"{len(unused_viable)} quality-approved provider-backed candidate(s) were not scheduled, "
                    "so this is an itinerary-selection gap, not a data limit."
                )
            else:
                cause = "No further quality-approved provider-backed candidates were available for this destination."
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="empty_day",
                    message=f"Day {day.day_number} has no scheduled experiences. {cause}",
                    affected_section=section,
                    suggested_fix="Regenerate the plan or add places for this day.",
                )
            )
        elif count < minimum and unused_viable:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="thin_day",
                    message=(
                        f"Day {day.day_number} has {count} scheduled experience(s), below the "
                        f"{minimum} expected for a {pace.value} pace, while {len(unused_viable)} "
                        "quality-approved provider-backed candidate(s) were left unscheduled."
                    ),
                    affected_section=section,
                    suggested_fix="Regenerate the plan so available candidates can be distributed across days.",
                )
            )

    if held_back_junk and any(len(d.experiences) < minimum for d in plan.daily_plans):
        issues.append(
            ValidationIssue(
                severity=ValidationSeverity.SUGGESTION,
                category="low_value_filler_skipped",
                message=(
                    f"{len(held_back_junk)} low-value single object(s) (plain statues, plaques or "
                    "isolated trees) were available but not used as filler, so this itinerary is lighter "
                    "than the pace suggests."
                ),
                affected_section="experience_plan",
                suggested_fix="Try a broader destination name or add specific places you want to see.",
            )
        )

    # -- interest coverage ----------------------------------------------------------------
    for interest in canonical:
        supply = [s for s in viable if interest in s.matched_interests]
        covered = any(interest in e.matched_interests for e in scheduled)
        if covered:
            continue
        if supply:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="interest_undercoverage",
                    message=(
                        f"Requested interest '{interest}' has {len(supply)} quality-approved "
                        "provider-backed candidate(s) available, but none is scheduled in this itinerary."
                    ),
                    affected_section="experience_plan",
                    suggested_fix=f"Regenerate the plan so it includes a place matching '{interest}'.",
                )
            )
        else:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.SUGGESTION,
                    category="interest_supply_limited",
                    message=(
                        f"No quality-approved provider-backed place matching the requested interest "
                        f"'{interest}' was available for this destination, so none could be scheduled."
                    ),
                    affected_section="experience_plan",
                    suggested_fix="Try a broader destination name or another interest.",
                )
            )
            notes.append(f"No provider-backed candidate for requested interest '{interest}'.")

    # -- category concentration -------------------------------------------------------------
    if len(scheduled) >= _CONCENTRATION_MIN_EXPERIENCES:
        diluted = [
            e
            for e in scheduled
            if e.low_value_object or e.notable_object or (e.commercial_gallery and not art_focused)
        ]
        alternatives = [
            s
            for s in unused_viable
            if not s.low_value_object and not s.notable_object and not (s.commercial_gallery and not art_focused)
        ]
        if len(diluted) / len(scheduled) >= _CONCENTRATION_SHARE and alternatives:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="category_concentration",
                    message=(
                        f"{len(diluted)} of {len(scheduled)} scheduled experiences are small "
                        "objects (memorials, statues, artworks) or commercial galleries that do not match the requested interests, "
                        f"while {len(alternatives)} other quality-approved candidate(s) were left "
                        "unscheduled. This itinerary needs review."
                    ),
                    affected_section="experience_plan",
                    suggested_fix="Regenerate the plan to favour more varied, higher-ranked places.",
                )
            )
    return issues, notes
