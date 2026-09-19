from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.models.ai_itinerary_reasoning import (
    AIItineraryReasoningGuardrailReport,
    AIItineraryReasoningResult,
    AIItineraryReasoningStatus,
)
from app.models.planning_state import PlanningState
from app.providers.ai_itinerary_reasoning import (
    AIItineraryReasoningProvider,
    get_ai_itinerary_reasoning_provider,
)
from app.services.ai_itinerary_reasoning_request_builder import AIItineraryReasoningRequestBuilder

logger = logging.getLogger(__name__)

# Section 193B (docs/14_backend_architecture.md section 142): the service
# layer wiring `AIItineraryReasoningRequestBuilder` (Section 193A) to a
# real `AIItineraryReasoningProvider` (this step). Mirrors
# `ItineraryNarrativeService` exactly: `Settings.ai_itinerary_reasoning_enabled`
# (default `False`) gates whether this ever calls a provider at all --
# when disabled, `reason`/`apply` return/store an honest `not_connected`
# result without resolving the factory or making any network call.
#
# Section 193C (docs/14_backend_architecture.md section 143) wires this
# service into the LangGraph `ai_itinerary_reasoning` node, positioned
# immediately before `experience_planning`. `apply` only ever populates
# `planning_state.ai_itinerary_reasoning_result` -- it never touches
# `experience_plan`, `route_feasibility_report`,
# `route_aware_sequencing_report`, `travel_time_buffer_report`, or
# `validation_report` itself. `ExperiencePlannerService` is the one
# consumer that reads the resulting `ai_itinerary_reasoning_result` back
# off `planning_state` to decide selection/grouping/order, always falling
# back to its pre-193C deterministic path whenever the result isn't a
# safely resolvable `completed` one.

_STAGE = "ai_itinerary_reasoning"
_DISABLED_MESSAGE = "AI itinerary reasoning is disabled (AI_ITINERARY_REASONING_ENABLED=false)."
_UNEXPECTED_FAILURE_MESSAGE = (
    "AI itinerary reasoning failed unexpectedly; no reasoning result was produced."
)

_STATUS_TO_ERROR_CODE = {
    "not_connected": "PROVIDER_NOT_CONNECTED",
    "rejected": "PROVIDER_REJECTED",
    "failed": "PROVIDER_FAILED",
}


def _disabled_result() -> AIItineraryReasoningResult:
    return AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.NOT_CONNECTED,
        days=[],
        guardrail_report=AIItineraryReasoningGuardrailReport(
            passed=False,
            blocked_reasons=[_DISABLED_MESSAGE],
            checked_fields=["ai_itinerary_reasoning_enabled"],
        ),
        confidence=0.0,
    )


def _unexpected_failure_result(provider_name: str | None) -> AIItineraryReasoningResult:
    return AIItineraryReasoningResult(
        status=AIItineraryReasoningStatus.REJECTED,
        days=[],
        guardrail_report=AIItineraryReasoningGuardrailReport(
            passed=False,
            blocked_reasons=[_UNEXPECTED_FAILURE_MESSAGE],
            checked_fields=["provider_call"],
        ),
        provider_name=provider_name,
        confidence=0.0,
    )


def _log_fields(*, provider_name: str, status: str, result: AIItineraryReasoningResult | None, duration_ms: float) -> dict[str, object]:
    fields: dict[str, object] = {
        "provider": provider_name,
        "stage": _STAGE,
        "status": status,
        "duration_ms": round(duration_ms, 3),
        "day_count": len(result.days) if result is not None else 0,
    }
    error_code = _STATUS_TO_ERROR_CODE.get(status)
    if error_code is not None:
        fields["error_code"] = error_code
    return fields


class AIItineraryReasoningService:
    """Composes `AIItineraryReasoningRequestBuilder` -> a configured
    `AIItineraryReasoningProvider` -> the Section 193A candidate-ID-safe
    result. `reason` is pure/read-only (never mutates `planning_state`);
    `apply` is the only method that writes, and it writes exactly one
    field (`ai_itinerary_reasoning_result`).
    """

    def __init__(
        self,
        request_builder: AIItineraryReasoningRequestBuilder | None = None,
        provider: AIItineraryReasoningProvider | None = None,
    ) -> None:
        self.request_builder = request_builder or AIItineraryReasoningRequestBuilder()
        # Explicit injection (tests only) -- in production this stays
        # `None` and `_resolve_provider` reads the factory fresh on every
        # call, matching `ItineraryNarrativeService`'s own convention, so
        # a config change takes effect on the next call without
        # restarting the process.
        self._provider_override = provider

    def _resolve_provider(self) -> AIItineraryReasoningProvider:
        return self._provider_override or get_ai_itinerary_reasoning_provider()

    def reason(self, planning_state: PlanningState) -> AIItineraryReasoningResult:
        """Pure: builds a request from `planning_state` and calls the
        configured provider. Never mutates `planning_state`. Returns an
        honest `not_connected` result, without calling a provider at all,
        whenever `Settings.ai_itinerary_reasoning_enabled` is `False`.
        """
        if not get_settings().ai_itinerary_reasoning_enabled:
            return _disabled_result()

        provider = self._resolve_provider()
        provider_name = getattr(provider, "provider_name", "ai_itinerary_reasoning_provider")

        started_at = time.monotonic()
        request = self.request_builder.build_request(planning_state)
        result = provider.reason(request)
        duration_ms = (time.monotonic() - started_at) * 1000

        status = result.status.value
        if status == "completed":
            logger.info(
                "AIItineraryReasoningService.reason completed.",
                extra=_log_fields(provider_name=provider_name, status=status, result=result, duration_ms=duration_ms),
            )
        else:
            logger.warning(
                "AIItineraryReasoningService.reason did not succeed.",
                extra=_log_fields(provider_name=provider_name, status=status, result=result, duration_ms=duration_ms),
            )
        return result

    def apply(self, planning_state: PlanningState) -> PlanningState:
        """The only mutating entry point: computes a fresh reasoning
        result and stores it on `planning_state.ai_itinerary_reasoning_result`,
        replacing whatever was there before. Never touches
        `experience_plan`/`route_feasibility_report`/
        `route_aware_sequencing_report`/`travel_time_buffer_report`/
        `validation_report`. Does not persist -- a future caller (Section
        193C) still owns `planning_state_repository.save`.
        """
        planning_state.ai_itinerary_reasoning_result = self.reason(planning_state)
        return planning_state


def apply_itinerary_reasoning_safely(
    planning_state: PlanningState,
    service: AIItineraryReasoningService,
) -> PlanningState:
    """Fail-safe wrapper, mirroring
    `app.services.ai_candidate_discovery_service.apply_discovery_to_state`:
    catches any unexpected exception from `service.apply` so a reasoning
    failure never crashes a caller, logging safely (no prompt, no raw
    exception text, no API key) and leaving `planning_state` exactly as it
    was for that call -- never a fabricated reasoning result.

    Called by `build_ai_itinerary_reasoning_node` (Section 193C,
    `app.graphs.planning_graph_nodes`), the LangGraph node positioned
    immediately before `experience_planning`.
    """
    try:
        return service.apply(planning_state)
    except Exception:
        # The provider that would have been used is resolved fresh inside
        # service.apply/reason (never eagerly), and the failure could have
        # happened before that resolution even completed -- so this logs
        # the generic provider-boundary name only, never a guess at which
        # concrete adapter was involved.
        logger.warning(
            "AIItineraryReasoningService.apply failed unexpectedly; leaving the plan "
            "otherwise unaffected.",
            exc_info=True,
            extra={
                "provider": "ai_itinerary_reasoning_provider",
                "stage": _STAGE,
                "status": "failed",
                "error_code": "PROVIDER_FAILED",
            },
        )
        return planning_state
