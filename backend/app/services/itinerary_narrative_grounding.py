from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.itinerary_narrative import (
    ItineraryNarrativeDayOutput,
    ItineraryNarrativeReport,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
)
from app.models.planning_state import PlanningState
from app.services import place_taxonomy as taxonomy

# Section 202B.3 (Tasks 2, 4, 5): deterministic grounding for narration.
#
# 1. `find_ungrounded_terms` -- a STRUCTURAL check (not a growing
#    forbidden-word list): a capitalised word in AI prose that is neither
#    a sentence-initial word nor part of any supplied name/interest/
#    destination is content the input does not contain (an invented place,
#    landmark or proper-noun description such as a music genre) and the
#    AI narration is rejected in favour of the fallback.
# 2. `normalize_day_titles` -- day titles are deterministic ("Day N"), so a
#    title can never carry an unsupported label such as "Historic X".
# 3. `build_deterministic_narrative` -- a fact-only narrative built from the
#    FINAL `PlanningState`, used whenever AI narration is unavailable, fails
#    or is rejected. No LLM, no descriptive text.

SAFE_AI_UNAVAILABLE_MESSAGE = (
    "AI narration was unavailable, so a factual summary built from the itinerary data is shown."
)
_GROUNDING_REJECTED_MESSAGE = (
    "AI narration included wording that is not in the itinerary data and was not used; "
    "a factual summary built from the itinerary data is shown instead."
)
_SAFE_PROVIDER_MESSAGE = re.compile(r"^(Groq|Anthropic) API call failed: the provider ")

_WORD = re.compile(r"[^\W\d_][\w'’\-]*", re.UNICODE)
_COMMON_CAPITALISED = frozenset(
    {
        "day", "route", "weather", "nearby", "food", "visit", "then", "monday", "tuesday", "wednesday",
        "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "may",
        "june", "july", "august", "september", "october", "november", "december",
    }
)


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def _allowed_vocabulary(request: ItineraryNarrativeRequest) -> set[str]:
    vocab: set[str] = set(_COMMON_CAPITALISED)
    sources: list[str] = [request.destination, *request.interests, *request.stay_area_names,
                          *request.unavailable_data_fields, request.travel_group_type or "", request.pace or ""]
    for day in request.days:
        sources.extend(day.restaurant_names)
        for experience in day.experiences:
            sources.extend([experience.name, experience.category or "", experience.normalized_category or "",
                            *experience.matched_interests])
    for interest in taxonomy.canonical_interests(request.interests):
        sources.append(interest)
    for text in sources:
        vocab.update(_words(text))
    return vocab


def find_ungrounded_terms(request: ItineraryNarrativeRequest, report: ItineraryNarrativeReport) -> list[str]:
    vocab = _allowed_vocabulary(request)
    texts: list[str] = [report.summary or "", *report.warnings, *report.assumptions]
    for day in report.daily_narratives:
        texts.append(day.narrative)
        texts.extend(day.caveats)
    found: list[str] = []
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
            tokens = _WORD.findall(sentence)
            for index, token in enumerate(tokens):
                if index == 0 or not token[0].isupper() or len(token) < 2:
                    continue
                if token.lower() not in vocab and token not in found:
                    found.append(token)
    return found


def normalize_day_titles(report: ItineraryNarrativeReport) -> ItineraryNarrativeReport:
    days = [day.model_copy(update={"title": f"Day {day.day_number}"}) for day in report.daily_narratives]
    return report.model_copy(update={"daily_narratives": days})


def grounding_rejected_report(report: ItineraryNarrativeReport) -> ItineraryNarrativeReport:
    return ItineraryNarrativeReport(
        status=ItineraryNarrativeStatus.FAILED,
        provider=report.provider,
        model=report.model,
        message=_GROUNDING_REJECTED_MESSAGE,
    )


def safe_narrator_message(report: ItineraryNarrativeReport) -> str | None:
    """Only fixed, secret-free sentences may reach the user: classified
    provider failures, 'not configured', the grounding rejection, and
    the generic fallback sentence. Anything else (model output snippets,
    validation dumps) is replaced."""
    message = report.message or ""
    if not message:
        return None
    if (
        _SAFE_PROVIDER_MESSAGE.match(message)
        or message == _GROUNDING_REJECTED_MESSAGE
        or message.endswith("is not configured (GROQ_API_KEY unset).")
        or message.endswith("is not configured (ANTHROPIC_API_KEY unset).")
        or "AI narration output did not meet the required structure" in message
        or message.startswith("Itinerary narrator is disabled")
    ):
        return message
    return SAFE_AI_UNAVAILABLE_MESSAGE


def build_deterministic_narrative(
    planning_state: PlanningState, attempt: ItineraryNarrativeReport | None = None
) -> ItineraryNarrativeReport:
    """Fact-only narrative from the final state. Uses `model_construct` so a
    place name that happens to contain a guarded substring is never
    rejected by the AI-output validators (this text is deterministic)."""
    plan = planning_state.experience_plan
    request = planning_state.trip_request
    profile = planning_state.traveler_profile
    interests = profile.interests if profile else request.interests
    days = plan.daily_plans if plan else []
    all_experiences = [e for d in days for e in d.experiences]
    canonical = taxonomy.canonical_interests(interests)
    served = [i for i in canonical if any(i in e.matched_interests for e in all_experiences)]
    unserved = [i for i in canonical if i not in served]

    validation = planning_state.validation_report
    findings_by_day: dict[int, set[str]] = {}
    if validation is not None:
        for issue in (*validation.critical_issues, *validation.warnings):
            match = re.search(r"daily_plans\[(\d+)\]", issue.affected_section or "")
            if match and issue.category in ("empty_day", "thin_day"):
                findings_by_day.setdefault(int(match.group(1)), set()).add(issue.category)

    movement_ids = _movement_ids(planning_state)
    outputs: list[ItineraryNarrativeDayOutput] = []
    for day in days:
        names = [e.name for e in day.experiences]
        if not names:
            narrative = "No places are scheduled for this day."
        elif len(names) == 1:
            narrative = f"Visit {names[0]}."
        else:
            narrative = "Visit " + ", then ".join(names) + "."
        caveats: list[str] = []
        if len(names) >= 2 and not any(e.experience_id in movement_ids for e in day.experiences):
            caveats.append("Route data was not available for this day.")
        categories = findings_by_day.get(day.day_number, set())
        if "empty_day" in categories or not names:
            caveats.append("No approved place could be scheduled on this day.")
        elif "thin_day" in categories:
            caveats.append("This day has fewer places than expected for the chosen pace.")
        outputs.append(
            ItineraryNarrativeDayOutput.model_construct(
                day_number=day.day_number,
                date=day.date,
                title=f"Day {day.day_number}",
                narrative=narrative,
                caveats=caveats,
                referenced_experience_ids=[e.experience_id for e in day.experiences],
            )
        )

    parts = [
        f"{len(days)}-day itinerary for {request.primary_destination} "
        f"({request.start_date} to {request.end_date}) with {len(all_experiences)} scheduled place(s)."
    ]
    if served:
        parts.append("Requested interests served by scheduled places: " + ", ".join(served) + ".")
    if unserved:
        parts.append("No scheduled place serves: " + ", ".join(unserved) + ".")
    warnings: list[str] = []
    if validation is not None and (validation.critical_issues or any(
        w.severity.value == "warning" for w in validation.warnings
    )):
        warnings.append("Some itinerary checks still need review; see the limitations shown with the plan.")

    return ItineraryNarrativeReport.model_construct(
        status=(attempt.status if attempt is not None else ItineraryNarrativeStatus.UNAVAILABLE),
        provider=attempt.provider if attempt is not None else None,
        model=attempt.model if attempt is not None else None,
        message=(safe_narrator_message(attempt) if attempt is not None else None) or SAFE_AI_UNAVAILABLE_MESSAGE,
        summary=" ".join(parts),
        daily_narratives=outputs,
        warnings=warnings,
        assumptions=[],
        source_fields_used=["experience_plan", "traveler_profile", "validation_report"],
        generated_at=datetime.now(timezone.utc),
        narrative_source="deterministic_fallback",
    )


def _movement_ids(planning_state: PlanningState) -> set[str]:
    report = planning_state.route_feasibility_report
    ids: set[str] = set()
    if report is None:
        return ids
    for leg in report.legs:
        if getattr(leg.feasibility_status, "value", leg.feasibility_status) == "feasible":
            ids.add(leg.from_experience_id)
            ids.add(leg.to_experience_id)
    return ids
