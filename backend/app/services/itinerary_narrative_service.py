from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.models.itinerary_narrative import (
    ItineraryNarrativeReport,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
)
from app.models.planning_state import PlanningState
from app.providers.itinerary_narrator import ItineraryNarratorProvider, get_itinerary_narrator_provider
from app.services.itinerary_narrative_grounding import (
    SAFE_AI_UNAVAILABLE_MESSAGE,
    build_deterministic_narrative,
    find_ungrounded_terms,
    grounding_rejected_report,
    normalize_day_titles,
    safe_narrator_message,
)
from app.services.itinerary_narrative_request_builder import (
    ItineraryNarrativeRequestBuilder,
    itinerary_narrative_request_builder,
)

logger = logging.getLogger(__name__)

# Deterministic provider infrastructure wrapper, not AI reasoning itself
# (Step 182F, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). `generate` is the *only* place
# `PlanningState.itinerary_narrative_report` is ever set -- called by
# `PlanningOrchestrator` after validation/provider coverage/the full plan
# already exist (generation), and again after `rerun_affected_stages`
# (regeneration, via `app.api.routes.trips`).
#
# `generate` never mutates any other `PlanningState` field: it only ever
# reads `planning_state` (via the injected request builder) and calls the
# injected/factory-resolved narrator provider, then assigns the returned
# `ItineraryNarrativeReport` onto `planning_state.itinerary_narrative_report`.
# It never touches `experience_plan`, `validation_report`,
# `provider_coverage`, `regeneration_readiness`, or any other field --
# so a caller can always call `generate` and trust nothing else changed.
#
# Generation/regeneration must succeed whether or not the narrator is
# enabled or fails: `generate` never raises. When disabled (the
# default), it never calls the provider factory or the request builder
# at all -- a pure, instant `not_connected` result, with zero risk of
# accidentally reaching a real network call. When enabled but the
# request builder or provider raises unexpectedly for any reason, that
# exception is caught here and converted into an honest `failed` result
# with a generic, safe message -- never the raw exception text, and
# never a fabricated narrative.

_DISABLED_MESSAGE = "Itinerary narrator is disabled (ITINERARY_NARRATOR_ENABLED=false)."
_UNEXPECTED_FAILURE_MESSAGE = "The itinerary narrator failed unexpectedly."

# Section 195.1 (docs/14_backend_architecture.md, following section 146):
# a deterministic, non-LLM safety net -- Section 195's own real
# verification showed a real narrator can honestly avoid claiming a plan
# is resolved/verified without ever actually surfacing the limitation
# either, which technically satisfies "no false claim" but not "never
# hidden." This exact wording carries no overclaim language of its own
# and never describes what kind of issue remains (Task 2: "do not invent
# the nature of an issue").
_UNRESOLVED_VALIDATION_LIMITATION_WARNING = (
    "Some itinerary checks still need review; see the validation details before relying on the plan."
)


def _apply_final_validation_disclosure(
    report: ItineraryNarrativeReport, request: ItineraryNarrativeRequest
) -> ItineraryNarrativeReport:
    """Deterministic post-processing over an already-`success` narrator
    result -- never applied to a `failed`/`not_connected`/`unavailable`
    result (Task 5: those are unchanged). Driven entirely by `request`'s
    own `warning_count`/`critical_issue_count`, which the request builder
    already computed from `planning_state.validation_report` at request-
    build time -- the same FINAL, current, post-repair validation state
    the narrator itself saw, never a stale pre-repair snapshot (Task 3).

    - Zero unresolved issues (a clean final validation, including the
      case where a repair genuinely resolved what used to be a problem):
      returns `report` completely unchanged -- never adds a warning about
      a resolved issue.
    - One or more unresolved issues, but the narrator already returned at
      least one warning of its own: returns `report` unchanged too (Task
      4's deliberately simple structural rule -- trust an existing
      warning rather than risk a redundant duplicate; no fuzzy semantic
      matching).
    - One or more unresolved issues and `report.warnings` is empty:
      returns a new report (never mutates `report` in place) with
      `_UNRESOLVED_VALIDATION_LIMITATION_WARNING` appended.
    """
    if report.status != ItineraryNarrativeStatus.SUCCESS:
        return report
    unresolved_issue_count = request.warning_count + request.critical_issue_count
    if unresolved_issue_count == 0:
        return report
    if report.warnings:
        return report
    return report.model_copy(update={"warnings": [_UNRESOLVED_VALIDATION_LIMITATION_WARNING]})

# Step 187F (docs/14_backend_architecture.md section 125): this LLM-backed
# subsystem calls its provider directly (via `get_itinerary_narrator_
# provider()`), never through `ProviderGateway` -- so, per that step's
# own scope, one safe structured summary log line is added here instead,
# at the exact service boundary that already calls the provider and
# already catches its exceptions (the `except Exception` block below
# existed before this step; no new exception boundary is added).
# Deliberately logs only `provider`/`stage="itinerary_narrator"`/
# `status`/`error_code`/`duration_ms` -- never the built `request`
# (which embeds real scheduled-experience/restaurant names from the
# plan), never `report.message`, and never the narrator's own generated
# prose. The disabled short-circuit above (the default) is never logged
# here -- no provider is even resolved in that branch, and logging
# "disabled" on every single generation would just repeat this app's own
# well-documented default, adding noise without new information (same
# reasoning Step 187E applied to a simply-missing auth cookie).
_ITINERARY_NARRATOR_STAGE = "itinerary_narrator"

_NARRATOR_STATUS_TO_ERROR_CODE = {
    "failed": "PROVIDER_FAILED",
    "not_connected": "PROVIDER_NOT_CONNECTED",
    "unavailable": "DATA_UNAVAILABLE",
}


def _narrator_log_fields(
    *,
    provider_name: str,
    status: str,
    duration_ms: float,
    planning_state: PlanningState | None = None,
    report: ItineraryNarrativeReport | None = None,
) -> dict[str, object]:
    fields: dict[str, object] = {
        "provider": provider_name,
        "stage": _ITINERARY_NARRATOR_STAGE,
        "status": status,
        "duration_ms": round(duration_ms, 3),
    }
    error_code = _NARRATOR_STATUS_TO_ERROR_CODE.get(status)
    if error_code is not None:
        fields["error_code"] = error_code
    # Section 195 (Task 34): plain counts only -- never a prompt, a
    # candidate name, or a validator message.
    if planning_state is not None:
        experience_plan = planning_state.experience_plan
        fields["day_count"] = len(experience_plan.daily_plans) if experience_plan else 0
        fields["final_plan_item_count"] = (
            sum(len(day.experiences) for day in experience_plan.daily_plans) if experience_plan else 0
        )
        fields["repair_attempt_count"] = planning_state.ai_itinerary_repair_attempt_count
        validation_report = planning_state.validation_report
        fields["remaining_validation_issue_count"] = (
            len(validation_report.critical_issues) + len(validation_report.warnings)
            if validation_report is not None
            else 0
        )
    if report is not None:
        fields["narrative_reference_count"] = sum(
            len(day.referenced_experience_ids) for day in report.daily_narratives
        )
    return fields


class ItineraryNarrativeService:
    def __init__(
        self,
        request_builder: ItineraryNarrativeRequestBuilder | None = None,
        provider: ItineraryNarratorProvider | None = None,
    ) -> None:
        self.request_builder = request_builder or itinerary_narrative_request_builder
        # Explicit injection (tests only, in production this stays None
        # and _resolve_provider() below reads it fresh from the factory
        # every call, so a config change takes effect on the next
        # generation/regeneration without restarting the process).
        self._provider_override = provider

    def generate(self, planning_state: PlanningState) -> PlanningState:
        settings = get_settings()

        if not settings.itinerary_narrator_enabled:
            planning_state.itinerary_narrative_report = ItineraryNarrativeReport(
                status=ItineraryNarrativeStatus.NOT_CONNECTED,
                message=_DISABLED_MESSAGE,
            )
            return planning_state

        provider = self._provider_override or get_itinerary_narrator_provider()
        provider_name = getattr(provider, "provider_name", "itinerary_narrator_provider")

        started_at = time.monotonic()
        try:
            request = self.request_builder.build_request(planning_state)
            report = provider.narrate(request)
            if report.status == ItineraryNarrativeStatus.SUCCESS:
                # Section 202B.3: neutral deterministic titles, then a
                # structural grounding check; a rejected narration falls
                # back rather than surfacing unsupported wording.
                report = normalize_day_titles(report)
                if find_ungrounded_terms(request, report):
                    report = grounding_rejected_report(report)
            report = _apply_final_validation_disclosure(report, request)
        except Exception:
            duration_ms = (time.monotonic() - started_at) * 1000
            logger.warning(
                "ItineraryNarrativeService.generate failed unexpectedly; leaving the plan "
                "otherwise unaffected.",
                exc_info=True,
                extra=_narrator_log_fields(
                    provider_name=provider_name,
                    status="failed",
                    duration_ms=duration_ms,
                    planning_state=planning_state,
                ),
            )
            report = ItineraryNarrativeReport(
                status=ItineraryNarrativeStatus.FAILED,
                message=_UNEXPECTED_FAILURE_MESSAGE,
            )
            planning_state.itinerary_narrative_report = self._with_fallback(planning_state, report)
            return planning_state

        duration_ms = (time.monotonic() - started_at) * 1000
        fields = _narrator_log_fields(
            provider_name=provider_name,
            status=report.status.value,
            duration_ms=duration_ms,
            planning_state=planning_state,
            report=report,
        )
        if report.status == ItineraryNarrativeStatus.SUCCESS:
            logger.info("ItineraryNarrativeService.generate completed.", extra=fields)
        else:
            # A real, honest non-success outcome returned without an
            # exception (e.g. not_connected/unavailable) -- never logged
            # as if it were a success.
            logger.warning("ItineraryNarrativeService.generate did not succeed.", extra=fields)
            report = self._with_fallback(planning_state, report)
        planning_state.itinerary_narrative_report = report
        return planning_state


    @staticmethod
    def _with_fallback(
        planning_state: PlanningState, attempt: ItineraryNarrativeReport
    ) -> ItineraryNarrativeReport:
        """Section 202B.3 (Tasks 4/5): AI narration did not succeed -- keep the
        plan untouched and attach a fact-only narrative from the final state,
        with a fixed, safe status message. Never raises."""
        try:
            return build_deterministic_narrative(planning_state, attempt)
        except Exception:
            logger.warning("Deterministic narrative fallback failed.", exc_info=True)
            return ItineraryNarrativeReport(
                status=attempt.status, message=safe_narrator_message(attempt) or SAFE_AI_UNAVAILABLE_MESSAGE
            )


itinerary_narrative_service = ItineraryNarrativeService()
