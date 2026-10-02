"""Inventory sufficiency gate (Section 203C.2B).

Runs after destination context, candidate quality and AI anchor
grounding/promotion, and BEFORE trip strategy and itinerary reasoning. It
answers one question from already-grounded data: is there enough verified
inventory for a useful itinerary (see `usefulness_contract` for T/R/H)?

If the pool is below the healthy buffer and the places provider can size
its own inventory, ONE bounded expansion round fetches the next page of
the same verified category groups, the pool is re-scored, and the status
is re-evaluated. The gate never invents a candidate, never lowers a
quality bar, and never fails generation: `insufficient` is recorded and
later surfaces as `readiness=blocked` + `INSUFFICIENT_VERIFIED_INVENTORY`
on an otherwise normally completed generation.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings
from app.models.inventory_sufficiency import InventorySufficiencyReport, InventorySufficiencyStatus
from app.models.planning_state import PlanningState
from app.providers.gateway import ProviderGateway, provider_gateway
from app.core.provider_usage import GenerationProviderContext
from app.services.candidate_quality_service import CandidateQualityService
from app.services.pace_targets import PaceTargets, pace_targets_for
from app.services.usefulness_contract import count_viable_candidates, inventory_status

logger = logging.getLogger(__name__)

_MESSAGES = {
    InventorySufficiencyStatus.HEALTHY: "Verified inventory is healthy for this trip.",
    InventorySufficiencyStatus.SUFFICIENT: (
        "Verified inventory covers this trip's target, without a healthy buffer."
    ),
    InventorySufficiencyStatus.THIN_BUT_USABLE: (
        "Verified inventory is thin: enough for a useful itinerary, below the trip's ideal target."
    ),
    InventorySufficiencyStatus.INSUFFICIENT: (
        "Not enough verified places were found for a useful itinerary of this length and pace."
    ),
}


def _normalize_name(name: Any) -> str:
    return str(name or "").strip().lower()


class InventorySufficiencyService:
    def __init__(
        self,
        gateway: ProviderGateway | None = None,
        quality_service: CandidateQualityService | None = None,
    ) -> None:
        self.gateway = gateway or provider_gateway
        self.quality_service = quality_service or CandidateQualityService()

    def evaluate(
        self,
        planning_state: PlanningState,
        *,
        expansion_attempted: bool = False,
        viable_before_expansion: int | None = None,
    ) -> InventorySufficiencyReport:
        targets = pace_targets_for(planning_state)
        viable = count_viable_candidates(planning_state)
        status = inventory_status(viable, targets)
        return InventorySufficiencyReport(
            status=status,
            trip_days=targets.trip_days,
            pace=targets.pace.value,
            target_stops=targets.target_stops,
            minimum_useful=targets.minimum_useful,
            healthy_buffer=targets.healthy_buffer,
            viable_candidates=viable,
            expansion_attempted=expansion_attempted,
            viable_before_expansion=viable_before_expansion,
            message=_MESSAGES[status],
        )

    def run(
        self,
        planning_state: PlanningState,
        provider_context: GenerationProviderContext | None = None,
    ) -> PlanningState:
        # A new generation starts with its single fallback pass unused.
        planning_state.usefulness_fallback_applied = False
        if planning_state.destination_context is None:
            return planning_state

        report = self.evaluate(planning_state)
        if report.status != InventorySufficiencyStatus.HEALTHY:
            targets = pace_targets_for(planning_state)
            before = report.viable_candidates
            if self._expand_once(planning_state, targets, provider_context):
                report = self.evaluate(planning_state, expansion_attempted=True, viable_before_expansion=before)

        planning_state.inventory_sufficiency_report = report
        logger.info(
            "Inventory sufficiency evaluated.",
            extra={
                "stage": "inventory_sufficiency",
                "status": report.status.value,
                "outcome": "expanded" if report.expansion_attempted else "initial",
            },
        )
        return planning_state

    def _expand_once(
        self,
        planning_state: PlanningState,
        targets: PaceTargets,
        provider_context: GenerationProviderContext | None,
    ) -> bool:
        """One further provider page of the same category groups. Returns
        True when a page was requested (whatever it contained)."""
        places_for = getattr(self.gateway, "places_for", None)
        places = places_for(provider_context) if callable(places_for) else self.gateway.places
        if not getattr(places, "supports_inventory_sizing", False):
            return False

        context = planning_state.destination_context
        pool_size = max(60, min(5 * targets.target_stops, 110))
        try:
            response = places.search_attractions(context.destination_name, {"pool_size": pool_size, "page": 1})
        except Exception:
            logger.warning("Inventory expansion request failed unexpectedly; keeping the current pool.", exc_info=True)
            return True

        seen_ids = {poi.get("place_id") for poi in context.candidate_pois if poi.get("place_id")}
        seen_names = {_normalize_name(poi.get("name")) for poi in context.candidate_pois}
        added = 0
        for place in response.data or []:
            place_dict = place.model_dump(mode="json")
            if place_dict.get("place_id") in seen_ids or _normalize_name(place_dict.get("name")) in seen_names:
                continue
            context.candidate_pois.append(place_dict)
            seen_ids.add(place_dict.get("place_id"))
            seen_names.add(_normalize_name(place_dict.get("name")))
            added += 1

        if added:
            # Re-score the enlarged pool; scores for AI-grounded anchors
            # (already computed from real provider evidence) are kept.
            previous = planning_state.candidate_quality_report
            rebuilt = self.quality_service.build_report(planning_state)
            if previous is not None and previous.ai_directed_scores:
                rebuilt = rebuilt.model_copy(update={"ai_directed_scores": previous.ai_directed_scores})
            planning_state.candidate_quality_report = rebuilt
        return True


def apply_inventory_sufficiency_safely(
    planning_state: PlanningState,
    service: InventorySufficiencyService,
    provider_context: GenerationProviderContext | None = None,
) -> PlanningState:
    """The stage runner both planning engines call. A no-op when the gate is
    disabled. An unexpected error in the expansion round never drops the
    gate: the pool as it stands is still evaluated and recorded."""
    if not get_settings().inventory_sufficiency_gate_enabled:
        return planning_state
    try:
        return service.run(planning_state, provider_context)
    except Exception:
        logger.warning(
            "InventorySufficiencyService.run failed unexpectedly; recording the unexpanded pool.", exc_info=True
        )
        if planning_state.destination_context is not None:
            planning_state.inventory_sufficiency_report = service.evaluate(planning_state)
        return planning_state
