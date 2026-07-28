from __future__ import annotations

from app.models.common import GeoPoint
from app.utils.geo import haversine_distance_km, point_in_bounding_box


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


def test_point_in_bounding_box_true_when_inside() -> None:
    nyc_bbox = (40.4961, 40.9153, -74.2557, -73.7002)
    empire_state_building = GeoPoint(lat=40.7484, lng=-73.9857)
    assert point_in_bounding_box(empire_state_building, nyc_bbox) is True


def test_point_in_bounding_box_false_when_outside() -> None:
    nyc_bbox = (40.4961, 40.9153, -74.2557, -73.7002)
    rathaus_rheydt = GeoPoint(lat=51.1743, lng=6.4453)
    assert point_in_bounding_box(rathaus_rheydt, nyc_bbox) is False


def test_point_in_bounding_box_boundary_is_inclusive() -> None:
    bbox = (40.0, 41.0, -75.0, -74.0)
    assert point_in_bounding_box(GeoPoint(lat=40.0, lng=-75.0), bbox) is True
    assert point_in_bounding_box(GeoPoint(lat=41.0, lng=-74.0), bbox) is True
