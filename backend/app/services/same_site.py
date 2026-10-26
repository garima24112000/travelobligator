"""Same-site records: a heritage site and a collection housed in it (Final
Batch 1, D).

A castle, fort, palace, manor, monastery or place of worship and a museum or
gallery mapped as a separate record AT that site are, for a visitor, one visit:
scheduling both spends two slots -- sometimes on two different days -- on the
same physical place. Two records are one site only when BOTH kinds of evidence
agree:

  * ROLES, from the records' own provider tags: one is a heritage site that can
    house a collection, the other a collection (museum, gallery) that is not
    itself such a site;
  * GEOGRAPHY: they are co-located -- no farther apart than the existing
    co-location rule (`entity_identity.COLLOCATED_METERS`).

Neither alone is enough. Two museums in one building, two places of worship
side by side, or a site and a museum down the street stay separate
experiences; sharing an address is never a reason. No name is read.

The group is written on the candidate (`SAME_SITE_KEY`, a deterministic id from
the site's place id) by the stage that owns the pool, and the planner treats it
like its existing sub-feature collapse: at most one record of a site is
scheduled, the more useful one (a must-visit is always kept). Pure: no
provider, routing or model call.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.models.common import GeoPoint
from app.providers.places.entity_identity import COLLOCATED_METERS
from app.utils.geo import haversine_distance_km

SAME_SITE_KEY = "same_site_group"

# Heritage sites that can house a collection (provider tag evidence only).
_SITE_TAGS: dict[str, frozenset[str]] = {
    "historic": frozenset({"castle", "fort", "palace", "manor", "monastery", "church", "cathedral", "chapel"}),
    "building": frozenset({"castle", "palace", "church", "cathedral", "chapel", "monastery", "temple", "mosque",
                           "synagogue", "shrine"}),
    "amenity": frozenset({"place_of_worship"}),
}
_COLLECTION_TAGS: dict[str, frozenset[str]] = {"tourism": frozenset({"museum", "gallery"})}

SITE = "site"
COLLECTION = "collection"


def _tagged(tags: dict[str, Any], table: dict[str, frozenset[str]]) -> bool:
    return any(str(tags.get(key) or "").lower() in values for key, values in table.items())


def role(poi: dict[str, Any]) -> str | None:
    """`site`, `collection` or None, from the record's own provider tags. A
    record that is both (a museum mapped on the site itself) has no role: it
    already IS the one visit."""
    tags = poi.get("provider_tags") or {}
    site, collection = _tagged(tags, _SITE_TAGS), _tagged(tags, _COLLECTION_TAGS)
    if site and not collection:
        return SITE
    if collection and not site:
        return COLLECTION
    return None


def _point(poi: dict[str, Any]) -> GeoPoint | None:
    coordinates = poi.get("coordinates") or {}
    if coordinates.get("lat") is None or coordinates.get("lng") is None:
        return None
    return GeoPoint(lat=coordinates["lat"], lng=coordinates["lng"])


def one_site(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """A heritage site and a collection, co-located (see module docstring)."""
    if {role(a), role(b)} != {SITE, COLLECTION}:
        return False
    first, second = _point(a), _point(b)
    if first is None or second is None:
        return False
    distance_km = haversine_distance_km(first, second)
    return distance_km is not None and distance_km * 1000.0 <= COLLOCATED_METERS


def _group_id(site: dict[str, Any]) -> str:
    return f"site:{site.get('place_id')}"


def assign_same_site_groups(pois: Iterable[dict[str, Any]]) -> None:
    """Writes `SAME_SITE_KEY` on every record that forms one site with
    another. A collection co-located with several sites joins the first (in
    pool order), so each record belongs to at most one group."""
    pois = [poi for poi in pois if poi.get("place_id")]
    sites = [poi for poi in pois if role(poi) == SITE]
    for collection in (poi for poi in pois if role(poi) == COLLECTION):
        site = next((candidate for candidate in sites if one_site(candidate, collection)), None)
        if site is not None:
            group = site.get(SAME_SITE_KEY) or _group_id(site)
            site[SAME_SITE_KEY] = group
            collection[SAME_SITE_KEY] = group


def group_for(poi: dict[str, Any], pool: Iterable[dict[str, Any]]) -> str | None:
    """The site group of a candidate that is not itself marked (a promoted
    candidate built at plan time), read against the marked pool without
    changing it."""
    if poi.get(SAME_SITE_KEY):
        return str(poi[SAME_SITE_KEY])
    for other in pool:
        if other is poi or not other.get("place_id") or not one_site(poi, other):
            continue
        if other.get(SAME_SITE_KEY):
            return str(other[SAME_SITE_KEY])
        return _group_id(other if role(other) == SITE else poi)
    return None
