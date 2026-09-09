from __future__ import annotations

import logging

from app.core.config import get_settings
from app.models.itinerary_narrative import ItineraryNarrativeReport, ItineraryNarrativeStatus
from app.models.planning_state import PlanningState
from app.providers.itinerary_narrator import ItineraryNarratorProvider, get_itinerary_narrator_provider
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

        try:
            request = self.request_builder.build_request(planning_state)
            report = provider.narrate(request)
        except Exception:
            logger.warning(
                "ItineraryNarrativeService.generate failed unexpectedly; leaving the plan "
                "otherwise unaffected.",
                exc_info=True,
            )
            report = ItineraryNarrativeReport(
                status=ItineraryNarrativeStatus.FAILED,
                message=_UNEXPECTED_FAILURE_MESSAGE,
            )

        planning_state.itinerary_narrative_report = report
        return planning_state


itinerary_narrative_service = ItineraryNarrativeService()
