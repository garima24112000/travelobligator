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
# The two straight-line proximity proxies the planner already used before
# Q3, now named once. Neither is a neighbourhood definition, a walking
# distance or a travel time:
#   * `NEAR_DAY_STOPS_KM` -- a place this close to a day's other stops is
#     still "near the day" (the diversity pass's hard-replacement bound, and
#     the least distance at which a stop can count as misplaced);
#   * `AS_WELL_PLACED_KM` -- a replacement within this distance is "just as
#     well placed" as the stop it replaces (the diversity pass's soft bound).
NEAR_DAY_STOPS_KM = 3.0
AS_WELL_PLACED_KM = 1.0

# A stop is flagged as belonging to another day when it is this many times
# farther from its own day's centre than from the other day's (and at least
# `MISPLACED_MIN_KM` away from its own).
MISPLACED_DISTANCE_RATIO = 2.5
MISPLACED_MIN_KM = NEAR_DAY_STOPS_KM

# Geographic spread of ONE day: the straight-line length of its ordered,
# located stops. Beyond this the plan validator reports the day
# (`geographic_spread`), and the planner's prospective pass over AI-chosen
# days uses the very same measure and boundary -- one value, read by both.
GEOGRAPHIC_SPREAD_THRESHOLD_KM = 8.0

T = TypeVar("T")


def day_spread_km(points: Sequence[GeoPoint | None]) -> float | None:
    """Sum of straight-line distances between consecutive located stops, in
    the order given. Stops without coordinates are skipped (never invented
    or estimated); None with fewer than two located stops, since spread
    cannot be measured."""
    located = [point for point in points if point is not None]
    if len(located) < 2:
        return None
    total_km = 0.0
    for previous_point, next_point in zip(located, located[1:]):
        distance_km = haversine_distance_km(previous_point, next_point)
        if distance_km is not None:
            total_km += distance_km
    return total_km


def day_extent_km(points: Sequence[GeoPoint | None]) -> float | None:
    """The largest straight-line distance between any two located stops of a
    day -- order-independent, unlike `day_spread_km`. None with fewer than
    two located stops (nothing to measure). A proxy for how far apart a
    day's places lie; never a route length or a duration."""
    located = [point for point in points if point is not None]
    if len(located) < 2:
        return None
    return max(
        haversine_distance_km(a, b) or 0.0 for index, a in enumerate(located) for b in located[index + 1 :]
    )


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


def prospective_day_spread_km(points: Sequence[GeoPoint | None]) -> float:
    """`day_spread_km` of a set of places that has no final order yet: the
    path length of their nearest-next order (first place first), which is
    how a day is ordered before routing. 0.0 when it cannot be measured.

    This is the SAME measure the validator's boundary is defined for -- a
    straight-line PATH length through the day's stops, compared with
    `GEOGRAPHIC_SPREAD_THRESHOLD_KM`. It is not the day's extent
    (`day_extent_km`, the largest distance between two stops), and the
    boundary is never applied to that."""
    located = [point for point in points if point is not None]
    return day_spread_km(nearest_next_order(located, located)) or 0.0


def keeps_day_within_spread(day_points: Sequence[GeoPoint | None], candidate: GeoPoint | None) -> bool:
    """Whether a day that also held a place at `candidate` would still be
    within the validator's geographic-spread boundary (the prospective path
    measure above). A place without coordinates cannot be judged and is not
    refused here; a day that is already beyond the boundary is never said to
    be kept within it."""
    if candidate is None:
        return True
    return prospective_day_spread_km([*day_points, candidate]) <= GEOGRAPHIC_SPREAD_THRESHOLD_KM


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


def balanced_day_sizes(total: int, num_days: int, max_per_day: int) -> list[int]:
    """`total` stops spread over `num_days` as evenly as the pace cap allows."""
    if num_days <= 0:
        return []
    total = min(total, num_days * max_per_day)
    base, extra = divmod(total, num_days)
    return [min(max_per_day, base + (1 if index < extra else 0)) for index in range(num_days)]


def balanced_spatial_clusters(
    items: Sequence[T], points: Sequence[GeoPoint | None], sizes: Sequence[int]
) -> list[list[T]]:
    """Deterministic, capacity-balanced geographic grouping of an already
    SELECTED set into day-sized groups (Section 203C.2B, canary correction).

    `items` are in priority order (best first). The result has one group per
    entry of `sizes`; group sizes are a permutation-free match of `sizes` as
    far as the items allow, and every group lists its items in priority
    order. Days are returned with the group holding the best-ranked item
    first.

    Method (no randomness, no external dependency):
      1. seeds are spatially separated: the best-ranked located item, then
         repeatedly the item farthest from every seed chosen so far;
      2. remaining items are assigned by largest regret first (the item that
         loses most if it cannot have its nearest group), each to the
         nearest group that still has capacity, recomputing that group's
         centroid as it grows;
      3. a group left below the smallest target size takes the nearest item
         from a larger group;
      4. items without coordinates cannot be placed geographically and go
         to the smallest groups.

    This only decides WHICH stops share a day. It produces no distance or
    duration: the routing provider remains the only source of movement data.
    """
    group_count = sum(1 for size in sizes if size > 0)
    total = sum(sizes)
    chosen = list(range(min(total, len(items))))
    if group_count == 0 or not chosen:
        return [[] for _ in sizes]

    located = [index for index in chosen if points[index] is not None]
    unlocated = [index for index in chosen if points[index] is None]
    capacity = max(sizes)
    smallest_target = min(size for size in sizes if size > 0)

    seeds: list[int] = []
    if located:
        seeds.append(located[0])
        while len(seeds) < min(group_count, len(located)):
            seeds.append(
                max(
                    (index for index in located if index not in seeds),
                    key=lambda index: (min(_gap_km(points[index], points[seed]) for seed in seeds), -index),
                )
            )
    groups: list[list[int]] = [[seed] for seed in seeds]
    while len(groups) < group_count:
        groups.append([])

    def group_centre(group: list[int]) -> GeoPoint | None:
        return centroid([points[index] for index in group])

    unassigned = [index for index in located if index not in seeds]
    while unassigned:
        best: tuple[float, float, int, int] | None = None  # (-regret, nearest distance, item, group)
        for index in unassigned:
            distances = sorted(
                (_gap_km(points[index], group_centre(group)), group_index)
                for group_index, group in enumerate(groups)
                if len(group) < capacity
            )
            if not distances:
                break
            nearest_distance, nearest_group = distances[0]
            regret = (distances[1][0] - nearest_distance) if len(distances) > 1 else float("inf")
            candidate = (-regret, nearest_distance, index, nearest_group)
            if best is None or candidate < best:
                best = candidate
        if best is None:
            break
        _, _, index, group_index = best
        groups[group_index].append(index)
        unassigned.remove(index)

    for index in unlocated:
        open_groups = [group for group in groups if len(group) < capacity] or groups
        min(open_groups, key=len).append(index)

    # No day far below the others while another has room to give.
    for group in groups:
        while len(group) < smallest_target:
            donors = [donor for donor in groups if donor is not group and len(donor) > smallest_target]
            if not donors:
                break
            centre = group_centre(group)
            donor, moved = min(
                ((donor, index) for donor in donors for index in donor),
                key=lambda pair: (_gap_km(points[pair[1]], centre), pair[1]),
            )
            donor.remove(moved)
            group.append(moved)

    ordered = sorted((sorted(group) for group in groups if group), key=lambda group: group[0])
    result = [[items[index] for index in group] for group in ordered]
    while len(result) < len(sizes):
        result.append([])
    return result


def grouping_length_km(groups: Sequence[Sequence[GeoPoint | None]]) -> float:
    """Total straight-line length of every day's path, each day visited in
    the given order. Used only to compare two candidate groupings."""
    return sum(path_length_km(group) for group in groups)


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
