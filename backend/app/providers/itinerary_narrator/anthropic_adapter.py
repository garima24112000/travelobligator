from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.itinerary_narrative import (
    ItineraryNarrativeDayOutput,
    ItineraryNarrativeReport,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
)
from app.providers.itinerary_narrator.base import ItineraryNarratorProvider

# Anthropic/Claude-backed itinerary narrator adapter (Step 182F,
# docs/13_llm_reasoning_pipeline.md, docs/14_backend_architecture.md).
# Mirrors `app.providers.ai_candidate_proposal.anthropic_adapter`'s
# structure (same Anthropic Messages API forced tool-use pattern, same
# lazy import, same no-key-never-crashes contract) but is a completely
# separate feature -- see `groq_adapter.py`'s module docstring for the
# full rationale (identical here, just a different SDK).
#
# The tool schema below has no field for a price, rating, review count,
# route/travel-time duration, booking link, availability flag, opening
# hour, or clock time -- narrative text and caveats are the only content
# fields. Every day's real `date` is taken from `request` itself (looked
# up by the returned `day_number`), never from anything the model could
# hallucinate, and a `day_number` the request doesn't actually contain is
# dropped rather than invented into a new day.

_TOOL_NAME = "submit_itinerary_narrative"

_DAY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "day_number": {
            "type": "integer",
            "description": "Must match one of the day_number values given in the input.",
        },
        "title": {"type": "string", "description": "A short, traveler-facing title for this day."},
        "narrative": {
            "type": "string",
            "description": "Polished prose summarizing this day's scheduled places. Never invent a "
            "hotel, flight, price, rating, review count, route/travel-time duration, booking "
            "confirmation, or clock time not already present in the input.",
        },
        "caveats": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short caveats to preserve, e.g. when movement/route data wasn't available.",
        },
    },
    "required": ["day_number", "title", "narrative"],
    "additionalProperties": False,
}

_TOOL_DEFINITION: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Submit polished, traveler-facing prose summarizing an already-finalized draft "
        "itinerary. Never invent a hotel, flight, price, rating, review count, "
        "route/travel-time duration, booking confirmation, opening hour, or clock time not "
        "already present in the input. Every day_number must exactly match one given in the "
        "input -- never invent a new day."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "A short, trip-level polished summary."},
            "daily_narratives": {"type": "array", "items": _DAY_OUTPUT_SCHEMA},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "daily_narratives"],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = (
    "You are an itinerary narrator for a travel planning system. You write polished, "
    "traveler-facing prose that summarizes an already-finalized draft itinerary -- you are "
    "a writer, never a source of new travel facts.\n\n"
    "Use only the information given to you below. You MUST call the "
    "submit_itinerary_narrative tool to respond, and your output must match its schema "
    "exactly. Do not invent, guess, or embellish a hotel, flight, price, rating, review "
    "count, route or travel-time duration, booking confirmation, opening hour, or clock time "
    "that isn't already present in the input. If something is marked unavailable or missing, "
    "acknowledge that honestly in a caveat instead of inventing a replacement fact. Never "
    "claim a place is booked, a route exists, or a plan is finalized/production-ready."
)


def _build_prompt(request: ItineraryNarrativeRequest) -> str:
    """Builds a minimal, controlled prompt from `request` fields only --
    the exact same allow-listed fields `ItineraryNarrativeRequest` itself
    carries, nothing else. Mirrors the Groq adapter's own `_build_prompt`
    exactly (kept as a separate copy, matching this codebase's existing
    AI candidate-proposal adapter convention of not sharing prompt
    builders across providers).
    """
    lines = [
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

    lines.append("")
    lines.append("Days:")
    for day in request.days:
        lines.append(f"- day_number {day.day_number} ({day.date}):")
        if day.experiences:
            for experience in day.experiences:
                piece = experience.name
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

    if request.truncated:
        lines.append("")
        lines.append(
            "Note: this input was truncated for length -- some days/items were omitted."
        )

    return "\n".join(lines)


class AnthropicItineraryNarratorProvider(ItineraryNarratorProvider):
    """`ItineraryNarratorProvider` implementation backed by the Anthropic
    Messages API (Claude), used only when explicitly config-gated in via
    `get_itinerary_narrator_provider` -- never the default.

    `narrate` never invents a hotel, flight, price, rating, review count,
    route/travel-time duration, booking confirmation, or clock time: the
    tool schema Claude must respond through has no such field, every
    day's real date is taken from `request` (never the model's own
    output), and a `day_number` the request doesn't contain is dropped.
    If validation fails, the call raises, or no usable structured output
    comes back, this returns an honest `failed` result -- never a
    fabricated one.
    """

    provider_name = "anthropic_itinerary_narrator_provider"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        max_tokens: int = 2000,
        temperature: float = 0.4,
    ) -> None:
        settings = get_settings()

        self._client = client
        if api_key is None and client is None:
            self._api_key = settings.anthropic_api_key
        else:
            self._api_key = api_key
        self._model = (
            model
            if model is not None
            else (settings.itinerary_narrator_model or settings.anthropic_model)
        )
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout_seconds = settings.itinerary_narrator_timeout_seconds

    def narrate(self, request: ItineraryNarrativeRequest) -> ItineraryNarrativeReport:
        if self._client is None and not self._api_key:
            return self._not_connected_result(
                "Anthropic API key is not configured (ANTHROPIC_API_KEY unset)."
            )

        if self._client is not None:
            client = self._client
        else:
            try:
                client = self._build_client()
            except Exception as exc:  # missing package / bad config -> not_connected
                return self._not_connected_result(
                    f"Anthropic client could not be initialized: {exc}"
                )

        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                timeout=self._timeout_seconds,
                system=_SYSTEM_PROMPT,
                tools=[_TOOL_DEFINITION],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[{"role": "user", "content": _build_prompt(request)}],
            )
        except Exception as exc:  # API/runtime/timeout failure -> failed, never fabricated
            return self._failed_result(f"Anthropic API call failed: {exc}")

        tool_input = self._extract_tool_input(response)
        if tool_input is None:
            return self._failed_result("Claude did not return a structured tool_use response.")

        return self._build_result(request, tool_input)

    @staticmethod
    def _build_client() -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("The 'anthropic' package is not installed.") from exc
        return anthropic.Anthropic()

    @staticmethod
    def _extract_tool_input(response: Any) -> dict[str, Any] | None:
        content = getattr(response, "content", None) or []
        for block in content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if getattr(block, "name", None) != _TOOL_NAME:
                continue
            tool_input = getattr(block, "input", None)
            if isinstance(tool_input, dict):
                return tool_input
        return None

    def _build_result(
        self, request: ItineraryNarrativeRequest, tool_input: dict[str, Any]
    ) -> ItineraryNarrativeReport:
        summary = tool_input.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return self._failed_result("Tool output did not include a usable summary.")

        real_dates_by_day_number = {day.day_number: day.date for day in request.days}

        raw_daily = tool_input.get("daily_narratives")
        if not isinstance(raw_daily, list):
            return self._failed_result("Tool output did not include a daily_narratives list.")

        daily_narratives: list[ItineraryNarrativeDayOutput] = []
        for raw_day in raw_daily:
            if not isinstance(raw_day, dict):
                return self._failed_result("Tool output contained a malformed day entry.")
            day_number = raw_day.get("day_number")
            real_date = real_dates_by_day_number.get(day_number)
            if real_date is None:
                continue
            try:
                daily_narratives.append(
                    ItineraryNarrativeDayOutput(
                        day_number=day_number,
                        date=real_date,
                        title=raw_day.get("title", ""),
                        narrative=raw_day.get("narrative", ""),
                        caveats=[c for c in raw_day.get("caveats", []) if isinstance(c, str)],
                    )
                )
            except ValidationError as exc:
                return self._failed_result(f"Tool day output failed validation: {exc}")

        assumptions = [a for a in tool_input.get("assumptions", []) if isinstance(a, str)]
        warnings = [w for w in tool_input.get("warnings", []) if isinstance(w, str)]
        if request.truncated:
            assumptions = assumptions + [
                "Input was truncated for length; some days/items were omitted from the narrator's view."
            ]

        return ItineraryNarrativeReport(
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
    """Mirrors `groq_adapter._source_fields_used` exactly -- kept as a
    separate copy per this module's own docstring on adapter
    independence."""
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
    return used
