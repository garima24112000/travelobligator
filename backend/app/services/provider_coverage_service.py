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

# A provider only counts as a "data source used" when both its call status
# and its data status indicate real, usable data actually came back --
# never for not_connected/failed/unavailable/not_requested/retrying results
# (Step 152). This keeps `data_sources_used` honest instead of listing a
# provider that was merely attempted or that returned nothing usable.
_USABLE_PROVIDER_STATUSES = {"success", "partial", "fallback_used"}
_USABLE_DATA_STATUSES = {
    "live",
    "cached",
    "fallback_used",
    "scheduled",
    "estimated",
    "user_provided",
    "ai_inferred",
}


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

        # Keyed by provider + coverage field (not just provider name) so that
        # one provider handling multiple fields (e.g. openstreetmap_places
        # serving places, restaurants, and accommodations) gets a separate
        # status entry per field. Otherwise a later field's result (e.g. a
        # failed accommodation_pois lookup) would silently overwrite an
        # earlier field's real success (e.g. a successful places lookup) in
        # `provider_status`, even though `provider_coverage` already tracks
        # those fields separately.
        status_key = (
            f"{response.provider_name}:{coverage_field}"
            if coverage_field is not None
            else response.provider_name
        )

        existing_entry = planning_state.provider_status.get(status_key)
        if existing_entry is not None:
            merged_fields = list(existing_entry.unavailable_fields)
            for field in entry.unavailable_fields:
                if field not in merged_fields:
                    merged_fields.append(field)
            entry.unavailable_fields = merged_fields

        planning_state.provider_status[status_key] = entry

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

        # Deterministic, deduplicated, append-only: never clears or reorders
        # pre-existing entries (so any already-recorded data source is
        # preserved), and a provider that already appears (e.g.
        # openstreetmap_places called again for a different coverage_field)
        # is never listed twice.
        if (
            response.status.value in _USABLE_PROVIDER_STATUSES
            and response.data_status.value in _USABLE_DATA_STATUSES
            and response.provider_name not in planning_state.data_sources_used
        ):
            planning_state.data_sources_used.append(response.provider_name)

        planning_state.touch()
        return planning_state

    def summarize(self, planning_state: PlanningState) -> ProviderCoverage:
        return planning_state.provider_coverage


provider_coverage_service = ProviderCoverageService()
