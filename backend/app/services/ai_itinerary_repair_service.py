from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.models.ai_itinerary_reasoning import AIItineraryReasoningGuardrailReport
from app.models.ai_itinerary_repair import AIItineraryRepairRequest, AIItineraryRepairResult, AIItineraryRepairStatus
from app.models.planning_state import PlanningState
from app.providers.ai_itinerary_reasoning import AIItineraryReasoningProvider, get_ai_itinerary_reasoning_provider
from app.services.ai_itinerary_repair_request_builder import AIItineraryRepairRequestBuilder

logger = logging.getLogger(__name__)

# Section 194A (docs/14_backend_architecture.md, following section 143):
# the service layer wiring `AIItineraryRepairRequestBuilder` to the same
# configured `AIItineraryReasoningProvider.repair` used for `reason` --
# repair is the same LLM #2 job/provider, not a second subsystem.
#
# Reuses `Settings.ai_itinerary_reasoning_enabled`/
# `ai_itinerary_reasoning_provider`/`ai_itinerary_reasoning_model`
# exactly as `AIItineraryReasoningService` does -- no new
# `AI_ITINERARY_REPAIR_ENABLED` setting is introduced in this step (Task
# 12): repairing a reasoning result makes no sense when reasoning itself
# is disabled, and Section 194B is what will need its own, separate
# bounded-loop-control setting (attempt limits, automatic wiring), not
# this one.
#
# **Not called by anything in the runtime pipeline yet.** No LangGraph
# node, `PlanningOrchestrator` stage, or API route constructs or calls
# this service, and nothing here reruns routing, reruns validation, or
# mutates `experience_plan` -- that wiring is Section 194B's job. `apply`
# may populate `planning_state.ai_itinerary_repair_result`, but touches
# no other field.

_STAGE = "ai_itinerary_repair"
_DISABLED_MESSAGE = "AI itinerary reasoning is disabled (AI_ITINERARY_REASONING_ENABLED=false)."
_NO_REPAIR_NEEDED_MESSAGE = (
    "No repairable validation issue was found; the repair provider was not called."
)

_STATUS_TO_ERROR_CODE = {
    "not_connected": "PROVIDER_NOT_CONNECTED",
    "rejected": "PROVIDER_REJECTED",
    "failed": "PROVIDER_FAILED",
}


def _disabled_result(attempt_number: int) -> AIItineraryRepairResult:
    return AIItineraryRepairResult(
        status=AIItineraryRepairStatus.NOT_CONNECTED,
        repaired_days=[],
        guardrail_report=AIItineraryReasoningGuardrailReport(
            passed=False,
            blocked_reasons=[_DISABLED_MESSAGE],
            checked_fields=["ai_itinerary_reasoning_enabled"],
        ),
        confidence=0.0,
        attempt_number=attempt_number,
    )


def _no_repair_needed_result(attempt_number: int) -> AIItineraryRepairResult:
    return AIItineraryRepairResult(
        status=AIItineraryRepairStatus.SKIPPED,
        repaired_days=[],
        repair_summary=_NO_REPAIR_NEEDED_MESSAGE,
        guardrail_report=AIItineraryReasoningGuardrailReport(
            passed=True,
            checked_fields=["repairable_issues"],
        ),
        confidence=0.0,
        attempt_number=attempt_number,
    )


def _log_fields(
    *,
    provider_name: str,
    status: str,
    request: AIItineraryRepairRequest | None,
    duration_ms: float,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "provider": provider_name,
        "stage": _STAGE,
        "status": status,
        "duration_ms": round(duration_ms, 3),
        "attempt_number": request.attempt_number if request is not None else None,
        "repairable_issue_count": len(request.issues) if request is not None else 0,
        "affected_day_count": len(request.affected_days) if request is not None else 0,
    }
    error_code = _STATUS_TO_ERROR_CODE.get(status)
    if error_code is not None:
        fields["error_code"] = error_code
    return fields


class AIItineraryRepairService:
    """Composes `AIItineraryRepairRequestBuilder` (Task 13) -> the same
    configured `AIItineraryReasoningProvider.repair` `AIItineraryReasoningService`
    resolves for `reason` -> the validated `AIItineraryRepairResult`.
    `repair` is pure/read-only (never mutates `planning_state`); `apply`
    is the only method that writes, and it writes exactly one field
    (`ai_itinerary_repair_result`).
    """

    def __init__(
        self,
        request_builder: AIItineraryRepairRequestBuilder | None = None,
        provider: AIItineraryReasoningProvider | None = None,
    ) -> None:
        self.request_builder = request_builder or AIItineraryRepairRequestBuilder()
        # Explicit injection (tests only) -- in production this stays
        # `None` and `_resolve_provider` reads the factory fresh on every
        # call, matching `AIItineraryReasoningService` exactly.
        self._provider_override = provider

    def _resolve_provider(self) -> AIItineraryReasoningProvider:
        return self._provider_override or get_ai_itinerary_reasoning_provider()

    def repair(self, planning_state: PlanningState, attempt_number: int = 1) -> AIItineraryRepairResult:
        """Pure: builds a repair request from `planning_state` and calls
        the configured provider's `repair`. Never mutates
        `planning_state`, never reruns routing/validation, never touches
        `experience_plan`.

        Returns an honest `not_connected` result, without building a
        request or resolving a provider at all, whenever
        `Settings.ai_itinerary_reasoning_enabled` is `False`. Returns an
        honest `skipped` result, without resolving a provider or calling
        it, whenever `AIItineraryRepairRequestBuilder.build_request`
        finds nothing repairable (Task 13/26: "if no repairable issues
        exist: do not call the LLM").
        """
        if not get_settings().ai_itinerary_reasoning_enabled:
            return _disabled_result(attempt_number)

        request = self.request_builder.build_request(planning_state, attempt_number=attempt_number)
        if request is None:
            return _no_repair_needed_result(attempt_number)

        provider = self._resolve_provider()
        provider_name = getattr(provider, "provider_name", "ai_itinerary_reasoning_provider")

        started_at = time.monotonic()
        result = provider.repair(request)
        duration_ms = (time.monotonic() - started_at) * 1000

        status = result.status.value
        if status == "completed":
            logger.info(
                "AIItineraryRepairService.repair completed.",
                extra=_log_fields(provider_name=provider_name, status=status, request=request, duration_ms=duration_ms),
            )
        else:
            logger.warning(
                "AIItineraryRepairService.repair did not succeed.",
                extra=_log_fields(provider_name=provider_name, status=status, request=request, duration_ms=duration_ms),
            )
        return result

    def apply(self, planning_state: PlanningState, attempt_number: int = 1) -> PlanningState:
        """The only mutating entry point: computes a fresh repair result
        and stores it on `planning_state.ai_itinerary_repair_result`,
        replacing whatever was there before. Never touches
        `ai_itinerary_reasoning_result`/`experience_plan`/
        `route_feasibility_report`/`route_aware_sequencing_report`/
        `travel_time_buffer_report`/`validation_report`. Does not persist
        and does not merge -- a future caller (Section 194B) owns both
        `merge_repair_into_reasoning_result` and
        `planning_state_repository.save`.
        """
        planning_state.ai_itinerary_repair_result = self.repair(planning_state, attempt_number=attempt_number)
        return planning_state


def apply_repair_safely(
    planning_state: PlanningState,
    service: AIItineraryRepairService,
    attempt_number: int = 1,
) -> PlanningState:
    """Fail-safe wrapper, mirroring
    `app.services.ai_itinerary_reasoning_service.apply_itinerary_reasoning_safely`:
    catches any unexpected exception from `service.apply` so a repair
    failure never crashes a caller, logging safely (no prompt, no raw
    exception text, no API key) and leaving `planning_state` exactly as
    it was for that call -- never a fabricated repair result.

    Not called by anything in this step -- provided so Section 194B has
    one existing, already-tested implementation to call rather than
    writing its own.
    """
    try:
        return service.apply(planning_state, attempt_number=attempt_number)
    except Exception:
        logger.warning(
            "AIItineraryRepairService.apply failed unexpectedly; leaving the plan "
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
