from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.providers.places import openstreetmap_adapter as adapter_module
from app.providers.places.openstreetmap_adapter import OpenStreetMapPlacesAdapter
from app.storage.provider_cache_store import ProviderCacheStore

# Section 202B.1 (Tasks 12-15, 21): structural, provider-evidence-based
# destination plausibility. Result shapes below mirror real Nominatim
# `jsonv2` responses (addressdetails/namedetails/accept-language=en)
# observed during the 202A follow-up verification.


def _result(
    *,
    name: str,
    display_name: str,
    category: str = "boundary",
    type_: str = "administrative",
    namedetails: dict[str, str] | None = None,
    address: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "lat": "38.72",
        "lon": "-9.14",
        "category": category,
        "type": type_,
        "name": name,
        "display_name": display_name,
        "namedetails": namedetails or {"name": name},
        "address": address or {},
        "boundingbox": ["38.6", "38.8", "-9.3", "-9.0"],
    }


LISBON = _result(
    name="Lisbon",
    display_name="Lisbon, Portugal",
    namedetails={"name": "Lisboa", "name:en": "Lisbon", "int_name": "Lisbon"},
    address={"city": "Lisbon", "country": "Portugal", "country_code": "pt"},
)
LISBON_LOCAL_ONLY = _result(  # provider reports only the local name + name:en in namedetails
    name="Lisboa",
    display_name="Lisboa, Portugal",
    namedetails={"name": "Lisboa", "name:en": "Lisbon"},
    address={"country": "Portugal"},
)
CORDOBA_SPAIN = _result(
    name="Córdoba",
    display_name="Córdoba, Andalusia, Spain",
    namedetails={"name": "Córdoba", "name:en": "Córdoba"},
    address={"city": "Córdoba", "state": "Andalusia", "country": "Spain"},
)
CORDOBA_ARGENTINA = _result(
    name="Cordoba",
    display_name="Cordoba, Municipio de Córdoba, Argentina",
    namedetails={"name": "Córdoba", "name:en": "Cordoba"},
    address={"city": "Cordoba", "country": "Argentina"},
)
NYC = _result(
    name="New York",
    display_name="New York, United States",
    address={"city": "New York", "country": "United States"},
)
HOTEL_LISBON = _result(
    name="Hotel Lisbon", display_name="Hotel Lisbon, Maibara, Japan", category="tourism", type_="motel"
)
UNRELATED = _result(name="Rheydt", display_name="Rheydt, Germany", address={"city": "Mönchengladbach", "country": "Germany"})


@pytest.mark.parametrize(
    ("query", "result", "expected"),
    [
        ("Lisbon", LISBON, True),  # provider name is "Lisbon" (English)
        ("Lisbon", LISBON_LOCAL_ONLY, True),  # exonym accepted via the provider's own name:en
        ("Lisboa", LISBON, True),  # local name accepted via namedetails
        ("Lisbon, Portugal", LISBON, True),
        ("Cordoba", CORDOBA_SPAIN, True),  # diacritics normalized
        ("Córdoba", CORDOBA_SPAIN, True),
        ("Cordoba, Spain", CORDOBA_SPAIN, True),
        ("Cordoba", CORDOBA_ARGENTINA, True),  # ordinary city, still accepted
        ("New York City, USA", NYC, True),  # subset relation + unverifiable abbreviation
        ("New York", NYC, True),
        ("Cordoba, Spain", CORDOBA_ARGENTINA, False),  # wrong country for the supplied context
        ("Lisbon, Ohio", LISBON, False),  # supplied region contradicts the provider result
        ("Lisbon", HOTEL_LISBON, False),  # business/POI with a matching word is not a city
        ("New York", UNRELATED, False),  # clearly unrelated result
    ],
)
def test_structural_plausibility(query: str, result: dict[str, Any], expected: bool) -> None:
    assert adapter_module._is_plausible_geocode_result(query, result) is expected


def test_a_word_shared_with_an_unrelated_provider_name_is_not_enough() -> None:
    other_lisbon_business = _result(
        name="Lisbon Grill", display_name="Lisbon Grill, Newark, United States", category="amenity", type_="restaurant"
    )
    assert adapter_module._is_plausible_geocode_result("Lisbon", other_lisbon_business) is False


def test_result_with_no_structural_fields_falls_back_to_the_display_name_rule() -> None:
    minimal = {"lat": "1", "lon": "2", "display_name": "Cordoba, Spain"}
    assert adapter_module._is_plausible_geocode_result("Córdoba", minimal) is True
    assert adapter_module._is_plausible_geocode_result("Zzyzx", minimal) is False


class _Response:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class _Client:
    def __init__(self, payload: Any) -> None:
        self._payload = payload
        self.params: dict[str, Any] | None = None

    def get(self, url: str, params: dict[str, Any] | None = None) -> _Response:
        self.params = params
        return _Response(self._payload)


def test_resolve_destination_accepts_lisbon_and_asks_for_structural_evidence(tmp_path: Path) -> None:
    adapter = OpenStreetMapPlacesAdapter(cache_store=ProviderCacheStore(tmp_path / "c.sqlite3"))
    client = _Client([LISBON])

    resolved = adapter._resolve_destination(client, "Lisbon")

    assert resolved is not None
    assert resolved.display_name == "Lisbon, Portugal"
    assert client.params["addressdetails"] == 1
    assert client.params["namedetails"] == 1
    assert client.params["accept-language"] == "en"


def test_resolve_destination_accepts_cordoba_and_rejects_a_poi_match(tmp_path: Path) -> None:
    adapter = OpenStreetMapPlacesAdapter(cache_store=ProviderCacheStore(tmp_path / "c.sqlite3"))
    assert adapter._resolve_destination(_Client([CORDOBA_SPAIN]), "Cordoba, Spain") is not None
    assert adapter._resolve_destination(_Client([HOTEL_LISBON]), "Lisbon") is None


def test_unresolved_destination_is_flagged_structurally_not_by_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _CM(_Client):
        def __enter__(self) -> "_CM":
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(adapter_module.httpx, "Client", lambda **kwargs: _CM([HOTEL_LISBON]))
    adapter = OpenStreetMapPlacesAdapter(cache_store=ProviderCacheStore(tmp_path / "c.sqlite3"))

    response = adapter.search_attractions("Lisbon")

    assert response.data is None
    assert response.failure_reason == "destination_unresolved"
