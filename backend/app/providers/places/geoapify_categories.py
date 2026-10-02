"""Geoapify Places category configuration (Section 203C.2B).

Every identifier this app sends to Geoapify is listed here and nowhere
else, and must exist in `VERIFIED_TAXONOMY` -- a snapshot of the official
"Supported categories" list (apidocs.geoapify.com/docs/places), checked on
2026-10-01. A contract test fails on any configured identifier that is not
in the snapshot. Nothing here is city-specific.

Request grouping: categories are combined into one request wherever that
does not let one family crowd out another. `tourism.attraction` is kept
apart and small because Geoapify files artwork, clocks and fountains under
it; `tourism.sights` is kept apart because it is the largest family.

Pricing (geoapify.com/pricing-details, same date): a Places request costs
1 credit up to 20 places, plus 1 credit per further 20 places returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VERIFIED_TAXONOMY: frozenset[str] = frozenset(
    """
    tourism.attraction tourism.attraction.artwork tourism.attraction.artwork.mural
    tourism.attraction.artwork.sculpture tourism.attraction.artwork.statue tourism.attraction.clock
    tourism.attraction.fountain tourism.attraction.viewpoint tourism.information tourism.information.map
    tourism.information.office tourism.information.ranger_station tourism.sights
    tourism.sights.archaeological_site tourism.sights.battlefield tourism.sights.building
    tourism.sights.bridge tourism.sights.castle tourism.sights.city_gate tourism.sights.city_hall
    tourism.sights.conference_centre tourism.sights.fort tourism.sights.lighthouse tourism.sights.manor
    tourism.sights.memorial tourism.sights.memorial.aircraft tourism.sights.memorial.boundary_stone
    tourism.sights.memorial.locomotive tourism.sights.memorial.milestone tourism.sights.memorial.monument
    tourism.sights.memorial.necropolis tourism.sights.memorial.pillory tourism.sights.memorial.railway_car
    tourism.sights.memorial.ship tourism.sights.memorial.tank tourism.sights.memorial.tomb
    tourism.sights.memorial.tumulus tourism.sights.memorial.wayside_cross tourism.sights.mine
    tourism.sights.monastery tourism.sights.place_of_worship tourism.sights.place_of_worship.cathedral
    tourism.sights.place_of_worship.chapel tourism.sights.place_of_worship.church
    tourism.sights.place_of_worship.mosque tourism.sights.place_of_worship.shrine
    tourism.sights.place_of_worship.synagogue tourism.sights.place_of_worship.temple tourism.sights.ruines
    tourism.sights.tower tourism.sights.windmill tourism.sights.wreck
    entertainment.activity_park entertainment.activity_park.climbing entertainment.activity_park.trampoline
    entertainment.amusement_arcade entertainment.aquarium entertainment.bowling_alley entertainment.cinema
    entertainment.culture entertainment.culture.arts_centre entertainment.culture.gallery
    entertainment.culture.theatre entertainment.escape_game entertainment.flying_fox
    entertainment.miniature_golf entertainment.museum entertainment.planetarium entertainment.theme_park
    entertainment.water_park entertainment.zoo
    leisure.park leisure.park.garden leisure.park.nature_reserve leisure.picnic leisure.picnic.bbq
    leisure.picnic.picnic_site leisure.picnic.picnic_table leisure.playground leisure.spa
    leisure.spa.public_bath leisure.spa.sauna
    heritage.unesco national_park
    natural.coastal natural.desert natural.forest natural.heath_moor natural.mountain
    natural.mountain.cave_entrance natural.mountain.cliff natural.mountain.fell natural.mountain.glacier
    natural.mountain.hill natural.mountain.peak natural.mountain.rock natural.mountain.volcano
    natural.protected_area natural.sand natural.sand.dune natural.water natural.water.bay
    natural.water.geyser natural.water.hot_spring natural.water.reef natural.water.river_system
    natural.water.sea natural.water.spring natural.water.whitewater natural.wetland
    commercial.marketplace commercial.shopping_mall commercial.kiosk
    catering.restaurant catering.cafe
    accommodation accommodation.apartment accommodation.chalet accommodation.guest_house
    accommodation.hostel accommodation.hotel accommodation.hut accommodation.motel
    religion.place_of_worship religion.place_of_worship.buddhism religion.place_of_worship.christianity
    religion.place_of_worship.hinduism religion.place_of_worship.islam religion.place_of_worship.judaism
    religion.place_of_worship.multifaith religion.place_of_worship.shinto religion.place_of_worship.sikhism
    building.historic building.tourism
    man_made.breakwater man_made.bridge man_made.lighthouse man_made.pier man_made.tower
    man_made.water_tower man_made.watermill man_made.windmill
    beach.beach_resort
    """.split()
)


@dataclass(frozen=True)
class CategoryGroup:
    """One Places request: `categories` sent comma-separated, `share` of the
    attraction pool this group may fill."""

    key: str
    categories: tuple[str, ...]
    share: float


ATTRACTION_GROUPS: tuple[CategoryGroup, ...] = (
    CategoryGroup("sights", ("tourism.sights",), 0.35),
    CategoryGroup("attractions", ("tourism.attraction",), 0.15),
    CategoryGroup(
        "culture",
        (
            "entertainment.museum",
            "entertainment.culture",
            "entertainment.zoo",
            "entertainment.aquarium",
            "entertainment.theme_park",
            "entertainment.planetarium",
        ),
        0.30,
    ),
    CategoryGroup(
        "outdoors_heritage_markets",
        ("leisure.park", "heritage.unesco", "national_park", "commercial.marketplace"),
        0.20,
    ),
)
FOOD_GROUP = CategoryGroup("food", ("catering.restaurant", "catering.cafe"), 1.0)
ACCOMMODATION_GROUP = CategoryGroup(
    "accommodation",
    ("accommodation.hotel", "accommodation.guest_house", "accommodation.hostel", "accommodation.apartment"),
    1.0,
)

ALL_GROUPS: tuple[CategoryGroup, ...] = (*ATTRACTION_GROUPS, FOOD_GROUP, ACCOMMODATION_GROUP)


def configured_categories() -> frozenset[str]:
    return frozenset(category for group in ALL_GROUPS for category in group.categories)


# -- Geoapify category -> the taxonomy's tag vocabulary ---------------------------
#
# `app.services.place_taxonomy` classifies places from OpenStreetMap-style
# key/value tags. Geoapify publishes its classification as dotted category
# paths; each path below is translated to the equivalent tag. This is a
# rename of the PROVIDER'S OWN classification -- it never adds a fact the
# provider did not state, and a path with no entry contributes nothing.

_EXACT: dict[str, dict[str, str]] = {
    "tourism.attraction": {"tourism": "attraction"},
    "tourism.attraction.artwork": {"tourism": "artwork"},
    "tourism.attraction.artwork.mural": {"tourism": "artwork", "artwork_type": "mural"},
    "tourism.attraction.artwork.sculpture": {"tourism": "artwork", "artwork_type": "sculpture"},
    "tourism.attraction.artwork.statue": {"tourism": "artwork", "artwork_type": "statue"},
    "tourism.attraction.clock": {"amenity": "clock"},
    "tourism.attraction.fountain": {"amenity": "fountain"},
    "tourism.attraction.viewpoint": {"tourism": "viewpoint"},
    "tourism.sights.archaeological_site": {"historic": "archaeological_site"},
    "tourism.sights.battlefield": {"historic": "battlefield"},
    "tourism.sights.bridge": {"man_made": "bridge"},
    "tourism.sights.castle": {"historic": "castle"},
    "tourism.sights.city_gate": {"historic": "city_gate"},
    "tourism.sights.city_hall": {"amenity": "townhall"},
    "tourism.sights.fort": {"historic": "fort"},
    "tourism.sights.lighthouse": {"man_made": "lighthouse"},
    "tourism.sights.manor": {"historic": "manor"},
    "tourism.sights.memorial": {"historic": "memorial"},
    "tourism.sights.memorial.monument": {"historic": "monument"},
    "tourism.sights.memorial.tomb": {"historic": "tomb"},
    "tourism.sights.memorial.boundary_stone": {"historic": "boundary_stone"},
    "tourism.sights.memorial.milestone": {"historic": "milestone"},
    "tourism.sights.memorial.pillory": {"historic": "pillory"},
    "tourism.sights.memorial.wayside_cross": {"historic": "wayside_cross"},
    "tourism.sights.monastery": {"historic": "monastery"},
    "tourism.sights.place_of_worship": {"amenity": "place_of_worship"},
    "tourism.sights.place_of_worship.cathedral": {"amenity": "place_of_worship", "building": "cathedral"},
    "tourism.sights.place_of_worship.chapel": {"amenity": "place_of_worship", "building": "chapel"},
    "tourism.sights.place_of_worship.church": {"amenity": "place_of_worship", "building": "church"},
    "tourism.sights.place_of_worship.mosque": {"amenity": "place_of_worship", "building": "mosque"},
    "tourism.sights.place_of_worship.synagogue": {"amenity": "place_of_worship", "building": "synagogue"},
    "tourism.sights.place_of_worship.temple": {"amenity": "place_of_worship", "building": "temple"},
    "tourism.sights.ruines": {"historic": "ruins"},
    "tourism.sights.tower": {"man_made": "tower"},
    "entertainment.museum": {"tourism": "museum"},
    "entertainment.culture.arts_centre": {"amenity": "arts_centre"},
    "entertainment.culture.gallery": {"tourism": "gallery"},
    "entertainment.culture.theatre": {"amenity": "theatre"},
    "entertainment.zoo": {"tourism": "zoo"},
    "entertainment.aquarium": {"tourism": "aquarium"},
    "entertainment.theme_park": {"tourism": "theme_park"},
    "entertainment.planetarium": {"amenity": "planetarium"},
    "leisure.park": {"leisure": "park"},
    "leisure.park.garden": {"leisure": "garden"},
    "leisure.park.nature_reserve": {"leisure": "nature_reserve"},
    "national_park": {"leisure": "nature_reserve"},
    "commercial.marketplace": {"amenity": "marketplace"},
    "catering.restaurant": {"amenity": "restaurant"},
    "catering.cafe": {"amenity": "cafe"},
    "man_made.tower": {"man_made": "tower"},
    "man_made.lighthouse": {"man_made": "lighthouse"},
    "man_made.bridge": {"man_made": "bridge"},
    "man_made.pier": {"man_made": "pier"},
}

# Categories that describe WHAT the place is (as opposed to conditions such
# as `fee`, `wheelchair.yes`, `internet_access`).
_KIND_ROOTS = (
    "tourism", "entertainment", "leisure", "heritage", "national_park", "natural", "commercial",
    "catering", "accommodation", "religion", "man_made", "beach",
)


def most_specific_category(categories: list[str]) -> str | None:
    """The deepest kind-describing category path (ties: first as returned)."""
    kinds = [c for c in categories if c.split(".", 1)[0] in _KIND_ROOTS]
    return max(kinds, key=lambda c: c.count("."), default=None)


def taxonomy_tags_from_categories(categories: list[str]) -> dict[str, str]:
    """Geoapify's own classification, renamed into the taxonomy's tag
    vocabulary. More specific paths win; access and heritage markers are
    carried through as `access` / `heritage`."""
    tags: dict[str, str] = {}
    for path in sorted(categories, key=lambda c: c.count(".")):
        for key, value in _EXACT.get(path, {}).items():
            tags[key] = value  # deeper paths are applied later and win
        if path.startswith("catering.restaurant"):
            tags["amenity"] = "restaurant"
        elif path.startswith("catering.cafe"):
            tags["amenity"] = "cafe"
        elif path.startswith("accommodation."):
            tags.setdefault("tourism", path.split(".")[1])
    if any(c == "heritage" or c.startswith("heritage.") for c in categories):
        tags["heritage"] = "yes"
    if "no_access" in categories:
        tags["access"] = "no"
    elif "access_limited.private" in categories:
        tags["access"] = "private"
    specific = most_specific_category(categories)
    if specific:
        tags["category_path"] = specific
    return tags


def evidence_tags(properties: dict[str, Any]) -> dict[str, str]:
    """Wikipedia/Wikidata references the response itself carries."""
    tags: dict[str, str] = {}
    wiki = properties.get("wiki_and_media")
    if isinstance(wiki, dict):
        for key in ("wikidata", "wikipedia"):
            if isinstance(wiki.get(key), str) and wiki[key]:
                tags[key] = wiki[key]
    return tags
