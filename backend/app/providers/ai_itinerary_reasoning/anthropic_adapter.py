from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.providers.ai_failure import classify_and_message
from app.core.config import get_settings
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningRequest,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
    ItineraryReasoningCandidatePlacement,
    ItineraryReasoningDayPlan,
    ItineraryReasoningStrategy,
    ItineraryReasoningTimeWindow,
    validate_result_against_request,
)
from app.models.ai_itinerary_repair import (
    AIItineraryRepairRequest,
    AIItineraryRepairResult,
    AIItineraryRepairStatus,
    RepairableIssueType,
    validate_repair_result_against_request,
)
from app.providers.ai_itinerary_reasoning.base import AIItineraryReasoningProvider

# Anthropic/Claude-backed itinerary-reasoning adapter (Section 193B,
# docs/14_backend_architecture.md section 142). Same tool-use pattern
# `app.providers.ai_candidate_proposal.anthropic_adapter.
# AnthropicAICandidateProposalProvider` already established -- forced
# tool use via `client.messages.create(..., tools=[...],
# tool_choice={"type": "tool", ...})`, never the Claude Code CLI or any
# local coding-agent runtime.
#
# This adapter still only ever produces `AIItineraryReasoningResult`
# objects; every candidate_id referenced in a `completed` result is
# checked against `request.allowed_candidates` via
# `validate_result_against_request` (Section 193A) before `completed` is
# ever reported -- a hallucinated/duplicate/out-of-range candidate
# reference downgrades the result to `rejected` instead.
#
# The `anthropic` package import is deliberately deferred into
# `_build_client` (not a module-level import), matching every other
# Anthropic adapter in this repo, so the rest of the app imports cleanly
# whether or not the package is installed, and so tests never need it
# installed either. A missing `ANTHROPIC_API_KEY` (and, for the same
# reason, a missing `anthropic` package) never crashes the app: `reason`
# returns an honest `not_connected` result instead.

_TOOL_NAME = "submit_ai_itinerary_reasoning"

_CANDIDATE_PLACEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidate_id": {
            "type": "string",
            "description": "Must be one of the candidate_id values from allowed_candidates.",
        },
        "time_window": {
            "type": "string",
            "enum": [member.value for member in ItineraryReasoningTimeWindow],
        },
    },
    "required": ["candidate_id", "time_window"],
    "additionalProperties": False,
}

_DAY_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "day_index": {"type": "integer", "minimum": 1, "description": "1-based day number."},
        "candidate_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "candidate_id values (from allowed_candidates only) scheduled on "
            "this day. Never invent a new id; never repeat a candidate_id used on another day.",
        },
        "rationale": {
            "type": "string",
            "description": "One or two sentences explaining this day's grouping -- no price, "
            "rating, hours, or exact route-time claims.",
        },
        "tradeoffs": {"type": "string", "description": "Optional tradeoff note for this day."},
        "approximate_structure": {
            "type": "array",
            "items": _CANDIDATE_PLACEMENT_SCHEMA,
            "description": "Optional coarse time-of-day placement for candidates on this day.",
        },
    },
    "required": ["day_index", "candidate_ids", "rationale"],
    "additionalProperties": False,
}

_STRATEGY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One or two sentences summarizing the plan."},
        "pace": {"type": "string", "description": "One of: relaxed, balanced, packed."},
        "reason": {"type": "string", "description": "Why this pace/strategy fits the traveler."},
    },
    "required": ["summary", "pace", "reason"],
    "additionalProperties": False,
}

_TOOL_DEFINITION: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Submit a structured itinerary-reasoning proposal that selects, groups, and "
        "coarsely orders only the provider-backed candidates supplied in "
        "allowed_candidates. Every candidate_id must come from allowed_candidates -- never "
        "invent a new place or id. Do not include coordinates, prices, ratings, opening "
        "hours, availability, booking links, or exact route times/distances anywhere in "
        "your output."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "strategy": _STRATEGY_SCHEMA,
            "days": {"type": "array", "items": _DAY_PLAN_SCHEMA},
            "overall_tradeoffs": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["strategy", "days", "confidence"],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = (
    "You are selecting and organizing only the provider-backed candidates supplied in "
    "allowed_candidates. You are not a source of travel facts -- every candidate you "
    "reference was already verified by a real provider before you saw it.\n\n"
    "Use candidate_id exactly as provided in allowed_candidates. Do not invent new "
    "candidate IDs or new places, and never reference a place by name only. You MUST call "
    "the submit_ai_itinerary_reasoning tool to respond, and your output must match its "
    "schema exactly.\n\n"
    "Do not infer missing provider facts. Do not claim exact route times, prices, "
    "ratings, availability, opening hours, or booking information unless explicitly "
    "supplied to you (it will not be).\n\n"
    "Use coarse time windows only (morning/midday/afternoon/evening) -- never an exact "
    "clock time or a specific transfer duration.\n\n"
    "Optimize for the traveler's stated interests, pace, trip duration, and constraints, "
    "using each candidate's quality_score/quality_tier as a pre-ranking signal. "
    "When several requested interests have candidates whose matched_interests include them, represent each such interest at least once across the trip, without breaking geography or pace. normalized_category and matched_interests are provider-derived facts -- never assign or infer them yourself. A "
    "candidate should appear on at most one day."
)


def _format_candidate_line(candidate: Any) -> str:
    line = (
        f"- candidate_id={candidate.candidate_id!r} name={candidate.name!r} "
        f"category={candidate.category.value} quality_tier={candidate.quality_tier} "
        f"quality_score={candidate.quality_score:.2f}"
    )
    # Section 202B.2 (Task 11): deterministic, provider-derived evidence only.
    normalized_category = getattr(candidate, "normalized_category", None)
    if normalized_category:
        line += f" normalized_category={normalized_category}"
    matched = getattr(candidate, "matched_interests", None)
    if matched:
        line += f" matched_interests={list(matched)}"
    return line


def _build_prompt(request: AIItineraryReasoningRequest) -> str:
    """Builds a minimal, controlled prompt from `request` fields only --
    never a raw `PlanningState` dump, and never a coordinate."""
    traveler = request.traveler_context
    lines = [
        *[f"Instruction: {instruction}" for instruction in request.reasoning_instructions],
        "",
        f"Destination: {request.destination_name}",
        f"Trip dates: {request.start_date} to {request.end_date} "
        f"({request.trip_duration_days} day(s))",
        f"Travelers: {traveler.travelers_count} ({traveler.travel_group_type})",
        f"Pace: {traveler.pace}",
        f"Interests: {', '.join(traveler.interests) if traveler.interests else 'none specified'}",
        f"Must-visit: {', '.join(traveler.must_visit) if traveler.must_visit else 'none specified'}",
        f"Must-avoid: {', '.join(traveler.must_avoid) if traveler.must_avoid else 'none specified'}",
        f"Constraints: {', '.join(traveler.constraints) if traveler.constraints else 'none specified'}",
    ]
    if request.trip_strategy_summary is not None:
        strategy = request.trip_strategy_summary
        lines.append(
            "Existing trip strategy: "
            f"style={strategy.recommended_trip_style or 'unspecified'}, "
            f"planning_strategy={strategy.planning_strategy}, tradeoffs={strategy.tradeoffs}"
        )
    lines.append(f"Factual context: {request.factual_context.model_dump()}")
    lines.append(
        f"Allowed candidates (use only these candidate_id values, "
        f"{len(request.allowed_candidates)} total):"
    )
    lines.extend(_format_candidate_line(candidate) for candidate in request.allowed_candidates)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 194A: repair tool schema/prompt. Same forced-tool-use pattern as
# `reason` above. The tool schema is deliberately narrower than
# `_TOOL_DEFINITION`: only the repaired day(s), never a full day list,
# mirroring `AIItineraryRepairResult.repaired_days`.
# ---------------------------------------------------------------------------

_REPAIR_TOOL_NAME = "submit_ai_itinerary_repair"

_REPAIRED_DAY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "day_index": {
            "type": "integer",
            "minimum": 1,
            "description": "Must be one of the day_index values in affected_days.",
        },
        "candidate_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "candidate_id values (from allowed_candidates only) for this "
            "repaired day. Never invent a new id.",
        },
        "rationale": {
            "type": "string",
            "description": "One or two sentences explaining this day's revised grouping -- "
            "no price, rating, hours, or exact route-time claims.",
        },
        "tradeoffs": {"type": "string", "description": "Optional tradeoff note for this day."},
        "approximate_structure": {
            "type": "array",
            "items": _CANDIDATE_PLACEMENT_SCHEMA,
            "description": "Optional coarse time-of-day placement for candidates on this day.",
        },
    },
    "required": ["day_index", "candidate_ids", "rationale"],
    "additionalProperties": False,
}

_REPAIR_TOOL_DEFINITION: dict[str, Any] = {
    "name": _REPAIR_TOOL_NAME,
    "description": (
        "Submit a structured repair of only the affected day(s) of an existing itinerary, "
        "in response to deterministic validation findings. Every candidate_id must come "
        "from allowed_candidates -- never invent a new place or id. Never repair a day "
        "outside affected_days. Do not include coordinates, prices, ratings, opening "
        "hours, availability, booking links, or exact route times/distances anywhere in "
        "your output."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "repaired_days": {
                "type": "array",
                "items": _REPAIRED_DAY_SCHEMA,
                "description": "Only the day(s) you actually changed.",
            },
            "repair_summary": {
                "type": "string",
                "description": "One or two sentences summarizing what changed and why.",
            },
            "addressed_issue_types": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["repaired_days", "repair_summary", "confidence"],
        "additionalProperties": False,
    },
}

_REPAIR_SYSTEM_PROMPT = (
    "You are repairing an existing itinerary in response to deterministic validation "
    "findings. The validator is authoritative about what is wrong -- you are not being "
    "asked to judge feasibility yourself, only to choose a better arrangement of the "
    "already-verified candidates supplied in allowed_candidates.\n\n"
    "Use candidate_id exactly as provided in allowed_candidates. Do not invent new "
    "candidate IDs or new places, and never reference a place by name only. You MUST call "
    "the submit_ai_itinerary_repair tool to respond, and your output must match its schema "
    "exactly.\n\n"
    "Modify only the day_index values listed in affected_days. Do not restate or alter any "
    "day not in affected_days.\n\n"
    "Do not infer missing provider facts. Do not claim exact route times, prices, "
    "ratings, availability, opening hours, or booking information.\n\n"
    "Prefer the smallest change that resolves the supplied issues."
)


def _format_issue_line(issue: Any) -> str:
    return (
        f"- day {issue.day_index}: issue_type={issue.issue_type.value!r} "
        f"severity={issue.severity.value} message={issue.message!r}"
    )


def _build_repair_prompt(request: AIItineraryRepairRequest) -> str:
    """Builds a minimal, controlled repair prompt from `request` fields
    only -- never a raw `PlanningState` dump, and never a coordinate."""
    traveler = request.traveler_context
    lines = [
        *[f"Instruction: {instruction}" for instruction in request.repair_instructions],
        "",
        f"Destination: {request.destination_name}",
        f"Trip dates: {request.start_date} to {request.end_date} "
        f"({request.trip_duration_days} day(s))",
        f"Travelers: {traveler.travelers_count} ({traveler.travel_group_type})",
        f"Pace: {traveler.pace}",
        f"Repair attempt number: {request.attempt_number}",
        f"Affected days (you may only change these): {request.affected_days}",
        "Validator findings to address:",
    ]
    lines.extend(_format_issue_line(issue) for issue in request.issues)
    lines.append("Original day plans (unaffected days must come back unchanged, i.e. omitted):")
    for day in request.original_days:
        lines.append(f"- day {day.day_index}: candidate_ids={day.candidate_ids}")
    lines.append(
        f"Allowed candidates (use only these candidate_id values, "
        f"{len(request.allowed_candidates)} total, same universe as the original plan):"
    )
    lines.extend(_format_candidate_line(candidate) for candidate in request.allowed_candidates)
    return "\n".join(lines)


class AnthropicAIItineraryReasoningProvider(AIItineraryReasoningProvider):
    """`AIItineraryReasoningProvider` implementation backed by the
    Anthropic Messages API (Claude), used only when explicitly
    config-gated in via `get_ai_itinerary_reasoning_provider` -- never the
    default.

    `reason` never invents a candidate_id, place, coordinate, price,
    rating, opening hour, availability claim, booking link, or route
    time/distance: the tool schema Claude must respond through has no
    such fields, every parsed strategy/day is still validated through
    `ItineraryReasoningStrategy`/`ItineraryReasoningDayPlan`, and every
    candidate_id referenced is checked against
    `request.allowed_candidates` via `validate_result_against_request`
    before `completed` is ever reported.
    """

    provider_name = "anthropic_ai_itinerary_reasoning_provider"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        max_tokens: int = 4000,
        temperature: float = 0.2,
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
            else (settings.ai_itinerary_reasoning_model or settings.anthropic_model)
        )
        self._max_tokens = max_tokens
        self._temperature = temperature

    def reason(self, request: AIItineraryReasoningRequest) -> AIItineraryReasoningResult:
        if self._client is None and not self._api_key:
            return self._not_connected_result(
                request, "Anthropic API key is not configured (ANTHROPIC_API_KEY unset)."
            )

        if self._client is not None:
            client = self._client
        else:
            try:
                client = self._build_client()
            except Exception as exc:  # missing package / bad config -> not_connected
                return self._not_connected_result(
                    request, f"Anthropic client could not be initialized: {exc}"
                )

        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                system=_SYSTEM_PROMPT,
                tools=[_TOOL_DEFINITION],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[{"role": "user", "content": _build_prompt(request)}],
            )
        except Exception as exc:  # API/runtime failure -> rejected, never fabricated
            kind, message = classify_and_message("Anthropic", exc)
            return self._rejected_result(request, message, failure_kind=kind.value)

        tool_input = self._extract_tool_input(response)
        if tool_input is None:
            return self._rejected_result(
                request, "Claude did not return a structured tool_use response."
            )

        return self._build_result_from_tool_input(request, tool_input)

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

    def _build_result_from_tool_input(
        self, request: AIItineraryReasoningRequest, tool_input: dict[str, Any]
    ) -> AIItineraryReasoningResult:
        raw_strategy = tool_input.get("strategy")
        if not isinstance(raw_strategy, dict):
            return self._rejected_result(request, "Tool output did not include a strategy.")
        try:
            strategy = ItineraryReasoningStrategy(**raw_strategy)
        except ValidationError as exc:
            return self._rejected_result(request, f"Tool strategy failed validation: {exc}")

        raw_days = tool_input.get("days")
        if not isinstance(raw_days, list):
            return self._rejected_result(request, "Tool output did not include a days list.")

        days: list[ItineraryReasoningDayPlan] = []
        for raw_day in raw_days:
            if not isinstance(raw_day, dict):
                return self._rejected_result(request, "Tool output contained a malformed day entry.")
            try:
                placements = [
                    ItineraryReasoningCandidatePlacement(**placement)
                    for placement in raw_day.get("approximate_structure") or []
                ]
                days.append(
                    ItineraryReasoningDayPlan(
                        day_index=raw_day.get("day_index"),
                        candidate_ids=raw_day.get("candidate_ids") or [],
                        rationale=raw_day.get("rationale"),
                        tradeoffs=raw_day.get("tradeoffs"),
                        approximate_structure=placements,
                    )
                )
            except ValidationError as exc:
                return self._rejected_result(request, f"Tool output failed day validation: {exc}")

        if not days:
            return self._rejected_result(request, "Claude returned no scheduled days.")

        raw_overall_tradeoffs = tool_input.get("overall_tradeoffs") or []
        overall_tradeoffs = (
            [item for item in raw_overall_tradeoffs if isinstance(item, str)]
            if isinstance(raw_overall_tradeoffs, list)
            else []
        )

        confidence = tool_input.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = 0.0
        confidence = max(0.0, min(1.0, float(confidence)))

        try:
            result = AIItineraryReasoningResult(
                status=AIItineraryReasoningStatus.COMPLETED,
                strategy=strategy,
                days=days,
                overall_tradeoffs=overall_tradeoffs,
                guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
                provider_name=self.provider_name,
                model_name=self._model,
                confidence=confidence,
            )
        except ValidationError as exc:
            return self._rejected_result(request, f"Assembled reasoning result failed validation: {exc}")

        violations = validate_result_against_request(request, result)
        if violations:
            return self._rejected_result(
                request,
                "Claude output referenced candidate(s) outside the allowed set or violated "
                "day/candidate placement rules: " + "; ".join(violations),
            )

        return result

    def _not_connected_result(
        self, request: AIItineraryReasoningRequest, reason: str
    ) -> AIItineraryReasoningResult:
        return AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.NOT_CONNECTED,
            days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport(
                passed=False,
                blocked_reasons=[reason],
                checked_fields=["anthropic_api_key", "anthropic_client"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
        )

    def _rejected_result(
        self, request: AIItineraryReasoningRequest, reason: str, failure_kind: str | None = None
    ) -> AIItineraryReasoningResult:
        return AIItineraryReasoningResult(
            status=AIItineraryReasoningStatus.REJECTED,
            days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport(
                passed=False,
                blocked_reasons=[reason],
                checked_fields=["tool_use_output"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
            failure_kind=failure_kind,
        )

    # -----------------------------------------------------------------
    # Section 194A: repair. Same client/API-key resolution as `reason`
    # above (Task 9: reuse, never duplicate).
    # -----------------------------------------------------------------

    def repair(self, request: AIItineraryRepairRequest) -> AIItineraryRepairResult:
        if self._client is None and not self._api_key:
            return self._not_connected_repair_result(
                request, "Anthropic API key is not configured (ANTHROPIC_API_KEY unset)."
            )

        if self._client is not None:
            client = self._client
        else:
            try:
                client = self._build_client()
            except Exception as exc:
                return self._not_connected_repair_result(
                    request, f"Anthropic client could not be initialized: {exc}"
                )

        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                system=_REPAIR_SYSTEM_PROMPT,
                tools=[_REPAIR_TOOL_DEFINITION],
                tool_choice={"type": "tool", "name": _REPAIR_TOOL_NAME},
                messages=[{"role": "user", "content": _build_repair_prompt(request)}],
            )
        except Exception as exc:  # API/runtime failure -> rejected, never fabricated
            return self._rejected_repair_result(request, classify_and_message("Anthropic", exc)[1])

        tool_input = self._extract_repair_tool_input(response)
        if tool_input is None:
            return self._rejected_repair_result(
                request, "Claude did not return a structured tool_use response."
            )

        return self._build_repair_result_from_tool_input(request, tool_input)

    @staticmethod
    def _extract_repair_tool_input(response: Any) -> dict[str, Any] | None:
        content = getattr(response, "content", None) or []
        for block in content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if getattr(block, "name", None) != _REPAIR_TOOL_NAME:
                continue
            tool_input = getattr(block, "input", None)
            if isinstance(tool_input, dict):
                return tool_input
        return None

    def _build_repair_result_from_tool_input(
        self, request: AIItineraryRepairRequest, tool_input: dict[str, Any]
    ) -> AIItineraryRepairResult:
        raw_days = tool_input.get("repaired_days")
        if not isinstance(raw_days, list):
            return self._rejected_repair_result(request, "Tool output did not include a repaired_days list.")

        repaired_days: list[ItineraryReasoningDayPlan] = []
        for raw_day in raw_days:
            if not isinstance(raw_day, dict):
                return self._rejected_repair_result(request, "Tool output contained a malformed day entry.")
            try:
                placements = [
                    ItineraryReasoningCandidatePlacement(**placement)
                    for placement in raw_day.get("approximate_structure") or []
                ]
                repaired_days.append(
                    ItineraryReasoningDayPlan(
                        day_index=raw_day.get("day_index"),
                        candidate_ids=raw_day.get("candidate_ids") or [],
                        rationale=raw_day.get("rationale"),
                        tradeoffs=raw_day.get("tradeoffs"),
                        approximate_structure=placements,
                    )
                )
            except ValidationError as exc:
                return self._rejected_repair_result(request, f"Tool output failed day validation: {exc}")

        if not repaired_days:
            return self._rejected_repair_result(request, "Claude returned no repaired days.")

        repair_summary = tool_input.get("repair_summary")
        if not isinstance(repair_summary, str) or not repair_summary.strip():
            return self._rejected_repair_result(request, "Tool output did not include a repair_summary.")

        raw_addressed = tool_input.get("addressed_issue_types") or []
        addressed_issue_types: list[RepairableIssueType] = []
        if isinstance(raw_addressed, list):
            for item in raw_addressed:
                try:
                    addressed_issue_types.append(RepairableIssueType(item))
                except ValueError:
                    continue

        confidence = tool_input.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = 0.0
        confidence = max(0.0, min(1.0, float(confidence)))

        try:
            result = AIItineraryRepairResult(
                status=AIItineraryRepairStatus.COMPLETED,
                repaired_days=repaired_days,
                repair_summary=repair_summary,
                addressed_issue_types=addressed_issue_types,
                guardrail_report=AIItineraryReasoningGuardrailReport(passed=True),
                provider_name=self.provider_name,
                model_name=self._model,
                confidence=confidence,
                attempt_number=request.attempt_number,
            )
        except ValidationError as exc:
            return self._rejected_repair_result(request, f"Assembled repair result failed validation: {exc}")

        violations = validate_repair_result_against_request(request, result)
        if violations:
            return self._rejected_repair_result(
                request,
                "Claude repair output referenced a candidate/day outside the allowed scope: "
                + "; ".join(violations),
            )

        return result

    def _not_connected_repair_result(
        self, request: AIItineraryRepairRequest, reason: str
    ) -> AIItineraryRepairResult:
        return AIItineraryRepairResult(
            status=AIItineraryRepairStatus.NOT_CONNECTED,
            repaired_days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport(
                passed=False,
                blocked_reasons=[reason],
                checked_fields=["anthropic_api_key", "anthropic_client"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
            attempt_number=request.attempt_number,
        )

    def _rejected_repair_result(
        self, request: AIItineraryRepairRequest, reason: str
    ) -> AIItineraryRepairResult:
        return AIItineraryRepairResult(
            status=AIItineraryRepairStatus.REJECTED,
            repaired_days=[],
            guardrail_report=AIItineraryReasoningGuardrailReport(
                passed=False,
                blocked_reasons=[reason],
                checked_fields=["tool_use_output"],
            ),
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
            attempt_number=request.attempt_number,
        )
