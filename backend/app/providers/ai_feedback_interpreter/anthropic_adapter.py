from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.providers.ai_failure import classify_and_message
from app.core.config import get_settings
from app.models.ai_feedback_interpretation import (
    AdjustInterestAction,
    AIFeedbackAction,
    AIFeedbackClarification,
    AIFeedbackInterpretationRequest,
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
    AIFeedbackPreserveScope,
    AIFeedbackScope,
    ChangePaceAction,
    GeneralInstructionAction,
    MoveExperienceAction,
    NewPlaceRequest,
    RegenerateDayAction,
    RemoveExperienceAction,
    validate_interpretation_against_request,
)
from app.providers.ai_feedback_interpreter.base import AIFeedbackInterpreterProvider

# Anthropic/Claude-backed AI feedback-interpreter adapter (Section 196,
# docs/14_backend_architecture.md, following section 146). Same forced
# tool-use pattern
# `app.providers.ai_itinerary_reasoning.anthropic_adapter.AnthropicAIItineraryReasoningProvider`
# already established. Same flat action-schema-with-a-`type`-tag
# rationale as the Groq adapter's own module comment (this SDK doesn't
# have Groq's strict-required-array quirk, but the flat shape is kept
# identical between both adapters for one shared domain-side parsing
# story).

_TOOL_NAME = "submit_ai_feedback_interpretation"

_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [
                "remove_experience",
                "move_experience",
                "change_pace",
                "adjust_interest",
                "regenerate_day",
                "general_instruction",
            ],
        },
        "experience_id": {
            "type": "string",
            "description": "Required for remove_experience/move_experience -- must be an "
            "experience_id from current_items.",
        },
        "target_day_index": {
            "type": "integer",
            "description": "Required for move_experience -- the day_index to move the item to.",
        },
        "pace": {
            "type": "string",
            "description": "Required for change_pace -- one of relaxed, balanced, packed.",
        },
        "category": {
            "type": "string",
            "description": "Required for adjust_interest -- a short category/interest name.",
        },
        "direction": {
            "type": "string",
            "enum": ["more", "less"],
            "description": "Required for adjust_interest.",
        },
        "day_index": {"type": "integer", "description": "Required for regenerate_day."},
        "instruction": {
            "type": "string",
            "description": "Required for regenerate_day -- a short, safe description of the "
            "day-specific change the user wants (e.g. 'make it more relaxed', 'add more food').",
        },
        "note": {"type": "string", "description": "Required for general_instruction."},
    },
    "required": ["type"],
    "additionalProperties": False,
}

_NEW_PLACE_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "The place name/description the user asked for."},
        "note": {"type": "string", "description": "Optional short context."},
    },
    "required": ["query"],
    "additionalProperties": False,
}

_PRESERVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "day_indices": {"type": "array", "items": {"type": "integer"}},
        "experience_ids": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

_CLARIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "possible_experience_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["reason"],
    "additionalProperties": False,
}

_TOOL_DEFINITION: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Submit a structured interpretation of the user's natural-language itinerary feedback. "
        "Never invent a new experience_id or a new place's provider identity. If a reference is "
        "ambiguous, use status=needs_clarification instead of guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["completed", "needs_clarification", "rejected"],
            },
            "scope": {
                "type": "string",
                "enum": [
                    "single_experience",
                    "single_day",
                    "multiple_days",
                    "whole_itinerary",
                    "preferences_only",
                    "new_place_request",
                ],
            },
            "actions": {"type": "array", "items": _ACTION_SCHEMA},
            "new_place_requests": {"type": "array", "items": _NEW_PLACE_REQUEST_SCHEMA},
            "preserve": _PRESERVE_SCHEMA,
            "clarification": _CLARIFICATION_SCHEMA,
            "summary": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["status", "actions", "new_place_requests", "confidence"],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = (
    "You are interpreting user-requested itinerary changes. You do not redesign the itinerary "
    "yourself -- you only translate the user's feedback into a structured change request for a "
    "later step to act on. You MUST call the submit_ai_feedback_interpretation tool to respond, "
    "and your output must match its schema exactly.\n\n"
    "Reference an existing itinerary item only by its experience_id, exactly as given in "
    "current_items. Never invent a new experience_id, and never resolve a reference by "
    "position/order.\n\n"
    "If the user asks for a place that is not in current_items, return it only as a "
    "new_place_request with the name/description they used as the query -- never invent a "
    "provider id, coordinates, price, rating, opening hours, or availability for it.\n\n"
    "Do not invent new provider facts for an existing item either. If the user states a reason "
    "(e.g. 'it's closed', 'that area is unsafe'), treat that as their own opinion/context for "
    "the action, never as a verified fact.\n\n"
    "Preserve every explicit 'do not change'/'keep' constraint the user states, using the "
    "preserve field -- never leave it only in the summary.\n\n"
    "If a reference is ambiguous (e.g. two current items could both be 'the museum'), return "
    "status=needs_clarification with the possible experience_ids, and leave actions/"
    "new_place_requests empty -- never guess which one the user means.\n\n"
    "Map a pace/preference request only onto pace (relaxed/balanced/packed), or an "
    "adjust_interest action -- never invent a new preference concept.\n\n"
    "Explicit scope wins: if the user names a specific day (e.g. 'day 2'), keep the "
    "interpretation scoped to that day -- use scope=single_day (or multiple_days for several "
    "named days) with a regenerate_day action whose instruction field describes the "
    "day-specific request -- unless the user explicitly says the whole trip/every day/overall "
    "itinerary should change. Do not convert a local request like 'Day 2 is too packed; make "
    "it more relaxed' into a global change_pace or adjust_interest action."
)


def _format_item_line(item: Any) -> str:
    return (
        f"- experience_id={item.experience_id!r} name={item.name!r} "
        f"category={item.category or 'unknown'} day_index={item.day_index}"
    )


def _build_prompt(request: AIFeedbackInterpretationRequest) -> str:
    """Mirrors the Groq adapter's own `_build_prompt` exactly (kept as a
    separate copy, matching this codebase's existing AI candidate-
    proposal adapter convention of not sharing prompt builders across
    providers)."""
    traveler = request.traveler_context
    lines = [
        *[f"Instruction: {instruction}" for instruction in request.interpretation_instructions],
        "",
        f"Destination: {request.destination_name}",
        f"Trip dates: {request.start_date} to {request.end_date} ({request.trip_duration_days} day(s))",
        f"Travelers: {traveler.travelers_count} ({traveler.travel_group_type})",
        f"Pace: {traveler.pace}",
        f"Interests: {', '.join(traveler.interests) if traveler.interests else 'none specified'}",
    ]
    if request.validation_status:
        lines.append(
            f"Validation status: {request.validation_status} "
            f"({request.critical_issue_count} critical issue(s), {request.warning_count} warning(s))"
        )
    if request.active_locks:
        lines.append(
            "Active locks: "
            + ", ".join(f"{lock.locked_item_type}:{lock.locked_item_id}" for lock in request.active_locks)
        )
    lines.append("")
    lines.append(f"Current itinerary items ({len(request.current_items)} total):")
    lines.extend(_format_item_line(item) for item in request.current_items)
    lines.append("")
    lines.append(f"User feedback: {request.feedback_text!r}")
    return "\n".join(lines)


class AnthropicAIFeedbackInterpreterProvider(AIFeedbackInterpreterProvider):
    """`AIFeedbackInterpreterProvider` implementation backed by the
    Anthropic Messages API (Claude), used only when explicitly
    config-gated in via `get_ai_feedback_interpreter_provider` -- never
    the default.
    """

    provider_name = "anthropic_ai_feedback_interpreter_provider"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: Any | None = None,
        max_tokens: int = 2500,
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
            else (settings.ai_feedback_interpreter_model or settings.anthropic_model)
        )
        self._max_tokens = max_tokens
        self._temperature = temperature

    def interpret(self, request: AIFeedbackInterpretationRequest) -> AIFeedbackInterpretationResult:
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
                system=_SYSTEM_PROMPT,
                tools=[_TOOL_DEFINITION],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[{"role": "user", "content": _build_prompt(request)}],
            )
        except Exception as exc:  # API/runtime failure -> rejected, never fabricated
            kind, message = classify_and_message("Anthropic", exc)
            return self._rejected_result(message, failure_kind=kind.value)

        tool_input = self._extract_tool_input(response)
        if tool_input is None:
            return self._rejected_result(
                "Claude did not return a structured tool_use response."
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
        self, request: AIFeedbackInterpretationRequest, tool_input: dict[str, Any]
    ) -> AIFeedbackInterpretationResult:
        raw_status = tool_input.get("status")
        try:
            status = AIFeedbackInterpretationStatus(raw_status)
        except ValueError:
            return self._rejected_result(f"Tool output had an unknown status: {raw_status!r}.")
        if status not in (
            AIFeedbackInterpretationStatus.COMPLETED,
            AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION,
            AIFeedbackInterpretationStatus.REJECTED,
        ):
            return self._rejected_result(f"Tool output returned an unsupported status: {status.value!r}.")

        if status == AIFeedbackInterpretationStatus.REJECTED:
            return self._rejected_result("The model reported it could not interpret this feedback.")

        actions = self._parse_actions(tool_input.get("actions"))
        if actions is None:
            return self._rejected_result("Tool output contained a malformed or unknown action.")

        new_place_requests = self._parse_new_place_requests(tool_input.get("new_place_requests"))
        if new_place_requests is None:
            return self._rejected_result("Tool output contained a malformed new_place_request.")

        preserve = self._parse_preserve(tool_input.get("preserve"))
        if preserve is None:
            return self._rejected_result("Tool output contained a malformed preserve entry.")

        clarification = None
        raw_clarification = tool_input.get("clarification")
        if status == AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION:
            if not isinstance(raw_clarification, dict):
                return self._rejected_result(
                    "Tool output declared needs_clarification without a clarification entry."
                )
            try:
                clarification = AIFeedbackClarification(
                    reason=raw_clarification.get("reason", ""),
                    possible_experience_ids=[
                        i for i in raw_clarification.get("possible_experience_ids", []) if isinstance(i, str)
                    ],
                )
            except ValidationError as exc:
                return self._rejected_result(f"Tool clarification failed validation: {exc}")
            actions = []
            new_place_requests = []

        raw_scope = tool_input.get("scope")
        scope: AIFeedbackScope | None = None
        if isinstance(raw_scope, str) and raw_scope:
            try:
                scope = AIFeedbackScope(raw_scope)
            except ValueError:
                scope = None

        summary = tool_input.get("summary")
        confidence = tool_input.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = 0.0
        confidence = max(0.0, min(1.0, float(confidence)))

        try:
            result = AIFeedbackInterpretationResult(
                status=status,
                scope=scope,
                actions=actions,
                new_place_requests=new_place_requests,
                preserve=preserve,
                clarification=clarification,
                summary=summary if isinstance(summary, str) and summary.strip() else None,
                provider_name=self.provider_name,
                model_name=self._model,
                confidence=confidence,
            )
        except ValidationError as exc:
            return self._rejected_result(f"Assembled interpretation result failed validation: {exc}")

        violations = validate_interpretation_against_request(request, result)
        if violations:
            return self._rejected_result(
                "Claude output referenced an experience_id/day_index outside the allowed scope "
                "or was internally contradictory: " + "; ".join(violations)
            )

        return result

    @staticmethod
    def _parse_actions(raw_actions: Any) -> list[AIFeedbackAction] | None:
        if not isinstance(raw_actions, list):
            return None
        actions: list[AIFeedbackAction] = []
        for raw_action in raw_actions:
            if not isinstance(raw_action, dict):
                return None
            action_type = raw_action.get("type")
            try:
                if action_type == "remove_experience":
                    experience_id = raw_action.get("experience_id")
                    if not experience_id:
                        return None
                    actions.append(RemoveExperienceAction(experience_id=experience_id))
                elif action_type == "move_experience":
                    experience_id = raw_action.get("experience_id")
                    target_day_index = raw_action.get("target_day_index")
                    if not experience_id or target_day_index is None:
                        return None
                    actions.append(
                        MoveExperienceAction(experience_id=experience_id, target_day_index=target_day_index)
                    )
                elif action_type == "change_pace":
                    pace = raw_action.get("pace")
                    if not pace:
                        return None
                    actions.append(ChangePaceAction(pace=pace))
                elif action_type == "adjust_interest":
                    category = raw_action.get("category")
                    direction = raw_action.get("direction")
                    if not category or direction not in ("more", "less"):
                        return None
                    actions.append(AdjustInterestAction(category=category, direction=direction))
                elif action_type == "regenerate_day":
                    day_index = raw_action.get("day_index")
                    instruction = raw_action.get("instruction")
                    if day_index is None or not instruction:
                        return None
                    actions.append(RegenerateDayAction(day_index=day_index, instruction=instruction))
                elif action_type == "general_instruction":
                    note = raw_action.get("note")
                    if not note:
                        return None
                    actions.append(GeneralInstructionAction(note=note))
                else:
                    return None
            except ValidationError:
                return None
        return actions

    @staticmethod
    def _parse_new_place_requests(raw_requests: Any) -> list[NewPlaceRequest] | None:
        if not isinstance(raw_requests, list):
            return None
        requests: list[NewPlaceRequest] = []
        for raw_request in raw_requests:
            if not isinstance(raw_request, dict):
                return None
            query = raw_request.get("query")
            if not query:
                return None
            try:
                requests.append(NewPlaceRequest(query=query, note=raw_request.get("note")))
            except ValidationError:
                return None
        return requests

    @staticmethod
    def _parse_preserve(raw_preserve: Any) -> AIFeedbackPreserveScope | None:
        if raw_preserve is None:
            return AIFeedbackPreserveScope()
        if not isinstance(raw_preserve, dict):
            return None
        try:
            return AIFeedbackPreserveScope(
                day_indices=[d for d in raw_preserve.get("day_indices", []) if isinstance(d, int)],
                experience_ids=[
                    i for i in raw_preserve.get("experience_ids", []) if isinstance(i, str)
                ],
            )
        except ValidationError:
            return None

    def _not_connected_result(self, reason: str) -> AIFeedbackInterpretationResult:
        return AIFeedbackInterpretationResult(
            status=AIFeedbackInterpretationStatus.NOT_CONNECTED,
            blocked_reasons=[reason],
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
        )

    def _rejected_result(
        self, reason: str, failure_kind: str | None = None
    ) -> AIFeedbackInterpretationResult:
        return AIFeedbackInterpretationResult(
            status=AIFeedbackInterpretationStatus.REJECTED,
            failure_kind=failure_kind,
            blocked_reasons=[reason],
            provider_name=self.provider_name,
            model_name=self._model,
            confidence=0.0,
        )
