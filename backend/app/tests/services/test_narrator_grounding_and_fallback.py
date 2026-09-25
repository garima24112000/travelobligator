from __future__ import annotations

from typing import Any

import pytest

from app.models.itinerary_narrative import (
    ItineraryNarrativeDayOutput,
    ItineraryNarrativeReport,
    ItineraryNarrativeStatus,
)
from app.providers.itinerary_narrator.contract import NARRATOR_SYSTEM_PROMPT, build_grounded_prompt_body
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider
from app.services.itinerary_narrative_grounding import (
    SAFE_AI_UNAVAILABLE_MESSAGE,
    build_deterministic_narrative,
    find_ungrounded_terms,
    normalize_day_titles,
    safe_narrator_message,
)
from app.services.itinerary_narrative_request_builder import ItineraryNarrativeRequestBuilder
from test_itinerary_narrative_final_state import (  # type: ignore[import-not-found]
    _base_planning_state,
    _generate_with_fake_provider,
    _needs_review_validation_report,
)

# Section 202B.3 (Tasks 1-7): the narrator's factual boundary, the
# deterministic fallback, and failure semantics.

UNSUPPORTED_WORDS = (
    "popular", "famous", "must-see", "iconic", "scenic", "charming", "vibrant", "stunning",
    "rated", "stars", "price", "cheap", "open until", "tickets", "safe", "book",
)


def _request() -> Any:
    state = _base_planning_state()
    state.experience_plan.daily_plans[0].experiences[0].matched_interests = ["history"]
    state.experience_plan.daily_plans[0].experiences[0].normalized_category = "historic"
    state.trip_request.interests = ["history", "food"]
    return ItineraryNarrativeRequestBuilder().build_request(state), state


# -- contract / prompt ---------------------------------------------------------------


def test_prompt_contains_only_supplied_structured_facts() -> None:
    request, state = _request()
    state.experience_plan.daily_plans[0].experiences[0].why_included = "Matches your interests based on provider data."
    request = ItineraryNarrativeRequestBuilder().build_request(state)
    body = build_grounded_prompt_body(request)

    assert "Castelo de Sao Jorge (category: historic) -- serves requested interest: history" in body
    assert "Praca do Comercio" in body
    # Free text that invites embellishment is NOT sent.
    assert "Matches your interests" not in body
    assert "AI reasoning" not in body and "rationale" not in body.lower()
    for word in ("rating", "price", "opening", "review"):
        assert word not in body.lower()


def test_system_prompt_is_a_summariser_contract_not_a_writing_brief() -> None:
    lowered = NARRATOR_SYSTEM_PROMPT.lower()
    assert "summarizer" in lowered and "never a travel writer" in lowered
    assert "polished" not in lowered
    assert "serves requested interest" in lowered
    assert "no adjectives" in lowered


def test_requested_interests_are_split_into_served_and_unserved_for_the_narrator() -> None:
    """202B.3 (live run): the model wrote 'Trip covers museums, food, nightlife'
    for a plan whose places served only museum/food. The request now carries
    the provider-derived split so a merely-requested interest is never
    presented as covered."""
    request, _ = _request()
    assert request.interests_served == ["history"]
    assert request.interests_unserved == ["food"]

    body = build_grounded_prompt_body(request)
    assert "Requested interests served by a scheduled place: history" in body
    assert "Requested interests with no serving place: food" in body
    assert "never say the trip covers" in NARRATOR_SYSTEM_PROMPT.lower()


def test_known_limitations_are_sent_as_plain_words_not_field_identifiers() -> None:
    request, _ = _request()
    request = request.model_copy(update={"unavailable_data_fields": ["transit_feasibility", "weather_forecast"]})
    body = build_grounded_prompt_body(request)
    assert "Known limitations: transit feasibility, weather forecast" in body
    assert "transit_feasibility" not in body


# -- grounding guard -----------------------------------------------------------------------


def _report(narrative: str, summary: str = "A two-day plan.") -> ItineraryNarrativeReport:
    return ItineraryNarrativeReport(
        status=ItineraryNarrativeStatus.SUCCESS,
        summary=summary,
        daily_narratives=[
            ItineraryNarrativeDayOutput(
                day_number=1, date="2026-09-10", title="A day of Fado", narrative=narrative,
                referenced_experience_ids=["exp_a"],
            )
        ],
    )


def test_ungrounded_proper_noun_is_detected_and_grounded_text_is_not() -> None:
    request, _ = _request()
    assert find_ungrounded_terms(request, _report("Visit Castelo de Sao Jorge, then Praca do Comercio.")) == []
    assert find_ungrounded_terms(request, _report("Visit Castelo de Sao Jorge, then enjoy Fado in Alfama.")) == ["Fado", "Alfama"]


def test_titles_are_replaced_with_neutral_day_labels() -> None:
    request, _ = _request()
    assert normalize_day_titles(_report("Visit X."))  .daily_narratives[0].title == "Day 1"


# -- deterministic fallback ---------------------------------------------------------------------


def test_fallback_is_built_only_from_final_state_and_states_no_unsupported_claim() -> None:
    _, state = _request()
    state.validation_report = _needs_review_validation_report()

    report = build_deterministic_narrative(state)
    text = " ".join([report.summary or "", *report.warnings] + [f"{d.title} {d.narrative} {' '.join(d.caveats)}" for d in report.daily_narratives]).lower()

    assert report.narrative_source == "deterministic_fallback"
    assert "castelo de sao jorge" in text and "torre de belem" in text
    assert "visit castelo de sao jorge, then praca do comercio." in text
    assert "requested interests served by scheduled places: history." in text
    assert "no scheduled place serves: food." in text
    for word in UNSUPPORTED_WORDS:
        assert word not in text, word


def test_fallback_survives_place_names_that_contain_guarded_substrings() -> None:
    _, state = _request()
    state.experience_plan.daily_plans[0].experiences[0].name = "Price Tower Rating Hall"
    report = build_deterministic_narrative(state)
    assert "Price Tower Rating Hall" in report.daily_narratives[0].narrative


def test_fallback_adds_no_experiences_and_reports_empty_days_honestly() -> None:
    _, state = _request()
    state.experience_plan.daily_plans[1].experiences = []
    report = build_deterministic_narrative(state)
    assert report.daily_narratives[1].narrative == "No places are scheduled for this day."
    assert "No approved place could be scheduled on this day." in report.daily_narratives[1].caveats
    assert sum(len(d.referenced_experience_ids) for d in report.daily_narratives) == 2


# -- failure semantics through the service --------------------------------------------------------------


def _assert_plan_unchanged(before: Any, after: Any) -> None:
    assert after.experience_plan.model_dump() == before.experience_plan.model_dump()
    assert after.validation_report == before.validation_report


@pytest.mark.parametrize(
    "attempt",
    [
        ItineraryNarrativeReport(status=ItineraryNarrativeStatus.NOT_CONNECTED,
                                 message="Groq API key is not configured (GROQ_API_KEY unset)."),
        ItineraryNarrativeReport(status=ItineraryNarrativeStatus.FAILED,
                                 message="Groq API call failed: the provider rate-limited the request (HTTP 429)."),
        ItineraryNarrativeReport(status=ItineraryNarrativeStatus.FAILED,
                                 message="1 validation error for X input_value='raw model text org_SECRET'"),
        ItineraryNarrativeReport(status=ItineraryNarrativeStatus.UNAVAILABLE, message="anything"),
    ],
)
def test_ai_failure_keeps_the_plan_and_attaches_a_safe_fallback(attempt: ItineraryNarrativeReport) -> None:
    _, state = _request()
    state.validation_report = _needs_review_validation_report()
    before = state.model_copy(deep=True)

    result, _ = _generate_with_fake_provider(state, attempt)

    report = result.itinerary_narrative_report
    assert report.status == attempt.status  # the AI attempt's own status
    assert report.narrative_source == "deterministic_fallback"
    assert report.summary and report.daily_narratives
    assert "raw model text" not in str(report.model_dump()) and "org_SECRET" not in str(report.model_dump())
    _assert_plan_unchanged(before, result)


def test_rate_limit_message_is_preserved_because_it_is_a_fixed_classified_sentence() -> None:
    message = "Groq API call failed: the provider rate-limited the request (HTTP 429)."
    assert safe_narrator_message(ItineraryNarrativeReport(status=ItineraryNarrativeStatus.FAILED, message=message)) == message
    assert safe_narrator_message(ItineraryNarrativeReport(status=ItineraryNarrativeStatus.FAILED, message="raw text")) == SAFE_AI_UNAVAILABLE_MESSAGE


def test_ai_success_with_invented_content_is_rejected_for_the_fallback() -> None:
    request, state = _request()
    invented = _report("Visit Castelo de Sao Jorge and hear Fado in the vibrant streets.")
    before = state.model_copy(deep=True)

    result, _ = _generate_with_fake_provider(state, invented)

    report = result.itinerary_narrative_report
    assert report.status == ItineraryNarrativeStatus.FAILED
    assert "not in the itinerary data" in (report.message or "")
    assert report.narrative_source == "deterministic_fallback"
    assert "Fado" not in str(report.model_dump())
    _assert_plan_unchanged(before, result)


def test_grounded_ai_success_is_kept_with_neutral_titles() -> None:
    _, state = _request()
    good = _report("Visit Castelo de Sao Jorge, then Praca do Comercio.")
    result, _ = _generate_with_fake_provider(state, good)
    report = result.itinerary_narrative_report
    assert report.status == ItineraryNarrativeStatus.SUCCESS
    assert report.narrative_source == "ai"
    assert report.daily_narratives[0].title == "Day 1"


# -- adapter-level: forbidden / malformed output never leaks -------------------------------------------------


class _Client:
    def __init__(self, output: Any = None, exc: BaseException | None = None) -> None:
        self._output, self._exc = output, exc

    def invoke(self, prompt: str) -> Any:
        if self._exc:
            raise self._exc
        return self._output


def test_forbidden_model_output_fails_without_echoing_the_model_text() -> None:
    request, _ = _request()
    output = {
        "summary": "A plan. Everything here has been verified by the provider.",
        "daily_narratives": [{"day_number": 1, "title": "Day 1", "narrative": "Visit Castelo de Sao Jorge.",
                              "caveats": [], "referenced_experience_ids": ["exp_a"]}],
        "assumptions": [], "warnings": [],
    }
    report = GroqItineraryNarratorProvider(client=_Client(output), api_key="k").narrate(request)

    assert report.status == ItineraryNarrativeStatus.FAILED
    assert "verified" not in (report.message or "")
    assert "input_value" not in (report.message or "")
    assert safe_narrator_message(report) == report.message  # already a fixed sentence


def test_a_narrator_that_is_unavailable_never_marks_generation_failed() -> None:
    _, state = _request()
    result, _ = _generate_with_fake_provider(
        state, ItineraryNarrativeReport(status=ItineraryNarrativeStatus.FAILED, message="x")
    )
    assert result.experience_plan is not None and result.itinerary_narrative_report is not None
