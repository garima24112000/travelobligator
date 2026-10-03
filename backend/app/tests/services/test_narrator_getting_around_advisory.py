from __future__ import annotations

from typing import Any

import pytest

from app.models.itinerary_narrative import GettingAroundProfile, ItineraryNarrativeStatus
from app.providers.itinerary_narrator.anthropic_adapter import _TOOL_DEFINITION
from app.providers.itinerary_narrator.contract import NARRATOR_SYSTEM_PROMPT
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider, _NarratorBatchSchema
from app.services.itinerary_narrative_grounding import (
    build_deterministic_narrative,
    sanitize_getting_around_advisory,
)
from app.services.itinerary_narrative_request_builder import ItineraryNarrativeRequestBuilder
from test_itinerary_narrative_final_state import (  # type: ignore[import-not-found]
    _base_planning_state,
    _generate_with_fake_provider,
    _success_report_for,
)

# `getting_around_advisory`: the one narrator field that is general guidance
# rather than supplied data. It is sanitized on its own and dropped -- never
# the narrative -- on any violation.

GOOD = "Walking and the metro are generally practical for getting around Lisbon, with taxis and rideshare as an option."


def _request() -> Any:
    return ItineraryNarrativeRequestBuilder().build_request(_base_planning_state())


# -- contract ------------------------------------------------------------------------


def test_prompt_exempts_the_advisory_from_the_summarizer_rules_and_expects_it() -> None:
    lowered = NARRATOR_SYSTEM_PROMPT.lower()
    assert "the one exception to the summarizer rules above" in lowered
    assert "for any recognized destination city, always write both" in lowered
    assert "empty string for both fields only when the destination is ambiguous" in lowered
    assert "never repeat this guidance in the summary or day narratives" in lowered
    # Live canary 1/6: this escape hatch made the model leave known cities empty.
    assert "not confident" not in lowered


def test_both_adapter_schemas_require_the_advisory_key_without_inviting_empty_output() -> None:
    groq_schema = _NarratorBatchSchema.model_json_schema()
    anthropic_schema = _TOOL_DEFINITION["input_schema"]
    assert "getting_around_advisory" in groq_schema["required"]
    assert "getting_around_advisory" in anthropic_schema["required"]
    for description in (
        groq_schema["properties"]["getting_around_advisory"]["description"],
        anthropic_schema["properties"]["getting_around_advisory"]["description"],
    ):
        assert "recognized destination" in description
        assert "not confident" not in description


@pytest.mark.parametrize(
    "text",
    [
        "Metro or local trains and walking are usually the easiest combination, with taxis useful when rail is less convenient.",
        "Taxis or a car with a driver can be practical for longer sightseeing transfers, while walking works well within compact areas.",
        "A rental car or taxis can be useful for dispersed trips, while public transit and walking work better within some individual areas.",
        "Public transit works well for many trips, while taxis or a car can be useful for destinations that are more spread out.",
    ],
)
def test_the_prompt_style_examples_pass_the_sanitizer(text: str) -> None:
    assert text in NARRATOR_SYSTEM_PROMPT
    assert sanitize_getting_around_advisory(_request(), text) == text


@pytest.mark.parametrize(
    "text",
    [
        "Public transit is often convenient for most visitor trips.",
        "Auto-rickshaws or taxis are often practical for longer trips, while walking suits compact areas.",
    ],
)
def test_sanitizer_recognizes_the_allowed_transit_and_rickshaw_modes(text: str) -> None:
    assert sanitize_getting_around_advisory(_request(), text) == text


def test_prompt_requires_a_profile_choice_and_forbids_the_transit_walk_default() -> None:
    lowered = NARRATOR_SYSTEM_PROMPT.lower()
    for profile in GettingAroundProfile:
        assert f"{profile.value}:" in lowered
    assert "do not automatically choose transit_walk" in lowered
    assert "written for the chosen profile" in lowered
    assert "do not open with 'public transit and walking' unless the profile is transit_walk" in lowered


def test_both_schemas_constrain_the_profile_to_the_enum_and_emit_it_before_the_advisory() -> None:
    allowed = {p.value for p in GettingAroundProfile} | {""}
    groq_schema = _NarratorBatchSchema.model_json_schema()
    anthropic_schema = _TOOL_DEFINITION["input_schema"]
    assert set(groq_schema["properties"]["getting_around_profile"]["enum"]) == allowed
    assert set(anthropic_schema["properties"]["getting_around_profile"]["enum"]) == allowed
    assert "getting_around_profile" in groq_schema["required"]
    assert "getting_around_profile" in anthropic_schema["required"]
    for keys in (list(groq_schema["properties"]), list(anthropic_schema["properties"])):
        assert keys.index("getting_around_profile") < keys.index("getting_around_advisory")


@pytest.mark.parametrize(
    "raw, expected",
    [("rail_walk", GettingAroundProfile.RAIL_WALK), ("", None), ("subway_only", None), (None, None), (3, None)],
)
def test_profile_is_validated_against_the_enum(raw: Any, expected: Any) -> None:
    assert GettingAroundProfile.parse(raw) == expected


@pytest.mark.parametrize("raw, expected", [(GOOD, GOOD), ("   ", None), (None, None), (7, None)])
def test_groq_adapter_passes_the_raw_advisory_through(raw: Any, expected: str | None) -> None:
    request = _request()
    response = {"summary": "A trip.", "daily_narratives": [], "assumptions": [], "warnings": []}
    if raw is not None:
        response["getting_around_advisory"] = raw

    result = GroqItineraryNarratorProvider(client=type("C", (), {"invoke": lambda self, p: response})()).narrate(request)

    assert result.status == ItineraryNarrativeStatus.SUCCESS
    assert result.getting_around_advisory == expected


# -- sanitizer -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        GOOD,
        "Public buses, the metro and walking cover most visitor needs.",
        "A car with driver or taxis are a common choice for visitors here.",
        "Intercity rail and local buses are the usual options, and the centre is easy to explore on foot.",
    ],
)
def test_accepts_broad_mode_guidance(text: str) -> None:
    assert sanitize_getting_around_advisory(_request(), text) == text


@pytest.mark.parametrize(
    "text, reason",
    [
        ("Taxis cost about 10 euros across town.", "price"),
        ("The metro is cheap and convenient.", "price wording"),
        ("Buses run every 10 minutes.", "schedule/number"),
        ("The metro runs until late, so it is easy to get around.", "schedule"),
        ("Trams are frequent and reliable.", "schedule/reliability"),
        ("Taxis are safe at night.", "safety"),
        ("You need an international driving licence to rent a car.", "legal"),
        ("You should rent a car to see the region.", "ownership/instruction"),
        ("Take the Yellow Line metro or use Uber.", "brand/line name"),
        ("Walk between Castelo de Sao Jorge and Praca do Comercio.", "itinerary place"),
        ("Each day of this itinerary is best done on foot.", "itinerary override"),
        ("Buy a transit pass for the metro.", "fare product"),
        ("The best way around is the metro.", "superlative"),
        ("Lisbon has a long history of maritime trade.", "no mode named"),
        ("Walk. Take the metro. Use taxis.", "too many sentences"),
        ("Walking " + "and the metro " * 40, "too long"),
        ("Tickets are available at every metro station.", "availability"),
        ("A short walk gets you most places.", "route-duration form"),
    ],
)
def test_rejects_claims_outside_the_advisory_contract(text: str, reason: str) -> None:
    assert sanitize_getting_around_advisory(_request(), text) is None, reason


def test_destination_name_is_allowed_capitalised() -> None:
    text = "Walking and public buses are practical ways to get around Lisbon."
    assert sanitize_getting_around_advisory(_request(), text) == text


# -- service -------------------------------------------------------------------------


def test_service_keeps_a_valid_advisory_on_a_successful_narrative() -> None:
    state = _base_planning_state()
    report = _success_report_for(_request()).model_copy(update={"getting_around_advisory": GOOD})

    result, _ = _generate_with_fake_provider(state, report)

    narrative = result.itinerary_narrative_report
    assert narrative.status == ItineraryNarrativeStatus.SUCCESS
    assert narrative.narrative_source == "ai"
    assert narrative.getting_around_advisory == GOOD


def test_service_drops_a_bad_advisory_without_rejecting_the_narrative() -> None:
    state = _base_planning_state()
    good_report = _success_report_for(_request())
    report = good_report.model_copy(update={"getting_around_advisory": "Taxis cost about 10 euros."})

    result, _ = _generate_with_fake_provider(state, report)

    narrative = result.itinerary_narrative_report
    assert narrative.status == ItineraryNarrativeStatus.SUCCESS
    assert narrative.narrative_source == "ai"
    assert narrative.summary == good_report.summary
    assert narrative.getting_around_advisory is None


def test_deterministic_fallback_builder_itself_never_writes_an_advisory() -> None:
    assert build_deterministic_narrative(_base_planning_state()).getting_around_advisory is None


# -- end to end through the real Groq adapter with a fake client -----------------------


def _groq_response(summary: str, advisory: str, profile: str = "rail_walk") -> dict[str, Any]:
    return {
        "summary": summary,
        "daily_narratives": [
            {"day_number": 1, "title": "Day 1", "narrative": "Visit Castelo de Sao Jorge, then Praca do Comercio.",
             "caveats": [], "referenced_experience_ids": ["exp_a", "exp_b"]},
        ],
        "assumptions": [],
        "warnings": [],
        "getting_around_profile": profile,
        "getting_around_advisory": advisory,
    }


def _generate_via_groq(monkeypatch: pytest.MonkeyPatch, response: dict[str, Any], **trip_overrides: Any) -> Any:
    from app.core.config import Settings
    import app.services.itinerary_narrative_service as narrative_service_module
    from app.services.itinerary_narrative_service import ItineraryNarrativeService

    class _Client:
        def invoke(self, prompt: str) -> Any:
            return response

    state = _base_planning_state()
    for key, value in trip_overrides.items():
        setattr(state.trip_request, key, value)
    monkeypatch.setattr(
        narrative_service_module, "get_settings", lambda: Settings(_env_file=None, ITINERARY_NARRATOR_ENABLED=True)
    )
    service = ItineraryNarrativeService(provider=GroqItineraryNarratorProvider(client=_Client()))
    return service.generate(state).itinerary_narrative_report


def test_known_city_non_empty_advisory_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _generate_via_groq(monkeypatch, _groq_response("A 2-day trip to Lisbon, Portugal.", GOOD))

    assert report.status == ItineraryNarrativeStatus.SUCCESS
    assert report.narrative_source == "ai"
    assert report.getting_around_advisory == GOOD
    assert report.getting_around_profile == GettingAroundProfile.RAIL_WALK


def test_ambiguous_destination_empty_advisory_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _generate_via_groq(
        monkeypatch, _groq_response("A 2-day trip to Springfield.", "", profile=""), primary_destination="Springfield"
    )

    assert report.status == ItineraryNarrativeStatus.SUCCESS
    assert report.narrative_source == "ai"
    assert report.summary == "A 2-day trip to Springfield."
    assert report.getting_around_advisory is None
    assert report.getting_around_profile is None


def test_valid_advisory_survives_a_narrative_grounding_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    # "Famous Opera" is not in the input -> the narrative is rejected by grounding.
    report = _generate_via_groq(monkeypatch, _groq_response("A trip that includes the Famous Opera.", GOOD))

    assert report.narrative_source == "deterministic_fallback"
    assert report.status == ItineraryNarrativeStatus.FAILED
    assert "Famous Opera" not in (report.summary or "")
    assert report.getting_around_advisory == GOOD
    assert report.getting_around_profile == GettingAroundProfile.RAIL_WALK


def test_malformed_advisory_is_dropped_without_dropping_the_narrative(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = "A 2-day trip to Lisbon, Portugal."
    report = _generate_via_groq(
        monkeypatch, _groq_response(summary, "Take the Yellow Line metro; tickets cost 2 euros and run every 5 minutes.")
    )

    assert report.status == ItineraryNarrativeStatus.SUCCESS
    assert report.narrative_source == "ai"
    assert report.summary == summary
    assert report.getting_around_advisory is None
    # The profile is dropped together with its advisory.
    assert report.getting_around_profile is None


def test_out_of_enum_profile_is_dropped_but_a_valid_advisory_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _generate_via_groq(monkeypatch, _groq_response("A 2-day trip to Lisbon, Portugal.", GOOD, "subway_only"))

    assert report.getting_around_advisory == GOOD
    assert report.getting_around_profile is None


def test_failed_provider_report_never_carries_an_unsanitized_advisory(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models.itinerary_narrative import ItineraryNarrativeReport

    state = _base_planning_state()
    failed = ItineraryNarrativeReport(
        status=ItineraryNarrativeStatus.FAILED, getting_around_advisory="Taxis cost about 10 euros."
    )
    result, _ = _generate_with_fake_provider(state, failed)

    assert result.itinerary_narrative_report.narrative_source == "deterministic_fallback"
    assert result.itinerary_narrative_report.getting_around_advisory is None
