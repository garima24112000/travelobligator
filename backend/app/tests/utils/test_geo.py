from __future__ import annotations

from app.models.common import GeoPoint
from app.utils.geo import haversine_distance_km


def test_same_point_returns_near_zero() -> None:
    point = GeoPoint(lat=48.8566, lng=2.3522)
    distance = haversine_distance_km(point, point)
    assert distance is not None
    assert distance == 0 or abs(distance) < 0.001


def test_known_city_distance_within_tolerance() -> None:
    paris = GeoPoint(lat=48.8566, lng=2.3522)
    london = GeoPoint(lat=51.5074, lng=-0.1278)

    distance = haversine_distance_km(paris, london)

    # Commonly cited great-circle distance between Paris and London is ~344 km.
    assert distance is not None
    assert abs(distance - 344) < 5


def test_missing_origin_returns_none() -> None:
    destination = GeoPoint(lat=51.5074, lng=-0.1278)
    assert haversine_distance_km(None, destination) is None


def test_missing_destination_returns_none() -> None:
    origin = GeoPoint(lat=48.8566, lng=2.3522)
    assert haversine_distance_km(origin, None) is None


def test_both_missing_returns_none() -> None:
    assert haversine_distance_km(None, None) is None
