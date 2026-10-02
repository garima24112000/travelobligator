"""Cheap geographic heuristics for day ordering (Section 203C.2B).

These functions only decide WHICH alternative order (or which stop) is
worth asking the routing provider about. They never produce a distance or
duration that is shown, stored or validated: every movement value in a
plan comes from the routing provider. Keeping them here keeps the routing
services free of any straight-line computation.
"""

from __future__ import annotations

from typing import Sequence, TypeVar

from app.models.common import GeoPoint
from app.utils.geo import haversine_distance_km

# An alternative order is only worth a routing request for a day with at
# least three stops, and only when its straight-line length is at most this
# fraction of the current order's.
MIN_STOPS_FOR_ALTERNATIVE = 3
ALTERNATIVE_MAX_LENGTH_RATIO = 0.85
# A stop is flagged as belonging to another day when it is this many times
# farther from its own day's centre than from the other day's (and at least
# `MISPLACED_MIN_KM` away from its own).
MISPLACED_DISTANCE_RATIO = 2.5
MISPLACED_MIN_KM = 3.0

T = TypeVar("T")


def _gap_km(a: GeoPoint | None, b: GeoPoint | None) -> float:
    distance = haversine_distance_km(a, b)
    return distance if distance is not None else float("inf")


def path_length_km(points: Sequence[GeoPoint | None]) -> float:
    return sum(_gap_km(a, b) for a, b in zip(points, points[1:]))


def nearest_next_order(items: Sequence[T], points: Sequence[GeoPoint | None]) -> list[T]:
    """First item kept first, then always the nearest remaining one."""
    if not items:
        return []
    order = [0]
    remaining = list(range(1, len(items)))
    while remaining:
        current = points[order[-1]]
        nearest = min(remaining, key=lambda index: _gap_km(current, points[index]))
        remaining.remove(nearest)
        order.append(nearest)
    return [items[index] for index in order]


def clearly_shorter_alternative(items: Sequence[T], points: Sequence[GeoPoint | None]) -> list[T] | None:
    """A different order whose straight-line length is clearly shorter than
    the current one, or None when no such order is worth a routing request."""
    if len(items) < MIN_STOPS_FOR_ALTERNATIVE:
        return None
    by_item = {id(item): point for item, point in zip(items, points)}
    alternative = nearest_next_order(items, points)
    if [id(item) for item in alternative] == [id(item) for item in items]:
        return None
    current_km = path_length_km(points)
    alternative_km = path_length_km([by_item[id(item)] for item in alternative])
    if current_km <= 0 or current_km == float("inf") or alternative_km > ALTERNATIVE_MAX_LENGTH_RATIO * current_km:
        return None
    return alternative


def centroid(points: Sequence[GeoPoint | None]) -> GeoPoint | None:
    real = [point for point in points if point is not None]
    if not real:
        return None
    return GeoPoint(lat=sum(p.lat for p in real) / len(real), lng=sum(p.lng for p in real) / len(real))


def misplaced_stops(days: Sequence[Sequence[GeoPoint | None]]) -> list[tuple[int, int, int]]:
    """`(day_index, stop_index, better_day_index)` for every stop that sits
    far from the rest of its own day and much closer to another day's
    stops -- the west/east/west pattern. Coordinates only."""
    flagged: list[tuple[int, int, int]] = []
    for day_index, day in enumerate(days):
        if len(day) < 2:
            continue
        for stop_index, point in enumerate(day):
            if point is None:
                continue
            own_centre = centroid([p for i, p in enumerate(day) if i != stop_index])
            own_km = _gap_km(point, own_centre)
            if own_km < MISPLACED_MIN_KM or own_km == float("inf"):
                continue
            for other_index, other in enumerate(days):
                if other_index == day_index or not other:
                    continue
                other_km = _gap_km(point, centroid(other))
                if other_km * MISPLACED_DISTANCE_RATIO <= own_km:
                    flagged.append((day_index, stop_index, other_index))
                    break
    return flagged
