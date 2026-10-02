"""Real-world entity identity for place de-duplication (Section 203C.2B).

One real place can reach the candidate pool more than once: under an
English and a local-script name, through the broad Places search and
through a named lookup, or as two provider records. This module decides,
from FACTUAL provider evidence only, whether two normalised places are the
same real entity. In order:

  1. `place_id`        -- the same Geoapify place id;
  2. `source_identity` -- the same underlying source object (the
                          OpenStreetMap type + id the provider's own
                          `datasource.raw` carries), or the same Wikidata
                          entity for two places close together;
  3. `name_proximity`  -- one of their provider-supplied names is equal
                          (script-independent comparison, no transliteration
                          and no fuzzy matching) AND they are close together.

Two places that are merely near each other are never merged: every rule
needs an identity or an equal name.

Only a sanitised identity is kept from the provider payload: a
`osm/<type>/<id>` string and a short list of the place's own alternative
names. The public identity of a place stays its Geoapify `place_id`; the
source identity is an internal corroboration signal and is never used as
an id.
"""

from __future__ import annotations

from typing import Any, MutableMapping

from app.models.providers import NormalizedPlace
from app.utils.geo import haversine_distance_km
from app.utils.names import comparable_name

MERGED_BY_PLACE_ID = "place_id"
MERGED_BY_SOURCE_IDENTITY = "source_identity"
MERGED_BY_NAME_PROXIMITY = "name_proximity"

_OSM_TYPES = {"n": "node", "node": "node", "w": "way", "way": "way", "r": "relation", "relation": "relation"}
# Name keys of the underlying source record that name the SAME place; any
# per-language `name:<code>` key counts too (see `alternate_names`).
_NAME_KEYS = ("name", "name:en", "int_name", "official_name", "alt_name", "short_name", "loc_name", "old_name")
_MAX_ALT_NAMES = 12
_MAX_NAME_LENGTH = 120
# Two records this close are "co-located": together with a compatible class
# and no identity evidence either way, they are a SUSPECTED duplicate pair.
# Co-location alone never merges anything.
COLLOCATED_METERS = 50.0

RESOLVED_MERGED = "merged"
RESOLVED_DISTINCT = "distinct"
UNRESOLVED = "unresolved"
# Two records of one Wikidata entity are merged only when they are also
# close together (parts of one large site keep separate records otherwise).
_SAME_WIKIDATA_METERS = 1000.0


def source_entity_id(raw: Any) -> str | None:
    """`osm/<type>/<id>` from a provider `datasource.raw` object, or None
    when it carries no usable source identity. Nothing else is read."""
    if not isinstance(raw, dict):
        return None
    osm_type = _OSM_TYPES.get(str(raw.get("osm_type") or "").strip().lower())
    osm_id = raw.get("osm_id")
    if isinstance(osm_id, bool) or not isinstance(osm_id, (int, str)):
        return None
    digits = str(osm_id).strip().lstrip("-")
    if osm_type is None or not digits.isdigit():
        return None
    return f"osm/{osm_type}/{digits}"


def alternate_names(raw: Any, primary_name: str) -> list[str] | None:
    """The place's own other names from the source record (local name,
    English name, international name) -- for comparison only."""
    if not isinstance(raw, dict):
        return None
    seen = {comparable_name(primary_name)}
    names: list[str] = []
    language_keys = sorted(key for key in raw if isinstance(key, str) and key.startswith("name:"))
    for key in dict.fromkeys((*_NAME_KEYS, *language_keys)):
        value = raw.get(key)
        if not isinstance(value, str):
            continue
        name = value.strip()[:_MAX_NAME_LENGTH]
        comparable = comparable_name(name)
        if comparable and comparable not in seen:
            seen.add(comparable)
            names.append(name)
    return names[:_MAX_ALT_NAMES] or None


def _names(place: NormalizedPlace) -> set[str]:
    return {name for name in (comparable_name(n) for n in (place.name, *(place.alt_names or []))) if name}


def _meters(a: NormalizedPlace, b: NormalizedPlace) -> float | None:
    if a.coordinates is None or b.coordinates is None:
        return None
    distance_km = haversine_distance_km(a.coordinates, b.coordinates)
    return distance_km * 1000.0 if distance_km is not None else None


def _wikidata(place: NormalizedPlace) -> str | None:
    value = (place.provider_tags or {}).get("wikidata")
    return value.strip() if isinstance(value, str) and value.strip() else None


def merge_rule(a: NormalizedPlace, b: NormalizedPlace, name_meters: float) -> str | None:
    """The rule by which `a` and `b` are the same real entity, or None."""
    if a.place_id == b.place_id:
        return MERGED_BY_PLACE_ID
    if a.source_entity_id and a.source_entity_id == b.source_entity_id:
        return MERGED_BY_SOURCE_IDENTITY
    meters = _meters(a, b)
    if meters is None:
        return None
    wikidata = _wikidata(a)
    if wikidata and wikidata == _wikidata(b) and meters <= _SAME_WIKIDATA_METERS:
        return MERGED_BY_SOURCE_IDENTITY
    # Proximity alone never merges: a shared provider-supplied name is required.
    if meters <= name_meters and _names(a) & _names(b):
        return MERGED_BY_NAME_PROXIMITY
    return None


def separation_meters(a: NormalizedPlace, b: NormalizedPlace) -> float | None:
    return _meters(a, b)


def name_variants(place: NormalizedPlace) -> list[str]:
    """The provider-supplied names of a place: its name, then its other names."""
    return [place.name, *(place.alt_names or [])]


def conclusively_distinct(a: NormalizedPlace, b: NormalizedPlace) -> bool:
    """Provider evidence that two records are DIFFERENT real entities: each
    names a Wikidata entity and they are not the same one."""
    first, second = _wikidata(a), _wikidata(b)
    return bool(first and second and first != second)


def suspect_pair(a: NormalizedPlace, b: NormalizedPlace, compatible: bool) -> bool:
    """True for a SUSPECTED duplicate: co-located records of a compatible
    class that no identity rule merges and no evidence tells apart. This is
    a suspicion to investigate or to keep off one itinerary -- never a
    reason to merge."""
    if not compatible or merge_rule(a, b, COLLOCATED_METERS) is not None or conclusively_distinct(a, b):
        return False
    meters = _meters(a, b)
    return meters is not None and meters <= COLLOCATED_METERS


def collision_record(
    a: NormalizedPlace,
    b: NormalizedPlace,
    classes: tuple[str, str],
    resolution: str,
    merged_by: str | None = None,
    enrichment_attempted: bool = False,
) -> dict[str, Any]:
    """Secret-safe diagnostics for one suspicious pair: canonical provider
    ids, name variants, whether each record carries a source identity and a
    Wikidata identity (yes/no only -- never the identity itself), the
    coordinate separation and the coarse classes. No provider payload."""
    meters = _meters(a, b)
    return {
        "place_ids": [a.place_id, b.place_id],
        "name_variants": [name_variants(a), name_variants(b)],
        "coarse_classes": list(classes),
        "separation_meters": round(meters, 1) if meters is not None else None,
        "source_identity_present": [bool(a.source_entity_id), bool(b.source_entity_id)],
        "wikidata_identity_present": [bool(_wikidata(a)), bool(_wikidata(b))],
        "enrichment_attempted": enrichment_attempted,
        "resolution": resolution,
        "merged_by": merged_by,
    }


def absorb(kept: NormalizedPlace, duplicate: NormalizedPlace) -> NormalizedPlace:
    """`kept` with the duplicate's identity evidence added: its names (so a
    later record in either language still matches), its source identity and
    its Wikipedia/Wikidata references where `kept` has none. `kept`'s own
    id, name, coordinates and category never change."""
    known = _names(kept)
    extra = [
        name
        for name in (duplicate.name, *(duplicate.alt_names or []))
        if comparable_name(name) and comparable_name(name) not in known
    ]
    tags = dict(kept.provider_tags or {})
    for key in ("wikidata", "wikipedia"):
        value = (duplicate.provider_tags or {}).get(key)
        if value and not tags.get(key):
            tags[key] = value
    return kept.model_copy(
        update={
            "alt_names": ([*(kept.alt_names or []), *extra][:_MAX_ALT_NAMES * 2]) or None,
            "source_entity_id": kept.source_entity_id or duplicate.source_entity_id,
            "provider_tags": tags or None,
        }
    )


def dedupe_places(
    places: list[NormalizedPlace],
    name_meters: float,
    merges: MutableMapping[str, int] | None = None,
) -> list[NormalizedPlace]:
    """One place per real entity, first occurrence kept. `merges` (when
    given) counts the duplicates removed, by the rule that matched."""
    kept: list[NormalizedPlace] = []
    for place in places:
        for index, existing in enumerate(kept):
            rule = merge_rule(existing, place, name_meters)
            if rule is not None:
                kept[index] = absorb(existing, place)
                if merges is not None:
                    merges[rule] = merges.get(rule, 0) + 1
                break
        else:
            kept.append(place)
    return kept
