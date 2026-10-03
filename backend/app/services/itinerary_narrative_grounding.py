from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models.itinerary_narrative import (
    ItineraryNarrativeDayOutput,
    ItineraryNarrativeReport,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
    _find_forbidden_pattern,
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


# ---------------------------------------------------------------------------
# Section 202C.1E: unsupported FACTUAL-CLAIM forms.
#
# Audit: the narrator request carries no price, rating, opening hour, route
# duration/distance, availability, booking or safety value at all (route data
# is a yes/no flag; the only numbers are dates, traveler/day counts and
# validation counts). So any such claim in AI prose is invented. Before this,
# two guards existed: `_FORBIDDEN_TEXT_PATTERNS` (a fixed substring list --
# "price", "rating", "09:" ...) and the capitalised-vocabulary check above.
# "costs $12", "rated 4.8 stars" and "opens at 9am" contain none of those
# substrings and no unknown capitalised word, so they passed.
#
# These detectors match the FORM of a claim per category rather than one
# keyword, and are deliberately small. Because nothing in these categories is
# ever supplied, there is no "supported value" exception: a match rejects the
# narration and the deterministic fallback is used. Names supplied in the
# request (places, restaurants, destination, stay areas) are masked first, so
# a real place called "Pier 39" or "Crime Museum" is never mistaken for a claim.
# ---------------------------------------------------------------------------

_I = re.IGNORECASE
_NUM = r"\d+(?:[.,]\d+)?"
_FACTUAL_CLAIM_DETECTORS: dict[str, tuple[re.Pattern[str], ...]] = {
    "price": (
        re.compile(rf"[$€£¥₹]\s?{_NUM}|{_NUM}\s?[$€£¥₹]"),
        re.compile(rf"\b{_NUM}\s?(?:usd|eur|gbp|dollars?|euros?|pounds?|cents?|bucks)\b", _I),
        re.compile(
            rf"\b(?:costs?|priced|fees?|fares?|admission|entry|entrance|tickets?)\b(?:\s+\w+){{0,3}}?\s+{_NUM}\b", _I
        ),
        re.compile(r"\bfree\s+(?:admission|entry|entrance|of\s+charge|to\s+(?:enter|visit))\b|\b(?:admission|entry|entrance)\s+is\s+free\b", _I),
        re.compile(r"\b(?:inexpensive|affordable|pricey|expensive|budget-friendly|low-cost|costly)\b", _I),
    ),
    "rating": (
        re.compile(r"\b(?:top|highly|best|well|highest)?[- ]?rated\b", _I),
        re.compile(rf"\b(?:{_NUM}|one|two|three|four|five)[- ]?stars?\b", _I),
        re.compile(rf"\b{_NUM}\s?(?:/|out\s+of)\s?(?:5|10)\b", _I),
        re.compile(rf"\b(?:ratings?|scores?)\s+(?:of|is|are|at|:)\s*{_NUM}", _I),
        # "review" as a verb/status ("needs review", "you may want to review it") is
        # ordinary limitation wording; only review COUNTS and review-as-opinion forms are claims.
        re.compile(
            r"\b\d[\d.,]*k?\s+reviews?\b|\breviewers?\b|"
            r"\b(?:guest|visitor|customer|traveler|traveller|online|user|positive|negative|great|good|rave|glowing|mixed)\s+reviews?\b",
            _I,
        ),
    ),
    "opening_hours": (
        re.compile(r"\b(?:opens?|closes?|closed|closing|opening)\s+(?:at|from|until|till|by|between|around|on|daily|early|late)\b", _I),
        re.compile(r"\bopen\s+(?:from|until|till|daily|late|early|every|all\s+day|24)\b", _I),
        re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm|a\.m\.|p\.m\.)(?!\w)", _I),
        re.compile(r"\b\d{1,2}:\d{2}\b"),
        re.compile(r"\b(?:opening|closing|business|visiting)\s+(?:hours|times?)\b", _I),
    ),
    "availability_booking": (
        re.compile(
            r"\b(?:tickets?|spots?|seats?|tables?|rooms?|slots?|tours?|reservations?|bookings?|places)\s+"
            r"(?:(?:are|is|still|remain|remains|may\s+be)\s+)*(?:available|sold\s+out|limited|required|needed|recommended|essential)\b",
            _I,
        ),
        re.compile(
            r"\b(?:reservations?|(?:advance\s+|pre-?)?bookings?)\s+(?:(?:is|are)\s+)?(?:required|recommended|needed|essential|necessary|advised)\b",
            _I,
        ),
        re.compile(r"\bbook\s+(?:now|ahead|early|online|in\s+advance|your|a)\b|\breserve\s+(?:now|ahead|early|online|in\s+advance|your|a)\b", _I),
        re.compile(r"\b(?:must|should|need\s+to|have\s+to|be\s+sure\s+to)\s+(?:book|reserve)\b", _I),
        re.compile(r"\bsold\s+out\b|\bfully\s+booked\b|\bbooked\s+up\b|\b(?:booking|reservation)\s+(?:is\s+)?confirmed\b|\bconfirmation\s+number\b", _I),
    ),
    "safety": (
        re.compile(r"\b(?:un)?safe(?:ly|r|st)?\b|\bsafety\b", _I),
        re.compile(r"\bcrime\b|\bdangerous\b|\bpickpockets?\b|\bsecure\s+(?:area|neighbou?rhood)\b", _I),
    ),
    "route": (
        re.compile(rf"\b{_NUM}\s?-?\s?(?:min|mins|minutes?|hours?|hrs?)\b(?!\s?(?:/|per\b))", _I),
        re.compile(rf"\b{_NUM}\s?-?\s?(?:km|kilomet(?:er|re)s?|miles?|met(?:er|re)s?|blocks?)\b(?!\s?(?:/|per\b))", _I),
        re.compile(r"\b(?:minutes?|hours?|steps|blocks?|miles?|kilomet(?:er|re)s?)\s+(?:away|from)\b", _I),
        re.compile(r"\bwalking\s+distance\b|\b(?:short|quick|brief)\s+(?:walk|drive|ride|stroll|hop)\b", _I),
    ),
}


def _mask_supplied_names(text: str, request: ItineraryNarrativeRequest) -> str:
    names: set[str] = {request.destination, *request.stay_area_names}
    for day in request.days:
        names.update(day.restaurant_names)
        names.update(experience.name for experience in day.experiences)
    for name in sorted((n for n in names if n and len(n.strip()) >= 3), key=len, reverse=True):
        text = re.sub(re.escape(name.strip()), " <name> ", text, flags=re.IGNORECASE)
    return text


def find_unsupported_factual_claims(
    request: ItineraryNarrativeRequest, report: ItineraryNarrativeReport
) -> list[str]:
    """Categories (never the text) of factual claims the narration makes that
    no request field supports: price, rating, opening_hours,
    availability_booking, safety, route. Empty = none found."""
    texts: list[str] = [report.summary or "", *report.warnings, *report.assumptions]
    for day in report.daily_narratives:
        texts.append(day.narrative)
        texts.extend(day.caveats)
    masked = [_mask_supplied_names(text or "", request) for text in texts]
    return [
        category
        for category, patterns in _FACTUAL_CLAIM_DETECTORS.items()
        if any(pattern.search(text) for pattern in patterns for text in masked)
    ]


# ---------------------------------------------------------------------------
# `getting_around_advisory`: general "how visitors get around" guidance.
#
# This is the one narrator field that is general knowledge rather than
# supplied data, so the vocabulary check above cannot apply. Instead it must
# name at least one broad mode category and must not make any claim form the
# narration is barred from, nor any schedule/legal/ownership claim, nor talk
# about this itinerary's own legs (movement between scheduled places is
# provider route data only). Any number or non-initial capitalised word other
# than the destination's own is rejected, which keeps out fares, durations,
# line numbers, operators, apps and brands. A violation drops the advisory
# only; the narrative is unaffected.
# ---------------------------------------------------------------------------

_ADVISORY_MAX_CHARS = 320
_ADVISORY_MAX_SENTENCES = 2
_ADVISORY_MODE = re.compile(
    r"\b(?:cars?|self-drive|drivers?|driving|taxis?|cabs?|ride-?share|ride-?hailing|buses|bus|"
    r"metro|subway|underground|tram|trams|rail|trains?|walk|walking|on\s+foot|ferry|ferries|"
    r"transit|auto-?rickshaws?|rickshaws?)\b",
    _I,
)
_ADVISORY_EXTRA_CLAIMS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d"),
    re.compile(
        r"\b(?:schedules?|scheduled|timetables?|frequen\w*|every\s+(?:few|couple)|runs?\s+(?:until|till|from|every|late)|"
        r"24/7|round[- ]the[- ]clock|late[- ]night|reliable|punctual|on\s+time)\b",
        _I,
    ),
    re.compile(r"\b(?:permits?|licen[cs]es?|legal(?:ly)?|illegal|laws?|insurance|visas?|mandatory|required|regulat\w*)\b", _I),
    re.compile(
        r"\byour\s+(?:own\s+)?(?:car|vehicle)\b|\byou\s+(?:should|must|will\s+need\s+to|need\s+to|have\s+to)\s+"
        r"(?:rent|hire|drive|buy|use)\b|\b(?:rent|hire)\s+(?:a|your)\s+(?:car|vehicle)\s+(?:now|ahead|early|in\s+advance)\b",
        _I,
    ),
    re.compile(r"\b(?:itinerary|this\s+plan|your\s+plan|routes?|days?|stops?|scheduled\s+places)\b", _I),
    re.compile(r"\b(?:fares?|tickets?|pass(?:es)?|cards?|tolls?|tips?|surge|meters?|metered)\b", _I),
)


def sanitize_getting_around_advisory(request: ItineraryNarrativeRequest, text: str | None) -> str | None:
    """The advisory, unchanged, if it passes every check; otherwise `None`."""
    if not text or not text.strip():
        return None
    advisory = " ".join(text.split())
    if len(advisory) > _ADVISORY_MAX_CHARS:
        return None
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", advisory) if s.strip()]
    if len(sentences) > _ADVISORY_MAX_SENTENCES:
        return None
    if not _ADVISORY_MODE.search(advisory):
        return None
    lowered = advisory.lower()
    if _find_forbidden_pattern(advisory) is not None:
        return None

    # Scheduled place / restaurant / stay-area names: talking about them means
    # talking about this itinerary's own movement.
    plan_names: set[str] = set(request.stay_area_names)
    for day in request.days:
        plan_names.update(day.restaurant_names)
        plan_names.update(experience.name for experience in day.experiences)
    if any(name and len(name.strip()) >= 3 and name.strip().lower() in lowered for name in plan_names):
        return None

    destination_text = re.sub(re.escape(request.destination.strip()), " <destination> ", advisory, flags=_I)
    if any(pattern.search(destination_text) for patterns in _FACTUAL_CLAIM_DETECTORS.values() for pattern in patterns):
        return None
    if any(pattern.search(destination_text) for pattern in _ADVISORY_EXTRA_CLAIMS):
        return None

    destination_words = set(_words(request.destination))
    for sentence in sentences:
        for index, token in enumerate(_WORD.findall(sentence)):
            if index == 0 or not token[0].isupper():
                continue
            if token.lower() not in destination_words:
                return None
    return advisory


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
        # Why the AI attempt gave no answer, when it said (Section 1C).
        failure_kind=getattr(attempt, "failure_kind", None),
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
