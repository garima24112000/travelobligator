from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.providers.gateway import ProviderGateway
from app.services.route_burden import day_route_burdens
from app.services.route_feasibility_service import (
    ADAPTATION_ALLOWANCE_EXHAUSTED,
    ADAPTATION_PROVIDER_FAILED,
    RouteFeasibilityService,
)
from app.storage.provider_cache_store import ProviderCacheStore
from app.tests.providers.test_geoapify_places_routing_203c2b import (  # noqa: F401 - `configured` is a fixture
    _Network,
    _context,
    _routing,
    configured,
)
from app.tests.services.test_final_quality_correction_203c2b import _poi, _point, _state

# Remediation R1: when fewer alternate-mode (driving) requests are left than
# there are over-long walking legs, the requests go to the LONGEST walks of the
# report, across days, instead of to the earliest days. The real Geoapify
# routing adapter runs over an in-process HTTP mock, so requests are counted at
# the provider boundary. Synthetic places on a coordinate grid; nothing is
# city-specific.

_WALK_SECONDS_PER_DEGREE = 60_000.0  # 0.05 degree = 3000 s on foot: over the 2400 s walking-leg limit
_DRIVE_SECONDS_PER_DEGREE = 6_000.0


def _routes(failing_drive_origin_lat: float | None = None) -> Any:
    """Leg times from the request's own waypoints, per mode. A driving request
    starting at `failing_drive_origin_lat` gets an HTTP 500."""

    def handler(request: httpx.Request) -> httpx.Response:
        points = [tuple(float(v) for v in w.split(",")) for w in request.url.params["waypoints"].split("|")]
        drive = request.url.params["mode"] == "drive"
        if drive and failing_drive_origin_lat is not None and points[0][0] == failing_drive_origin_lat:
            return httpx.Response(500, json={})
        rate = _DRIVE_SECONDS_PER_DEGREE if drive else _WALK_SECONDS_PER_DEGREE
        legs = []
        for (lat_a, lng_a), (lat_b, lng_b) in zip(points, points[1:]):
            degrees = abs(lat_a - lat_b) + abs(lng_a - lng_b)
            legs.append({"distance": round(degrees * 100_000.0, 1), "time": round(degrees * rate, 1)})
        return httpx.Response(200, json={"features": [{"properties": {"legs": legs}, "geometry": None}]})

    return handler


def _day(index: int, degrees: float) -> list[dict[str, Any]]:
    """Two stops one leg apart; the leg's walking time is `degrees * 60000` s."""
    lat = 40.0 + index
    return [
        _poi(f"d{index}a", f"Day {index} Start", _point(lat, 20.0)),
        _poi(f"d{index}b", f"Day {index} End", _point(lat, 20.0 + degrees)),
    ]


# Walking times 3000 s, 9000 s, 6000 s: every leg is over the walking-leg limit.
_UNEQUAL = [_day(1, 0.05), _day(2, 0.15), _day(3, 0.10)]
_EQUAL = [_day(1, 0.10), _day(2, 0.10), _day(3, 0.10)]


class _Run:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        days: list[list[dict[str, Any]]],
        allowance: int,
        failing_drive_origin_lat: float | None = None,
    ) -> None:
        self.network = _Network(routing=_routes(failing_drive_origin_lat))
        store = ProviderCacheStore(tmp_path / f"cache-{id(self)}.sqlite3")
        self.gateway = ProviderGateway(routing=_routing(monkeypatch, self.network, store))
        self.context = _context(trip_days=len(days))
        self.context.alternate_mode_requests_left = allowance
        self.service = RouteFeasibilityService(gateway=self.gateway)
        self.state = _state([poi for day in days for poi in day], days)

    def build(self) -> None:
        """The ordinary report: walking legs, then the driving batch."""
        self.state.route_feasibility_report = self.service.build_report(self.state, self.context)

    def deferred(self) -> None:
        """The route-aware lifecycle: walking-only report, then one adaptation."""
        self.state.route_feasibility_report = self.service.build_report(self.state, self.context, adapt_modes=False)
        self.service.adapt_report_modes(self.state, self.context)

    def drives(self) -> list[httpx.Request]:
        return [r for r in self.network.requests["routing"] if r.url.params["mode"] == "drive"]

    def legs(self) -> list[tuple[str, str | None, float | None, float | None, float | None, bool, str | None]]:
        return [
            (leg.from_experience_name, leg.mode, leg.duration_seconds, leg.distance_meters,
             leg.walking_duration_seconds, leg.mode_adaptation_attempted, leg.mode_adaptation_outcome)
            for leg in self.state.route_feasibility_report.legs
        ]

    def modes(self) -> list[str | None]:
        return [leg.mode for leg in self.state.route_feasibility_report.legs]


@pytest.mark.parametrize("lifecycle", ["build", "deferred"])
def test_the_longest_walks_get_the_driving_requests_across_days(
    lifecycle: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    """Three over-long legs, two driving requests left: days 2 and 3 (9000 s
    and 6000 s) get them; day 1 (3000 s) keeps its walking route. In day order
    the two would have gone to days 1 and 2, leaving the 6000 s walk."""
    run = _Run(monkeypatch, tmp_path, _UNEQUAL, allowance=2)
    getattr(run, lifecycle)()

    assert run.modes() == ["walk", "drive", "drive"]  # legs stay in day and leg order
    assert len(run.drives()) == 2 and run.context.alternate_mode_requests_left == 0
    # requested longest first: day 2's leg, then day 3's
    assert [r.url.params["waypoints"].split("|")[0] for r in run.drives()] == ["42.0,20.0", "43.0,20.0"]
    day_one = run.legs()[0]
    assert day_one[1:] == ("walk", 3000.0, 5000.0, None, True, ADAPTATION_ALLOWANCE_EXHAUSTED)


@pytest.mark.parametrize("lifecycle", ["build", "deferred"])
def test_equal_walks_keep_day_and_leg_order(
    lifecycle: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    run = _Run(monkeypatch, tmp_path, _EQUAL, allowance=2)
    getattr(run, lifecycle)()

    assert run.modes() == ["drive", "drive", "walk"]
    assert run.legs()[2][6] == ADAPTATION_ALLOWANCE_EXHAUSTED


def test_the_number_of_driving_requests_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    """Prioritising changes WHICH legs are asked for, never how many: with
    allowance to spare every long leg is asked for once, and never beyond the cap."""
    for allowance, expected in ((6, 3), (3, 3), (2, 2), (1, 1)):
        run = _Run(monkeypatch, tmp_path, _UNEQUAL, allowance=allowance)
        run.build()
        assert len(run.drives()) == expected
        assert run.context.alternate_mode_requests_left == allowance - expected
        # one walking request per day, as before
        assert len([r for r in run.network.requests["routing"] if r.url.params["mode"] == "walk"]) == 3


def test_with_no_allowance_left_every_walking_leg_stands_unchanged(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    run = _Run(monkeypatch, tmp_path, _UNEQUAL, allowance=0)
    run.build()

    assert run.drives() == []
    # the provider's own walking figures, nothing estimated or substituted
    assert [leg[1:] for leg in run.legs()] == [
        ("walk", 3000.0, 5000.0, None, True, ADAPTATION_ALLOWANCE_EXHAUSTED),
        ("walk", 9000.0, 15000.0, None, True, ADAPTATION_ALLOWANCE_EXHAUSTED),
        ("walk", 6000.0, 10000.0, None, True, ADAPTATION_ALLOWANCE_EXHAUSTED),
    ]


def test_a_failed_driving_alternative_keeps_the_verified_walking_route(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    """The longest leg (day 2) is asked for first and the provider fails it:
    that leg keeps its walking route; the failure does not hand the request
    to another leg (the allowance is taken when the request is planned)."""
    run = _Run(monkeypatch, tmp_path, _UNEQUAL, allowance=2, failing_drive_origin_lat=42.0)
    run.build()

    assert len(run.drives()) == 2
    legs = run.legs()
    assert legs[1][1:] == ("walk", 9000.0, 15000.0, None, True, ADAPTATION_PROVIDER_FAILED)
    assert legs[2][1] == "drive" and legs[2][2] == 600.0 and legs[2][4] == 6000.0
    assert legs[0][1:] == ("walk", 3000.0, 5000.0, None, True, ADAPTATION_ALLOWANCE_EXHAUSTED)


def test_an_adapted_leg_carries_only_provider_figures(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    run = _Run(monkeypatch, tmp_path, _UNEQUAL, allowance=2)
    run.build()

    for leg in run.state.route_feasibility_report.legs[1:]:
        walk_degrees = abs(leg.from_lat - leg.to_lat) + abs(leg.from_lon - leg.to_lon)
        assert leg.mode == "drive" and leg.mode_adaptation_outcome is None
        assert leg.duration_seconds == round(walk_degrees * _DRIVE_SECONDS_PER_DEGREE, 1)
        assert leg.walking_duration_seconds == round(walk_degrees * _WALK_SECONDS_PER_DEGREE, 1)
        assert leg.walking_distance_meters == leg.distance_meters == round(walk_degrees * 100_000.0, 1)


@pytest.mark.parametrize("concurrency", ["true", "false"])
def test_serial_and_concurrent_provider_io_give_the_same_allocation(
    concurrency: str, monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    monkeypatch.setenv("PROVIDER_IO_CONCURRENCY_ENABLED", concurrency)
    get_settings.cache_clear()
    try:
        run = _Run(monkeypatch, tmp_path, _UNEQUAL, allowance=2)
        run.build()
        assert [leg[1:] for leg in run.legs()] == [
            ("walk", 3000.0, 5000.0, None, True, ADAPTATION_ALLOWANCE_EXHAUSTED),
            ("drive", 900.0, 15000.0, 9000.0, True, None),
            ("drive", 600.0, 10000.0, 6000.0, True, None),
        ]
    finally:
        get_settings.cache_clear()


def test_the_severe_walk_that_remains_is_still_reported(
    monkeypatch: pytest.MonkeyPatch, configured: None, tmp_path: Path
) -> None:
    """Day 1's walk was not adapted: its burden still says so. Days 2 and 3
    became vehicle transfers within limits and are no longer long walking days."""
    run = _Run(monkeypatch, tmp_path, _UNEQUAL, allowance=2)
    run.build()

    burdens = {burden.day_number: burden for burden in day_route_burdens(run.state)}
    assert burdens[1].long_route and burdens[1].max_walk_leg_duration_seconds == 3000.0
    assert not burdens[2].excessive_walking and burdens[2].max_walk_leg_duration_seconds == 0
    assert not burdens[3].excessive_walking and burdens[3].max_walk_leg_duration_seconds == 0
