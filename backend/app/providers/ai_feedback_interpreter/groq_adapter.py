from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

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

# Groq-backed AI feedback-interpreter adapter (Section 196,
# docs/14_backend_architecture.md, following section 146). Reuses the
# exact post-191A.1 Structured Outputs pattern
# `app.providers.ai_itinerary_reasoning.groq_adapter.GroqAIItineraryReasoningProvider`
# already established -- `with_structured_output(..., method="json_schema",
# strict=True)`, never `method="function_calling"`, never `tools`/
# `tool_choice`.
#
# Section 195's own lesson (see that adapter's module comment): Groq's
# strict mode requires every property in the schema's `required` array,
# so every field below has no Python default at all -- always required,
# with the prompt instructed to use null/empty list when a field doesn't
# apply. The wire schema is deliberately FLAT for `_GroqActionSchema`
# (one shape with every possible action field, tagged by `type`) rather
# than a Pydantic discriminated union -- mirrors this repo's own
# established "no unsupported constraint in the wire schema, domain-level
# re-validation after parsing" convention (Task 18), and avoids
# depending on Groq's strict-mode discriminated-union/oneOf support,
# which is untested territory in this codebase.
#
# `langchain_groq` is imported lazily inside `_build_client`, matching
# every other Groq adapter in this repo.


class _GroqActionSchema(BaseModel):
    type: str = Field(
        description="One of: remove_experience, move_experience, change_pace, adjust_interest, "
        "regenerate_day, general_instruction."
    )
    experience_id: str | None = Field(
        description="Required for remove_experience/move_experience -- must be an experience_id "
        "from current_items. Null for every other type."
    )
    target_day_index: int | None = Field(
        description="Required for move_experience -- the day_index to move the item to. Null "
        "for every other type."
    )
    pace: str | None = Field(
        description="Required for change_pace -- one of relaxed, balanced, packed. Null for "
        "every other type."
    )
    category: str | None = Field(
        description="Required for adjust_interest -- a short category/interest name. Null for "
        "every other type."
    )
    direction: str | None = Field(
        description="Required for adjust_interest -- 'more' or 'less'. Null for every other type."
    )
    day_index: int | None = Field(
        description="Required for regenerate_day. Null for every other type."
    )
    instruction: str | None = Field(
        description="Required for regenerate_day -- a short, safe description of the "
        "day-specific change the user wants (e.g. 'make it more relaxed', 'add more food'). "
        "Null for every other type."
    )
    note: str | None = Field(
        description="Required for general_instruction -- a short, safe note. Null for every "
        "other type."
    )


class _GroqNewPlaceRequestSchema(BaseModel):
    query: str = Field(description="The place name/description the user asked for.")
    note: str | None = Field(description="Optional short context. Null if there is none.")


class _GroqPreserveSchema(BaseModel):
    day_indices: list[int] = Field(
        description="Day indices the user explicitly wants left unchanged. Empty list if none."
    )
    experience_ids: list[str] = Field(
        description="Existing experience_ids the user explicitly wants left unchanged. Empty "
        "list if none."
    )


class _GroqClarificationSchema(BaseModel):
    reason: str = Field(description="Why the reference is ambiguous.")
    possible_experience_ids: list[str] = Field(
        description="The current_items experience_ids that could match. Empty list if unsure."
    )


class _GroqInterpretationSchema(BaseModel):
    status: str = Field(description="One of: completed, needs_clarification, rejected.")
    scope: str | None = Field(
        description="One of: single_experience, single_day, multiple_days, whole_itinerary, "
        "preferences_only, new_place_request. Null unless status is completed."
    )
    actions: list[_GroqActionSchema] = Field(
        description="Always include this key. Use an empty list if there are none."
    )
    new_place_requests: list[_GroqNewPlaceRequestSchema] = Field(
        description="Always include this key. Use an empty list if there are none."
    )
    preserve: _GroqPreserveSchema
    clarification: _GroqClarificationSchema | None = Field(
        description="Required when status is needs_clarification. Null otherwise."
    )
    summary: str | None = Field(
        description="A short, plain restatement of what will change. Null unless status is "
        "completed."
    )
    confidence: float = Field(description="Your confidence in this interpretation, from 0.0 to 1.0.")


_SYSTEM_PROMPT = (
    "You are interpreting user-requested itinerary changes. You do not redesign the itinerary "
    "yourself -- you only translate the user's feedback into a structured change request for a "
    "later step to act on.\n\n"
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
    "it more relaxed' into a global change_pace or adjust_interest action.\n\n"
    "Return only the structured interpretation matching the required schema exactly -- every "
    "schema key is required, so use null or an empty list instead of omitting a key or "
    "guessing a value."
)


def _format_item_line(item: Any) -> str:
    return (
        f"- experience_id={item.experience_id!r} name={item.name!r} "
        f"category={item.category or 'unknown'} day_index={item.day_index}"
    )


def _build_prompt(request: AIFeedbackInterpretationRequest) -> str:
    """Builds a minimal, controlled prompt from `request` fields only --
    never a raw `PlanningState` dump, and never a coordinate."""
    traveler = request.traveler_context
    lines = [
        _SYSTEM_PROMPT,
        "",
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


class GroqAIFeedbackInterpreterProvider(AIFeedbackInterpreterProvider):
    """`AIFeedbackInterpreterProvider` implementation backed by Groq (via
    `langchain_groq.ChatGroq`), used only when explicitly config-gated in
    via `get_ai_feedback_interpreter_provider` -- never the default.

    `interpret` never invents an experience_id, a new place's provider
    identity, or resolves an ambiguous reference: every candidate
    experience_id/day_index in a `completed` result is checked against
    `request.current_items`/`request.trip_duration_days` via
    `validate_interpretation_against_request` before `completed` is ever
    reported. If validation fails, the call raises, or no usable
    structured output comes back, this returns an honest `rejected`/
    `not_connected` result -- never a fabricated one.
    """

    provider_name = "groq_ai_feedback_interpreter_provider"

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
            self._api_key = settings.groq_api_key
        else:
            self._api_key = api_key
        self._model = (
            model
            if model is not None
            else (settings.ai_feedback_interpreter_model or settings.groq_model)
        )
        self._max_tokens = max_tokens
        self._temperature = temperature

    def interpret(self, request: AIFeedbackInterpretationRequest) -> AIFeedbackInterpretationResult:
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
                return self._not_connected_result(
                    f"Groq client could not be initialized: {exc}"
                )

        try:
            raw_output = client.invoke(_build_prompt(request))
        except Exception as exc:  # API/runtime failure -> rejected, never fabricated
            kind, message = classify_and_message("Groq", exc)
            return self._rejected_result(message, failure_kind=kind.value)

        output_dict = self._coerce_output(raw_output)
        if output_dict is None:
            return self._rejected_result("Groq did not return a structured response.")

        return self._build_result_from_output(request, output_dict)

    def _build_client(self) -> Any:
        try:
            from langchain_groq import ChatGroq
        except ImportError as exc:
            raise RuntimeError("The 'langchain_groq' package is not installed.") from exc

        chat = ChatGroq(
            model=self._model,
            api_key=self._api_key,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        return chat.with_structured_output(
            _GroqInterpretationSchema, method="json_schema", strict=True
        )

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

    def _build_result_from_output(
        self, request: AIFeedbackInterpretationRequest, output: dict[str, Any]
    ) -> AIFeedbackInterpretationResult:
        raw_status = output.get("status")
        try:
            status = AIFeedbackInterpretationStatus(raw_status)
        except ValueError:
            return self._rejected_result(f"Groq output had an unknown status: {raw_status!r}.")
        if status not in (
            AIFeedbackInterpretationStatus.COMPLETED,
            AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION,
            AIFeedbackInterpretationStatus.REJECTED,
        ):
            return self._rejected_result(f"Groq output returned an unsupported status: {status.value!r}.")

        if status == AIFeedbackInterpretationStatus.REJECTED:
            return self._rejected_result("The model reported it could not interpret this feedback.")

        actions = self._parse_actions(output.get("actions"))
        if actions is None:
            return self._rejected_result("Groq output contained a malformed or unknown action.")

        new_place_requests = self._parse_new_place_requests(output.get("new_place_requests"))
        if new_place_requests is None:
            return self._rejected_result("Groq output contained a malformed new_place_request.")

        preserve = self._parse_preserve(output.get("preserve"))
        if preserve is None:
            return self._rejected_result("Groq output contained a malformed preserve entry.")

        clarification = None
        raw_clarification = output.get("clarification")
        if status == AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION:
            if not isinstance(raw_clarification, dict):
                return self._rejected_result(
                    "Groq output declared needs_clarification without a clarification entry."
                )
            try:
                clarification = AIFeedbackClarification(
                    reason=raw_clarification.get("reason", ""),
                    possible_experience_ids=[
                        i for i in raw_clarification.get("possible_experience_ids", []) if isinstance(i, str)
                    ],
                )
            except ValidationError as exc:
                return self._rejected_result(f"Groq clarification failed validation: {exc}")
            actions = []
            new_place_requests = []

        raw_scope = output.get("scope")
        scope: AIFeedbackScope | None = None
        if isinstance(raw_scope, str) and raw_scope:
            try:
                scope = AIFeedbackScope(raw_scope)
            except ValueError:
                scope = None

        summary = output.get("summary")
        confidence = output.get("confidence", 0.0)
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
                "Groq output referenced an experience_id/day_index outside the allowed scope "
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
