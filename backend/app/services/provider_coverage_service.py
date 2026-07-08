from __future__ import annotations

from typing import Any

from app.models.common import ProviderCoverage, UnavailableDataItem
from app.models.planning_state import PlanningState
from app.models.providers import ProviderResponse
from app.providers.gateway import provider_gateway


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
        planning_state.provider_status[response.provider_name] = entry

        if coverage_field is not None and hasattr(planning_state.provider_coverage, coverage_field):
            setattr(planning_state.provider_coverage, coverage_field, response.status.value)

        for field in response.unavailable_fields:
            planning_state.unavailable_data.append(
                UnavailableDataItem(
                    field=field,
                    reason=response.message or f"{response.provider_name} data is unavailable.",
                    data_status=response.data_status,
                    source=response.provider_name,
                )
            )

        planning_state.touch()
        return planning_state

    def summarize(self, planning_state: PlanningState) -> ProviderCoverage:
        return planning_state.provider_coverage


provider_coverage_service = ProviderCoverageService()
