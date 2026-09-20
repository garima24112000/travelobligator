from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.models.ai_feedback_interpretation import (
    AIFeedbackInterpretationRequest,
    AIFeedbackInterpretationResult,
    AIFeedbackInterpretationStatus,
)
from app.models.planning_state import PlanningState
from app.providers.ai_feedback_interpreter import (
    AIFeedbackInterpreterProvider,
    get_ai_feedback_interpreter_provider,
)
from app.services.ai_feedback_interpretation_request_builder import (
    AIFeedbackInterpretationRequestBuilder,
    ai_feedback_interpretation_request_builder,
)

logger = logging.getLogger(__name__)

# Section 196 (docs/14_backend_architecture.md, following section 146):
# the service layer wiring `AIFeedbackInterpretationRequestBuilder` to a
# real `AIFeedbackInterpreterProvider`. Mirrors
# `AIItineraryReasoningService`/`AIItineraryRepairService`/
# `ItineraryNarrativeService` exactly: `Settings.ai_feedback_interpreter_enabled`
# (default `False`) gates whether this ever calls a provider at all --
# when disabled, `interpret` returns/stores an honest `not_connected`
# result without resolving the factory or making any network call.
#
# **Not called by anything in the runtime pipeline yet.** No LangGraph
# node, `PlanningOrchestrator` stage, feedback/regeneration route, or
# `FeedbackService` method constructs or calls this service -- that
# wiring is Section 197's job. `interpret` is pure/read-only: it never
# mutates `planning_state`, never calls provider discovery for a new
# place, never regenerates anything, and never creates a version. There
# is deliberately no mutating `apply` method on this service at all
# (unlike `AIItineraryReasoningService`/`AIItineraryRepairService`,
# which each own exactly one `PlanningState` field to write) -- Section
# 196's own explicit scope is "no PlanningState mutation," so this
# service has nothing of its own to write. `FeedbackEvent.interpretation`
# (`app.models.planning_state`) already exists as the natural future
# home for a stored interpretation once Section 197 decides how/when to
# persist one -- this step does not write to it.

_STAGE = "ai_feedback_interpreter"
_DISABLED_MESSAGE = "AI feedback interpreter is disabled (AI_FEEDBACK_INTERPRETER_ENABLED=false)."

_STATUS_TO_ERROR_CODE = {
    "not_connected": "PROVIDER_NOT_CONNECTED",
    "rejected": "PROVIDER_REJECTED",
}


def _disabled_result() -> AIFeedbackInterpretationResult:
    return AIFeedbackInterpretationResult(
        status=AIFeedbackInterpretationStatus.NOT_CONNECTED,
        blocked_reasons=[_DISABLED_MESSAGE],
        confidence=0.0,
    )


def _log_fields(
    *,
    provider_name: str,
    status: str,
    result: AIFeedbackInterpretationResult,
    duration_ms: float,
    feedback_length: int,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "provider": provider_name,
        "stage": _STAGE,
        "status": status,
        "duration_ms": round(duration_ms, 3),
        # Task 42: a length only -- never the raw feedback text itself
        # (this repo's existing logging conventions log counts/statuses
        # about AI-facing text, never the text itself -- see
        # `ItineraryNarrativeService`'s own "never the built request"
        # rule).
        "feedback_length": feedback_length,
        "action_count": len(result.actions),
        "new_place_request_count": len(result.new_place_requests),
        "clarification_required": result.status == AIFeedbackInterpretationStatus.NEEDS_CLARIFICATION,
        "referenced_experience_count": len(
            {
                experience_id
                for action in result.actions
                for experience_id in (
                    [action.experience_id] if hasattr(action, "experience_id") else []
                )
            }
        ),
    }
    error_code = _STATUS_TO_ERROR_CODE.get(status)
    if error_code is not None:
        fields["error_code"] = error_code
    return fields


class AIFeedbackInterpreterService:
    """Composes `AIFeedbackInterpretationRequestBuilder` -> a configured
    `AIFeedbackInterpreterProvider` -> the validated structured
    interpretation. `interpret` is pure (never mutates `planning_state`,
    never calls provider discovery, never regenerates, never creates a
    version) -- the only method this service has.
    """

    def __init__(
        self,
        request_builder: AIFeedbackInterpretationRequestBuilder | None = None,
        provider: AIFeedbackInterpreterProvider | None = None,
    ) -> None:
        self.request_builder = request_builder or ai_feedback_interpretation_request_builder
        # Explicit injection (tests only) -- in production this stays
        # `None` and `_resolve_provider` reads the factory fresh per
        # call, matching every other AI service in this repo.
        self._provider_override = provider

    def _resolve_provider(self) -> AIFeedbackInterpreterProvider:
        return self._provider_override or get_ai_feedback_interpreter_provider()

    def interpret(self, planning_state: PlanningState, feedback_text: str) -> AIFeedbackInterpretationResult:
        """Pure: builds a request from `planning_state`/`feedback_text`
        and calls the configured provider. Never mutates `planning_state`,
        never persists a `FeedbackEvent`, never calls provider discovery,
        never regenerates, never creates a version. Returns an honest
        `not_connected` result, without building a request or resolving a
        provider at all, whenever `Settings.ai_feedback_interpreter_enabled`
        is `False`.
        """
        if not get_settings().ai_feedback_interpreter_enabled:
            return _disabled_result()

        provider = self._resolve_provider()
        provider_name = getattr(provider, "provider_name", "ai_feedback_interpreter_provider")

        started_at = time.monotonic()
        request = self.request_builder.build_request(planning_state, feedback_text)
        result = provider.interpret(request)
        duration_ms = (time.monotonic() - started_at) * 1000

        status = result.status.value
        log_kwargs = dict(
            provider_name=provider_name,
            status=status,
            result=result,
            duration_ms=duration_ms,
            feedback_length=len(feedback_text),
        )
        if status == "completed" or status == "needs_clarification":
            logger.info(
                "AIFeedbackInterpreterService.interpret completed.",
                extra=_log_fields(**log_kwargs),
            )
        else:
            logger.warning(
                "AIFeedbackInterpreterService.interpret did not succeed.",
                extra=_log_fields(**log_kwargs),
            )
        return result


ai_feedback_interpreter_service = AIFeedbackInterpreterService()
