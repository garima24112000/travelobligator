from __future__ import annotations

from datetime import date

import pytest

from app.models.flight import FlightSearchResult, FlightSearchStatus

# Step 178D: confirms provider_coverage.flights already maps a Kiwi
# MCP-labeled FlightSearchResult exactly the same way it maps any other
# provider's result -- _flight_coverage_value (duplicated intentionally
# in planning_orchestrator.py and planning_graph_nodes.py, mirroring
# every other coverage-mapping helper in this codebase) keys purely off
# `FlightSearchResult.status`/`.offers`, never `.provider`, so no code
# change was needed for this. This file exists because, before this
# step, no test called `_flight_coverage_value` directly for any
# provider label -- required tests 6/7.


def _kiwi_result(status: FlightSearchStatus, *, with_offer: bool = False) -> FlightSearchResult:
    from app.models.common import DataStatus
    from app.models.flight import FlightOffer, FlightSegment

    offers = []
    if with_offer:
        offers = [
            FlightOffer(
                offer_id="22f525c35142000074e76ad3_0",
                provider="kiwi_mcp",
                data_status=DataStatus.LIVE,
                outbound_segments=[
                    FlightSegment(origin_airport="LGW", destination_airport="CDG", data_status=DataStatus.LIVE)
                ],
            )
        ]
    return FlightSearchResult(
        provider="kiwi_mcp",
        status=status,
        offers=offers,
        destination="Paris",
        departure_date=date(2026, 12, 15),
    )


@pytest.mark.parametrize(
    "module_path",
    ["app.services.planning_orchestrator", "app.graphs.planning_graph_nodes"],
)
class TestFlightCoverageValueForKiwiMcp:
    """Required tests 6/7: both duplicated copies of
    `_flight_coverage_value` map a kiwi_mcp-labeled FlightSearchResult
    identically to any other provider's result."""

    def _coverage_value_fn(self, module_path: str):
        import importlib

        module = importlib.import_module(module_path)
        return module._flight_coverage_value

    def test_success_with_offers_maps_to_success(self, module_path: str) -> None:
        coverage_value = self._coverage_value_fn(module_path)
        result = _kiwi_result(FlightSearchStatus.SUCCESS, with_offer=True)
        assert coverage_value(result) == "success"

    def test_success_with_zero_offers_maps_to_unavailable(self, module_path: str) -> None:
        """A `success` status with zero offers must never be reported as
        `success` coverage -- never upgraded to imply bookable inventory
        exists when it doesn't, regardless of provider label."""
        coverage_value = self._coverage_value_fn(module_path)
        result = _kiwi_result(FlightSearchStatus.SUCCESS, with_offer=False)
        assert coverage_value(result) == "unavailable"

    def test_unavailable_maps_to_unavailable(self, module_path: str) -> None:
        coverage_value = self._coverage_value_fn(module_path)
        result = _kiwi_result(FlightSearchStatus.UNAVAILABLE)
        assert coverage_value(result) == "unavailable"

    def test_failed_maps_to_failed(self, module_path: str) -> None:
        coverage_value = self._coverage_value_fn(module_path)
        result = _kiwi_result(FlightSearchStatus.FAILED)
        assert coverage_value(result) == "failed"

    def test_not_connected_maps_to_not_connected(self, module_path: str) -> None:
        coverage_value = self._coverage_value_fn(module_path)
        result = _kiwi_result(FlightSearchStatus.NOT_CONNECTED)
        assert coverage_value(result) == "not_connected"
