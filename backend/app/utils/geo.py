from __future__ import annotations

import math

from app.models.common import GeoPoint

EARTH_RADIUS_KM = 6371.0088


def haversine_distance_km(origin: GeoPoint | None, destination: GeoPoint | None) -> float | None:
    """Straight-line (great-circle) distance in kilometers between two points.

    This is a geometric estimate only, not a route, walking, or travel-time
    distance -- those require a RoutesProvider (docs/12_provider_architecture.md).
    Returns None if either point is missing, since no distance can be
    calculated without both coordinates.
    """
    if origin is None or destination is None:
        return None

    lat1 = math.radians(origin.lat)
    lng1 = math.radians(origin.lng)
    lat2 = math.radians(destination.lat)
    lng2 = math.radians(destination.lng)

    dlat = lat2 - lat1
    dlng = lng2 - lng1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return EARTH_RADIUS_KM * c


def point_in_bounding_box(
    point: GeoPoint, bounding_box: tuple[float, float, float, float]
) -> bool:
    """True if `point` falls inside `bounding_box` (`south, north, west,
    east` -- matching Nominatim's `boundingbox` field order).

    Used to verify a candidate place is actually located within a resolved
    destination area, rather than trusted purely because it fell inside a
    raw provider search radius (docs/12_provider_architecture.md).
    """
    south, north, west, east = bounding_box
    return south <= point.lat <= north and west <= point.lng <= east
