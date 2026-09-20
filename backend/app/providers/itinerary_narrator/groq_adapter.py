from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.models.itinerary_narrative import (
    ItineraryNarrativeDayOutput,
    ItineraryNarrativeReport,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
    validate_narrative_against_request,
)
from app.providers.itinerary_narrator.base import ItineraryNarratorProvider

# Groq-backed itinerary narrator adapter (Step 182F,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
# Mirrors `app.providers.ai_candidate_proposal.groq_adapter`'s structure
# (same `langchain_groq.ChatGroq` structured-output pattern, same lazy
# import, same no-key-never-crashes contract) but is a completely
# separate feature: this adapter only ever turns an
# `ItineraryNarrativeRequest` (a strict allow-list of already-computed
# `PlanningState` fields) into prose, never a candidate proposal, and it
# is gated by `Settings.itinerary_narrator_enabled`/
# `itinerary_narrator_provider`, never
# `ai_candidate_discovery_shadow_mode_enabled`/
# `ai_candidate_proposal_provider`.
#
# The structured-output schema below has no field for a price, rating,
# review count, route/travel-time duration, booking link, availability
# flag, opening hour, or clock time -- narrative text and caveats are the
# only content fields. Every day's real `date` is taken from `request`
# itself (looked up by the LLM-returned `day_number`), never from
# anything the model could hallucinate, and a `day_number` the request
# doesn't actually contain is dropped rather than invented into a new day.
#
# The `langchain_groq` package import is deliberately deferred into
# `_build_client` (not a module-level import), matching the AI
# candidate-proposal Groq adapter exactly, so the rest of the app -- and
# every test that injects a fake client or exercises the no-key path --
# keeps working whether or not the package is installed.


class _NarratorDayOutputSchema(BaseModel):
    """Section 195 lesson (same as the Section 191A.1/193B ones): Groq's
    `json_schema` strict mode requires every property to appear in the
    schema's `required` array -- a field with `default_factory=...`
    (making it Python-optional) is silently excluded from `required` by
    `langchain_groq`'s schema conversion, and Groq's own strict-mode
    validator then rejects the whole request outright (`400 invalid JSON
    schema for response_format`) before any completion is even
    attempted. Every field below therefore has no Python default at all
    -- always required, with the prompt instructed (matching
    `_GroqItineraryReasoningSchema`'s own established convention) to use
    an empty list/null when there is nothing to report, never to omit
    the key.
    """

    day_number: int = Field(description="Must match one of the day_number values given in the input.")
    title: str = Field(description="A short, traveler-facing title for this day.")
    narrative: str = Field(
        description="Polished prose summarizing this day's scheduled places. Never invent a "
        "hotel, flight, price, rating, review count, route/travel-time duration, booking "
        "confirmation, or clock time not already present in the input."
    )
    caveats: list[str] = Field(
        description="Short caveats to preserve, e.g. when movement/route data wasn't "
        "available. Always include this key; use an empty list if there are none."
    )
    referenced_experience_ids: list[str] = Field(
        description="The experience_id value(s) (given alongside each scheduled place below) "
        "this day's narrative is actually about. Never an id from a different day, and never "
        "an invented id. Always include this key; use an empty list if unsure."
    )


class _NarratorBatchSchema(BaseModel):
    summary: str = Field(description="A short, trip-level polished summary.")
    daily_narratives: list[_NarratorDayOutputSchema] = Field(
        description="One entry per day. Always include this key; use an empty list if none apply."
    )
    assumptions: list[str] = Field(
        description="Always include this key. Use an empty list if there are none."
    )
    warnings: list[str] = Field(
        description="Always include this key. Use an empty list if there are none."
    )


_SYSTEM_PROMPT = (
    "You are an itinerary narrator for a travel planning system. You write polished, "
    "traveler-facing prose that summarizes an already-finalized draft itinerary -- you are "
    "a writer, never a source of new travel facts, and you never make a new planning "
    "decision.\n\n"
    "Use only the information given to you below. Do not invent, guess, or embellish a "
    "hotel, flight, price, rating, review count, route or travel-time duration, booking "
    "confirmation, opening hour, or clock time that isn't already present in the input. If "
    "something is marked unavailable or missing, acknowledge that honestly in a caveat "
    "instead of inventing a replacement fact. Never claim a place is booked, a route exists, "
    "or a plan is finalized/production-ready/optimal/verified/guaranteed/the safest option. "
    "Every day_number you return must exactly match one of the day_number values given in "
    "the input -- never invent a new day.\n\n"
    "Each scheduled place below is given with its own experience_id. For each day, set "
    "referenced_experience_ids to exactly the experience_id value(s) your narrative for that "
    "day is actually about -- never an id from a different day, and never an id you made up.\n\n"
    "Some days include an 'AI reasoning rationale' line -- this is context explaining why "
    "those places were grouped together, not a verified fact. You may draw on it for tone/"
    "phrasing, but every specific factual claim you make must still come from the scheduled "
    "places themselves. If a day has no rationale, do not invent one.\n\n"
    "If 'Plan adjusted after feasibility checks' is stated, you may mention once, briefly and "
    "in plain traveler-facing language, that the plan was adjusted after feasibility checks "
    "(e.g. 'the plan was adjusted after feasibility checks to keep the days manageable') -- "
    "never mention any internal system, model, or provider name, never a repair attempt "
    "number, and never say this when that line is absent."
)


def _build_prompt(request: ItineraryNarrativeRequest) -> str:
    """Builds a minimal, controlled prompt from `request` fields only --
    the exact same allow-listed fields `ItineraryNarrativeRequest` itself
    carries, nothing else.
    """
    lines = [
        _SYSTEM_PROMPT,
        "",
        f"Destination: {request.destination}",
        f"Dates: {request.start_date} to {request.end_date}",
        f"Travelers: {request.travelers_count}"
        + (f" ({request.travel_group_type})" if request.travel_group_type else ""),
        f"Pace: {request.pace or 'unspecified'}",
        f"Interests: {', '.join(request.interests) if request.interests else 'none specified'}",
    ]
    if request.weather_available and request.weather_summary:
        lines.append(f"Weather: {request.weather_summary}")
    if request.stay_area_names:
        lines.append(f"Stay-area ideas under consideration: {', '.join(request.stay_area_names)}")
    lines.append(f"Bookable accommodation offers on file: {request.accommodation_offer_count}")
    lines.append(f"Bookable flight offers on file: {request.flight_offer_count}")
    if request.validation_status:
        lines.append(
            f"Validation status: {request.validation_status} "
            f"({request.critical_issue_count} critical issue(s), {request.warning_count} warning(s))"
        )
    if request.unavailable_data_fields:
        lines.append(f"Known-unavailable data: {', '.join(request.unavailable_data_fields)}")
    if request.reasoning_strategy_summary:
        lines.append(f"Overall AI reasoning strategy (context only): {request.reasoning_strategy_summary}")
    if request.was_adjusted_after_feasibility_checks:
        lines.append("Plan adjusted after feasibility checks: yes")

    lines.append("")
    lines.append("Days:")
    for day in request.days:
        lines.append(f"- day_number {day.day_number} ({day.date}):")
        if day.experiences:
            for experience in day.experiences:
                piece = f"[experience_id={experience.experience_id}] {experience.name}"
                if experience.category:
                    piece += f" ({experience.category})"
                if experience.reason:
                    piece += f" -- {experience.reason}"
                lines.append(f"    * {piece}")
        else:
            lines.append("    * No scheduled places for this day.")
        if day.restaurant_names:
            lines.append(f"    Nearby food ideas: {', '.join(day.restaurant_names)}")
        lines.append(
            f"    Movement/route data available: {'yes' if day.has_movement_data else 'no'}"
        )
        if day.reasoning_rationale:
            lines.append(f"    AI reasoning rationale for this day (context only): {day.reasoning_rationale}")

    if request.truncated:
        lines.append("")
        lines.append(
            "Note: this input was truncated for length -- some days/items were omitted."
        )

    return "\n".join(lines)


class GroqItineraryNarratorProvider(ItineraryNarratorProvider):
    """`ItineraryNarratorProvider` implementation backed by Groq (via
    `langchain_groq.ChatGroq`), used only when explicitly config-gated in
    via `get_itinerary_narrator_provider` -- never the default.

    `narrate` never invents a hotel, flight, price, rating, review count,
    route/travel-time duration, booking confirmation, or clock time: the
    structured-output schema Groq must respond through has no such
    field, every day's real date is taken from `request` (never the
    model's own output), and a `day_number` the request doesn't contain
    is dropped. If validation fails, the call raises, or no usable
    structured output comes back, this returns an honest `failed` result
    with no narrative -- never a fabricated one.
    """

    provider_name = "groq_itinerary_narrator_provider"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        max_tokens: int = 2500,
        temperature: float = 0.4,
    ) -> None:
        settings = get_settings()

        self._client = client
        if api_key is None and client is None:
            self._api_key = settings.groq_api_key
        else:
            self._api_key = api_key
        self._model = (
            model
            if model is not None
            else (settings.itinerary_narrator_model or settings.groq_model)
        )
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout_seconds = settings.itinerary_narrator_timeout_seconds

    def narrate(self, request: ItineraryNarrativeRequest) -> ItineraryNarrativeReport:
        if self._client is None and not self._api_key:
            return self._not_connected_result(
                "Groq API key is not configured (GROQ_API_KEY unset)."
            )

        if self._client is not None:
            client = self._client
        else:
            try:
                client = self._build_client()
            except Exception as exc:  # missing package / bad config -> not_connected
                return self._not_connected_result(f"Groq client could not be initialized: {exc}")

        try:
            raw_output = client.invoke(_build_prompt(request))
        except Exception as exc:  # API/runtime/timeout failure -> failed, never fabricated
            return self._failed_result(f"Groq API call failed: {exc}")

        output_dict = self._coerce_output(raw_output)
        if output_dict is None:
            return self._failed_result("Groq did not return a structured response.")

        return self._build_result(request, output_dict)

    def _build_client(self) -> Any:
        """Lazily imports and constructs the real Groq client, bound to
        the structured-output schema. Kept inside a method (never a
        module-level import) so the rest of the app imports cleanly
        whether or not the `langchain_groq` package is installed, and so
        tests never need it installed either.
        """
        try:
            from langchain_groq import ChatGroq
        except ImportError as exc:
            raise RuntimeError("The 'langchain_groq' package is not installed.") from exc

        chat = ChatGroq(
            model=self._model,
            api_key=self._api_key,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            timeout=self._timeout_seconds,
        )
        # Section 195 (Task 18): switched to Structured Outputs
        # (`method="json_schema", strict=True`) -- the Section 191A.1
        # fix this adapter had not yet adopted -- now that its output
        # contract is changing anyway (`referenced_experience_ids`). No
        # `tools`/`tool_choice` sent alongside it.
        return chat.with_structured_output(_NarratorBatchSchema, method="json_schema", strict=True)

    @staticmethod
    def _coerce_output(raw_output: Any) -> dict[str, Any] | None:
        if isinstance(raw_output, dict):
            return raw_output
        model_dump = getattr(raw_output, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
            except Exception:
                return None
            return dumped if isinstance(dumped, dict) else None
        return None

    def _build_result(
        self, request: ItineraryNarrativeRequest, output: dict[str, Any]
    ) -> ItineraryNarrativeReport:
        summary = output.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return self._failed_result("Groq output did not include a usable summary.")

        real_dates_by_day_number = {day.day_number: day.date for day in request.days}

        raw_daily = output.get("daily_narratives")
        if not isinstance(raw_daily, list):
            return self._failed_result("Groq output did not include a daily_narratives list.")

        daily_narratives: list[ItineraryNarrativeDayOutput] = []
        for raw_day in raw_daily:
            if not isinstance(raw_day, dict):
                return self._failed_result("Groq output contained a malformed day entry.")
            day_number = raw_day.get("day_number")
            real_date = real_dates_by_day_number.get(day_number)
            if real_date is None:
                # The model referenced a day that isn't in our own
                # request -- dropped rather than invented into existence.
                continue
            try:
                daily_narratives.append(
                    ItineraryNarrativeDayOutput(
                        day_number=day_number,
                        date=real_date,
                        title=raw_day.get("title", ""),
                        narrative=raw_day.get("narrative", ""),
                        caveats=[c for c in raw_day.get("caveats", []) if isinstance(c, str)],
                        referenced_experience_ids=[
                            i for i in raw_day.get("referenced_experience_ids", []) if isinstance(i, str)
                        ],
                    )
                )
            except ValidationError as exc:
                return self._failed_result(f"Groq day output failed validation: {exc}")

        assumptions = [a for a in output.get("assumptions", []) if isinstance(a, str)]
        warnings = [w for w in output.get("warnings", []) if isinstance(w, str)]
        if request.truncated:
            assumptions = assumptions + [
                "Input was truncated for length; some days/items were omitted from the narrator's view."
            ]

        try:
            report = ItineraryNarrativeReport(
                status=ItineraryNarrativeStatus.SUCCESS,
                provider=self.provider_name,
                model=self._model,
                summary=summary.strip(),
                daily_narratives=daily_narratives,
                warnings=warnings,
                assumptions=assumptions,
                source_fields_used=_source_fields_used(request),
                generated_at=datetime.now(timezone.utc),
            )
        except ValidationError as exc:
            return self._failed_result(f"Groq output failed validation: {exc}")

        # Section 195 (Task 12): structural place-identity safety check --
        # even schema-valid, domain-valid output must still be checked
        # against request.days' own real experience_id sets before this
        # adapter can ever report success.
        violations = validate_narrative_against_request(request, report)
        if violations:
            return self._failed_result(
                "Groq output referenced an experience_id outside that day's real scheduled "
                "items: " + "; ".join(violations)
            )

        return report

    def _not_connected_result(self, reason: str) -> ItineraryNarrativeReport:
        return ItineraryNarrativeReport(
            status=ItineraryNarrativeStatus.NOT_CONNECTED,
            provider=self.provider_name,
            model=self._model,
            message=reason,
        )

    def _failed_result(self, reason: str) -> ItineraryNarrativeReport:
        return ItineraryNarrativeReport(
            status=ItineraryNarrativeStatus.FAILED,
            provider=self.provider_name,
            model=self._model,
            message=reason,
        )


def _source_fields_used(request: ItineraryNarrativeRequest) -> list[str]:
    """Names which allow-listed `ItineraryNarrativeRequest` fields were
    actually populated for this call -- Developer-view transparency
    bookkeeping only, never a claim about output accuracy.
    """
    used = ["destination", "start_date", "end_date", "travelers_count", "days"]
    if request.travel_group_type:
        used.append("travel_group_type")
    if request.pace:
        used.append("pace")
    if request.interests:
        used.append("interests")
    if request.weather_available:
        used.append("weather_summary")
    if request.stay_area_names:
        used.append("stay_area_names")
    if request.accommodation_offer_count:
        used.append("accommodation_offer_count")
    if request.flight_offer_count:
        used.append("flight_offer_count")
    if request.validation_status:
        used.append("validation_status")
    if request.unavailable_data_fields:
        used.append("unavailable_data_fields")
    if request.reasoning_strategy_summary:
        used.append("reasoning_strategy_summary")
    if any(day.reasoning_rationale for day in request.days):
        used.append("reasoning_rationale")
    if request.was_adjusted_after_feasibility_checks:
        used.append("was_adjusted_after_feasibility_checks")
    return used
