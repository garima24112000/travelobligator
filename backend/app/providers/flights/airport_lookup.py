"""Nearby commercial-airport lookup for flight searches (M2 Phase 2, Kiwi
remediation, second correction).

Kiwi's MCP `search-flight` tool takes ONE place name or airport code per side
and never says which airports it matched to a name, so a place with no airport
of its own comes back with zero offers that look exactly like "no flights".
This lookup finds an airport near such a place that has evidence of SCHEDULED
PASSENGER SERVICE, so the search can be repeated against it with provenance.

Why not the nearest airport, and why not Geoapify's `airport.international`:
the live verification asked Geoapify Places for `airport.international` and got
a seaplane base and an executive (general-aviation) airport -- the two nearest
features, both with an IATA tag, neither with scheduled airline service. A
category or an IATA code is not evidence of commercial service.

Evidence used: the OurAirports data set (public domain; ourairports.com/data,
data dictionary checked 2026-10-23), supplied by the operator as a local CSV
file (`FLIGHT_AIRPORT_DATA_PATH`; nothing is downloaded at runtime). Its
`scheduled_service` column is "yes" when "the airport currently has scheduled
airline service", and its `type` separates large / medium / small land
airports from heliports, seaplane bases, balloonports and closed airports.

Selection (no airport is hardcoded; no place name is read):
  * eligible only: `scheduled_service == "yes"`, type `large_airport` /
    `medium_airport` / `small_airport` (never heliport, seaplane base,
    balloonport or closed), a valid three-letter IATA code, and within the
    straight-line radius of the geocoded place;
  * ranked by service class first (large: "scheduled major airline service";
    medium: "scheduled regional airline service"; small last), then by
    straight-line distance, then IATA code as a neutral tie-break -- so a
    nearer airport with less service never displaces a larger one in range.

The place itself is located with the application's configured geocoder (one
request, charged to the generation's provider context). Distances are
straight-line between coordinates, never travel distances. The data set comes
"with no guarantee of accuracy": every selected airport carries its evidence,
the data set's fingerprint and the rationale, and a missing or unreadable file
is `not_connected` -- an airport is never called suitable without evidence.

Selected by `FLIGHT_AIRPORT_LOOKUP_PROVIDER` (`not_connected` by default).
"""

from __future__ import annotations

import copy
import csv
import hashlib
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.providers.errors import ProviderRequestError

LOOKUP_SOURCE = "ourairports"
EVIDENCE_SCHEDULED_SERVICE = "ourairports:scheduled_service=yes"
_MAX_AIRPORTS = 3
_IATA = re.compile(r"^[A-Z]{3}$")
# Service class: lower is better. Anything not listed is never eligible.
_CLASS_RANK = {"large_airport": 0, "medium_airport": 1, "small_airport": 2}
_CLASS_WORDS = {
    "large_airport": "a large airport (scheduled major airline service)",
    "medium_airport": "a medium airport (scheduled regional airline service)",
    "small_airport": "a small airport with scheduled service",
}
_REQUIRED_COLUMNS = frozenset(
    {"ident", "type", "name", "latitude_deg", "longitude_deg", "scheduled_service", "iata_code"}
)

STATUS_FOUND = "found"
STATUS_NONE_NEARBY = "none_nearby"
STATUS_PLACE_NOT_FOUND = "place_not_found"
STATUS_NOT_CONNECTED = "not_connected"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class NearbyAirport:
    name: str
    straight_line_km: float
    provider_place_id: str
    iata_code: str | None = None
    lat: float | None = None
    lon: float | None = None
    airport_type: str | None = None
    country: str | None = None
    # Evidence of scheduled passenger service; None = no evidence (never selected).
    commercial_service_evidence: str | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class AirportLookupResult:
    status: str
    radius_km: float | None = None
    airports: tuple[NearbyAirport, ...] = ()
    failure_kind: str | None = None
    requests: int = 0
    source: str | None = None
    dataset_version: str | None = None
    # How many candidates within the radius were left out, by reason (counts only).
    excluded: dict[str, int] = field(default_factory=dict)


def straight_line_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (a straight-line proxy, never a route)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


class AirportLookupProvider:
    """Not connected: never sends a request."""

    provider_name = "not_connected"

    def bound_to(self, provider_context: Any) -> "AirportLookupProvider":
        return self

    def nearby_airports(self, place_text: str) -> AirportLookupResult:
        return AirportLookupResult(status=STATUS_NOT_CONNECTED)


# -- the data set ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    ident: str
    name: str
    airport_type: str
    lat: float
    lon: float
    scheduled: bool
    iata: str | None
    country: str | None


@dataclass(frozen=True)
class AirportDataset:
    rows: tuple[_Row, ...]
    version: str  # "sha256:<first 16 hex>" of the file read


_CACHE: dict[tuple[str, float, int], AirportDataset] = {}
_CACHE_LOCK = threading.Lock()


class AirportDataUnavailable(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def load_dataset(path: str | os.PathLike[str] | None) -> AirportDataset:
    """Reads an OurAirports `airports.csv`, keeping only land airports with an
    IATA code (heliports, seaplane bases, balloonports and closed airports are
    never kept). Cached per (path, modification time, size)."""
    if not path:
        raise AirportDataUnavailable("airport_data_not_configured")
    file = Path(path).expanduser()
    try:
        stat = file.stat()
    except OSError:
        raise AirportDataUnavailable("airport_data_missing") from None
    key = (str(file.resolve()), stat.st_mtime, stat.st_size)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        raw = file.read_bytes()
        reader = csv.DictReader(raw.decode("utf-8-sig").splitlines())
        if not _REQUIRED_COLUMNS <= set(reader.fieldnames or ()):
            raise AirportDataUnavailable("airport_data_malformed")
        rows = []
        for record in reader:
            airport_type = (record.get("type") or "").strip()
            iata = (record.get("iata_code") or "").strip()
            if airport_type not in _CLASS_RANK or not _IATA.fullmatch(iata):
                continue
            try:
                lat, lon = float(record["latitude_deg"]), float(record["longitude_deg"])
            except (TypeError, ValueError):
                continue
            rows.append(_Row(
                ident=(record.get("ident") or "").strip()[:16],
                name=(record.get("name") or "").strip()[:160],
                airport_type=airport_type,
                lat=lat,
                lon=lon,
                scheduled=(record.get("scheduled_service") or "").strip().lower() == "yes",
                iata=iata,
                country=(record.get("iso_country") or "").strip()[:2] or None,
            ))
    except AirportDataUnavailable:
        raise
    except (OSError, UnicodeDecodeError, csv.Error):
        raise AirportDataUnavailable("airport_data_malformed") from None
    dataset = AirportDataset(rows=tuple(rows), version="sha256:" + hashlib.sha256(raw).hexdigest()[:16])
    with _CACHE_LOCK:
        _CACHE[key] = dataset
    return dataset


def clear_dataset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def select_commercial_airports(
    dataset: AirportDataset, lat: float, lon: float, radius_km: float
) -> tuple[list[NearbyAirport], dict[str, int]]:
    """Eligible airports within `radius_km`, best first (see module docstring),
    and the count of in-range candidates left out, by reason."""
    excluded: dict[str, int] = {}
    chosen: list[tuple[int, float, str, _Row]] = []
    for row in dataset.rows:
        distance = straight_line_km(lat, lon, row.lat, row.lon)
        if distance > radius_km:
            continue
        if not row.scheduled:
            excluded["no_scheduled_service"] = excluded.get("no_scheduled_service", 0) + 1
            continue
        chosen.append((_CLASS_RANK[row.airport_type], distance, row.iata or "", row))
    chosen.sort(key=lambda item: (item[0], item[1], item[2]))
    airports = []
    for rank, distance, _, row in chosen[:_MAX_AIRPORTS]:
        km = round(distance, 1)
        airports.append(NearbyAirport(
            name=row.name or row.iata or row.ident,
            straight_line_km=km,
            provider_place_id=f"ourairports/{row.ident}",
            iata_code=row.iata,
            lat=round(row.lat, 5),
            lon=round(row.lon, 5),
            airport_type=row.airport_type,
            country=row.country,
            commercial_service_evidence=EVIDENCE_SCHEDULED_SERVICE,
            rationale=(
                f"Chosen as {_CLASS_WORDS[row.airport_type]} with scheduled airline service in the OurAirports "
                f"data, about {km:g} km in a straight line; larger service classes are preferred over nearer "
                "smaller ones, and airports without scheduled service are never chosen."
            ),
        ))
    return airports, excluded


class OurAirportsLookup(AirportLookupProvider):
    """Geocoder (one request) + the local OurAirports file (no request)."""

    provider_name = "ourairports_file"

    def __init__(self, geocoder: Any | None = None, data_path: str | None = None) -> None:
        settings = get_settings()
        self._radius_km = settings.flight_airport_lookup_radius_km
        self._data_path = data_path if data_path is not None else settings.flight_airport_data_path
        if geocoder is None:
            from app.providers.geocoding.factory import get_geocoding_provider

            geocoder = get_geocoding_provider()
        self._geocoder = geocoder

    def bound_to(self, provider_context: Any) -> "OurAirportsLookup":
        if provider_context is None:
            return self
        bound = copy.copy(self)
        bind = getattr(self._geocoder, "bound_to", None)
        bound._geocoder = bind(provider_context) if callable(bind) else self._geocoder
        return bound

    def _client(self) -> httpx.Client:
        return httpx.Client()

    def nearby_airports(self, place_text: str) -> AirportLookupResult:
        # The evidence first: without it no request is worth making.
        try:
            dataset = load_dataset(self._data_path)
        except AirportDataUnavailable as exc:
            return AirportLookupResult(status=STATUS_NOT_CONNECTED, failure_kind=exc.kind, source=LOOKUP_SOURCE)
        try:
            with self._client() as client:
                hit = self._geocoder.search_destination(client, place_text)
        except ProviderRequestError as exc:
            return AirportLookupResult(status=STATUS_FAILED, failure_kind=exc.kind, requests=1,
                                       source=LOOKUP_SOURCE, dataset_version=dataset.version)
        if hit is None or hit.feature_class != "place":
            return AirportLookupResult(status=STATUS_PLACE_NOT_FOUND, requests=1, source=LOOKUP_SOURCE,
                                       dataset_version=dataset.version)
        airports, excluded = select_commercial_airports(dataset, hit.lat, hit.lon, self._radius_km)
        return AirportLookupResult(
            status=STATUS_FOUND if airports else STATUS_NONE_NEARBY,
            radius_km=self._radius_km,
            airports=tuple(airports),
            requests=1,
            source=LOOKUP_SOURCE,
            dataset_version=dataset.version,
            excluded=excluded,
        )


def get_airport_lookup_provider(provider_name: str | None = None) -> AirportLookupProvider:
    name = (provider_name or get_settings().flight_airport_lookup_provider or "not_connected").strip().lower()
    if name == "ourairports_file":
        return OurAirportsLookup()
    return AirportLookupProvider()
