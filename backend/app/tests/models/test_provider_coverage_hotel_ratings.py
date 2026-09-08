from __future__ import annotations

from app.models.common import ProviderCoverage

# Step 177D: ProviderCoverage.hotel_ratings model tests. Required test 1.


def test_provider_coverage_accepts_hotel_ratings_field() -> None:
    coverage = ProviderCoverage(hotel_ratings="success")
    assert coverage.hotel_ratings == "success"


def test_provider_coverage_defaults_hotel_ratings_to_none() -> None:
    """Old, already-persisted ProviderCoverage data built without a
    hotel_ratings key (from before Step 177D) must still validate, with
    hotel_ratings defaulting to None rather than a guessed value."""
    coverage = ProviderCoverage(places="success", hotel_prices="unavailable")
    assert coverage.hotel_ratings is None
    assert coverage.places == "success"
    assert coverage.hotel_prices == "unavailable"


def test_provider_coverage_round_trips_hotel_ratings_via_model_dump() -> None:
    coverage = ProviderCoverage(hotel_ratings="partial")
    dumped = coverage.model_dump()
    assert dumped["hotel_ratings"] == "partial"

    restored = ProviderCoverage.model_validate(dumped)
    assert restored.hotel_ratings == "partial"
