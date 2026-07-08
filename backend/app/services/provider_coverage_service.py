from __future__ import annotations

from typing import Any

from app.models.common import ProviderCoverage, UnavailableDataItem
from app.models.planning_state import PlanningState
from app.models.providers import ProviderResponse
from app.providers.gateway import provider_gateway

# A later failed/not_connected/unavailable result (e.g. StayTransportService's
# still-unconnected AccommodationProvider, or a later stage's provider call
# timing out) must not erase an earlier real success/partial result for the
# same coverage field (e.g. OSM candidate_pois already found by
# DestinationContextService).
_STRONG_COVERAGE_STATUSES = {"success", "partial", "fallback_used", "open_poi_available"}
_DOWNGRADE_COVERAGE_STATUSES = {"failed", "not_connected", "unavailable"}

# OSM only returns real accommodation *locations*, never bookable inventory
# (price/availability/booking links), so its accommodation coverage is
# labeled distinctly from a connected booking-capable AccommodationProvider.
_OPEN_DATA_PLACES_PROVIDER = "openstreetmap_places"
_OPEN_DATA_ACCOMMODATION_STATUSES = {"success", "partial", "fallback_used"}


class ProviderCoverageService:
    """Owns `provider_coverage` bookkeeping (docs/14_backend_architecture.md
    section 8: `ProviderCoverageService`).

    Other stage services call `record_provider_result` after every
    ProviderGateway call so that `provider_status`, `provider_coverage`, and
    `unavailable_data` stay consistent instead of each service reimplementing
    the same bookkeeping.
    """

    def record_provider_result(
        self,
        planning_state: PlanningState,
        response: ProviderResponse[Any],
        coverage_field: str | None = None,
    ) -> PlanningState:
        entry = provider_gateway.to_status_entry(response)

        existing_entry = planning_state.provider_status.get(response.provider_name)
        if existing_entry is not None:
            merged_fields = list(existing_entry.unavailable_fields)
            for field in entry.unavailable_fields:
                if field not in merged_fields:
                    merged_fields.append(field)
            entry.unavailable_fields = merged_fields

        planning_state.provider_status[response.provider_name] = entry

        if coverage_field is not None and hasattr(planning_state.provider_coverage, coverage_field):
            new_value = response.status.value
            if (
                coverage_field == "accommodations"
                and response.provider_name == _OPEN_DATA_PLACES_PROVIDER
                and new_value in _OPEN_DATA_ACCOMMODATION_STATUSES
            ):
                new_value = "open_poi_available"

            current_value = getattr(planning_state.provider_coverage, coverage_field)
            downgrades_real_coverage = (
                current_value in _STRONG_COVERAGE_STATUSES
                and new_value in _DOWNGRADE_COVERAGE_STATUSES
            )
            if not downgrades_real_coverage:
                setattr(planning_state.provider_coverage, coverage_field, new_value)

        existing_keys = {(item.source, item.field) for item in planning_state.unavailable_data}
        for field in response.unavailable_fields:
            key = (response.provider_name, field)
            if key in existing_keys:
                continue
            planning_state.unavailable_data.append(
                UnavailableDataItem(
                    field=field,
                    reason=response.message or f"{response.provider_name} data is unavailable.",
                    data_status=response.data_status,
                    source=response.provider_name,
                )
            )
            existing_keys.add(key)

        planning_state.touch()
        return planning_state

    def summarize(self, planning_state: PlanningState) -> ProviderCoverage:
        return planning_state.provider_coverage


provider_coverage_service = ProviderCoverageService()
