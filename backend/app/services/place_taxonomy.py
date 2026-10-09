from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

# Section 202B.2 (Tasks 2-8, 13): the ONE classification boundary for
# provider-backed places, and the ONE user-interest -> category mapping.
#
# Audit (see docs/14 section 157): candidate quality used to be name/
# category keyword heuristics over a single flattened `category` string
# (tourism|amenity|historic value); the OSM tag set the adapter actually
# received was discarded before scoring. This module classifies from the
# STRUCTURED provider tags (`provider_tags`, retained by the OSM adapter)
# and only falls back to the flattened `category` value -- itself an OSM
# tag value -- when no tags exist (AI-grounded candidates, older cached
# results). It never infers a category from a display name and never
# invents a rating, popularity or "top" claim: a significance signal
# (wikidata/wikipedia/heritage tags) only says the provider carries
# structured landmark evidence, not that a place is "best".

# -- normalized categories -----------------------------------------------------

LANDMARK = "landmark"
HISTORIC = "historic"
MUSEUM = "museum"
ART_CULTURE = "art_culture"
ARCHITECTURE = "architecture"
PARK_NATURE = "park_nature"
VIEWPOINT = "viewpoint"
FOOD_MARKET = "food_market"
RESTAURANT = "restaurant"
NIGHTLIFE = "nightlife"
SHOPPING = "shopping"
ENTERTAINMENT = "entertainment"
RELIGIOUS = "religious"
NEIGHBORHOOD_AREA = "neighborhood_area"
WATERFRONT = "waterfront"
GENERAL_ATTRACTION = "general_attraction"
LOW_VALUE_OBJECT = "low_value_object"
UNSUITABLE = "unsuitable"

# Categories that are legitimate but must not dominate a general itinerary
# (Tasks 4/17-19). Commercial galleries and sub-exhibits are flagged
# separately (they keep their real category for interest matching).
LOW_VALUE_CATEGORIES = frozenset({LOW_VALUE_OBJECT})

# -- provider tags retained by the adapter --------------------------------------

PROVIDER_TAG_KEYS: tuple[str, ...] = (
    "tourism",
    "historic",
    "amenity",
    "leisure",
    "natural",
    "man_made",
    "building",
    "shop",
    "office",
    "place",
    "artwork_type",
    "memorial",
    "attraction",
    "zoo",
    "wikidata",
    "wikipedia",
    # Section 202C.1C: explicit architecture evidence.
    "architect",
    "building:architecture",
    # Section 203C.2B: whether the public can enter (private / no), and the
    # provider's own most specific category path when it publishes one.
    "access",
    "category_path",
)


def filter_provider_tags(tags: dict[str, Any]) -> dict[str, str]:
    """The whitelisted, string-valued subset of raw OSM tags that the
    taxonomy reads. Heritage designations (`heritage`, `heritage:*`) are
    kept as a single `heritage` key. Nothing else (names, addresses,
    phone numbers, opening hours, ratings...) is retained."""
    kept = {key: str(tags[key]) for key in PROVIDER_TAG_KEYS if tags.get(key) not in (None, "")}
    for key, value in tags.items():
        if (key == "heritage" or key.startswith("heritage:")) and value not in (None, ""):
            kept["heritage"] = str(tags.get("heritage") or value)
            break
    return kept


# -- tag value tables --------------------------------------------------------------

_TOURISM_ATTRACTION_LIKE = frozenset(
    {"attraction", "museum", "gallery", "viewpoint", "zoo", "theme_park", "aquarium"}
)
_UNSUITABLE_AMENITIES = frozenset(
    {
        "hospital", "clinic", "doctors", "dentist", "pharmacy", "veterinary", "school",
        "kindergarten", "college", "driving_school", "parking", "parking_space", "parking_entrance",
        "bank", "atm", "post_office", "police", "fire_station", "courthouse", "social_facility",
        "nursing_home", "fuel", "toilets", "waste_disposal", "car_wash", "townhall",
    }
)
_UNSUITABLE_BUILDINGS = frozenset(
    {"residential", "apartments", "house", "detached", "terrace", "industrial", "warehouse",
     "garage", "garages", "office", "hospital", "school", "kindergarten", "service"}
)
_UNSUITABLE_MAN_MADE = frozenset(
    {"reservoir_covered", "storage_tank", "wastewater_plant", "pumping_station", "works",
     "utility_pole", "silo", "water_works"}
)
_MAJOR_HISTORIC_TYPES = frozenset(
    {"castle", "fort", "palace", "archaeological_site", "ruins", "monastery", "manor",
     "city_walls", "cathedral", "temple", "tomb", "battlefield", "citywalls"}
)
_SMALL_HISTORIC_TYPES = frozenset(
    {"memorial", "wayside_cross", "wayside_shrine", "boundary_stone", "milestone", "plaque",
     "bust", "statue", "city_gate", "gate", "charcoal_pile", "fountain", "bench", "stone",
     # Section 202C.1C: single punishment/ordnance objects are small historic
     # objects too (a pillory was scheduled as "architecture" in 202C.1B).
     "pillory", "stocks", "cannon"}
)
# Section 202C.1C: a bare `historic=ruins` is the weakest "major" type -- it is
# tagged on countless unnamed remnants -- so it only counts as significance
# evidence when the provider corroborates it (wikidata / wikipedia / heritage
# or an explicit tourism designation).
_MAJOR_HISTORIC_TYPES_NEEDING_CORROBORATION = frozenset({"ruins"})
# Section 202C.1C: individual amusement rides (`attraction=*` sub-tags). A
# ride is one object inside (or outside) a park, not a landmark: 202C.1B saw
# `tourism=attraction` + `attraction=carousel` + a wikipedia tag rank as a
# primary landmark above national museums.
_RIDE_ATTRACTION_TYPES = frozenset(
    {"carousel", "roller_coaster", "big_wheel", "ferris_wheel", "train", "slide", "water_slide",
     "bumper_car", "bumper_cars", "swing_carousel", "dark_ride", "drop_tower", "pirate_ship",
     "log_flume", "river_rafting", "kiddie_ride", "summer_toboggan", "alpine_coaster", "swing"}
)
_SMALL_MEMORIAL_TYPES = frozenset(
    {"statue", "plaque", "bust", "stolperstein", "stone", "bench", "cross", "tree", "sculpture"}
)
_ARCHITECTURE_BUILDINGS = frozenset(
    {"cathedral", "church", "chapel", "basilica", "mosque", "synagogue", "temple", "skyscraper",
     "castle", "palace", "tower", "monastery"}
)
_ARCHITECTURE_MAN_MADE = frozenset({"tower", "lighthouse", "bridge"})
# Section 202C.1C: `historic=*` values that name a BUILDING or built structure
# (architecture is about the structure itself). Deliberately not every historic
# value: memorials, pillories, ruins, battlefields, archaeological sites and a
# bare `historic=yes` are history, not architecture.
_ARCHITECTURE_HISTORIC = frozenset(
    {"building", "castle", "palace", "cathedral", "church", "chapel", "monastery", "fort",
     "manor", "tower", "temple", "mosque", "synagogue", "city_walls", "citywalls", "aqueduct",
     "bridge", "lighthouse", "house"}
)

# Flattened `category` (an OSM tag VALUE) -> the tag key it came from, for
# candidates that carry no structured tags.
_CATEGORY_VALUE_TO_KEY: dict[str, str] = {}
for _key, _values in {
    "tourism": ("attraction", "museum", "gallery", "viewpoint", "artwork", "zoo", "theme_park", "aquarium", "hotel"),
    "amenity": (
        "marketplace", "theatre", "nightclub", "arts_centre", "restaurant", "cafe", "bar", "pub",
        "fast_food", "cinema", "place_of_worship", "fountain", "food_court", "casino", "planetarium",
        *_UNSUITABLE_AMENITIES,
    ),
    "historic": (
        "monument", "memorial", "castle", "fort", "ruins", "archaeological_site", "city_gate",
        "wayside_cross", "wayside_shrine", "boundary_stone", "plaque", "monastery", "manor",
        "building", "district", "church", "palace", "tomb", "battlefield",
    ),
    "leisure": ("park", "garden", "nature_reserve", "marina"),
    "natural": ("beach", "peak"),
    "man_made": ("tower", "lighthouse", "pier"),
}.items():
    for _value in _values:
        _CATEGORY_VALUE_TO_KEY.setdefault(_value, _key)


# Section 202C.1A: isolated single objects. 202C found itineraries padded
# with single heritage trees, sculptures and memorial plaques that were
# counted as "landmarks" and matched to architecture/history/outdoors. An
# "object" is one small physical thing (not an area, building, museum,
# park or viewpoint); it is recognised structurally from provider tags.
OBJECT_TREE = "tree"
OBJECT_ARTWORK = "artwork"
OBJECT_FOUNTAIN = "fountain"
OBJECT_CLOCK = "clock"
OBJECT_MEMORIAL = "memorial"
OBJECT_GATE = "gate"
OBJECT_RIDE = "amusement_ride"

# Evidence that is ABOUT something other than the single object itself. A
# heritage/wikipedia tag on a tree only describes the tree, so it never
# lifts an isolated tree; a park/garden/reserve, a museum/viewpoint/zoo
# designation or a major historic type does.
_TREE_STRONGER_TOURISM = frozenset({"museum", "viewpoint", "zoo", "theme_park", "aquarium", "gallery"})
_TREE_STRONGER_LEISURE = frozenset({"park", "garden", "nature_reserve"})


@dataclass(frozen=True)
class PlaceClassification:
    primary_category: str
    categories: tuple[str, ...]
    unsuitable_reason: str | None
    low_value: bool
    commercial_gallery: bool
    significance_signals: tuple[str, ...]
    sub_feature_kind: str | None
    # Section 202C.1A
    object_kind: str | None = None
    notable_object: bool = False

    @property
    def is_unsuitable(self) -> bool:
        return self.unsuitable_reason is not None


def _tags_from_category(category: str | None) -> dict[str, str]:
    value = (category or "").strip().lower()
    key = _CATEGORY_VALUE_TO_KEY.get(value)
    return {key: value} if key else {}


def significance_signals(tags: dict[str, str]) -> tuple[str, ...]:
    """Structured landmark/heritage evidence the provider actually carries.
    Only a signal -- never proof that a place is "best" or "top rated"."""
    signals: list[str] = []
    if tags.get("wikidata"):
        signals.append("wikidata")
    if tags.get("wikipedia"):
        signals.append("wikipedia")
    if tags.get("heritage"):
        signals.append("heritage")
    historic = tags.get("historic")
    if historic in _MAJOR_HISTORIC_TYPES and (
        historic not in _MAJOR_HISTORIC_TYPES_NEEDING_CORROBORATION or signals or tags.get("tourism")
    ):
        signals.append("major_historic_type")
    if tags.get("tourism") == "attraction" and signals:
        signals.append("tourism_attraction")
    return tuple(signals)


def object_kind_of(tags: dict[str, str]) -> str | None:
    """The single-object kind a provider place is, or None. Structural only."""
    if tags.get("natural") == "tree":
        return OBJECT_TREE
    if tags.get("tourism") == "artwork":
        return OBJECT_ARTWORK
    if tags.get("amenity") == "fountain":
        return OBJECT_FOUNTAIN
    if tags.get("amenity") == "clock":
        return OBJECT_CLOCK
    if tags.get("attraction") in _RIDE_ATTRACTION_TYPES and tags.get("tourism") not in {"theme_park", "zoo", "aquarium", "museum"}:
        return OBJECT_RIDE
    historic = tags.get("historic")
    if historic in {"city_gate", "gate"}:
        return OBJECT_GATE
    if (
        historic in _SMALL_HISTORIC_TYPES
        or (historic == "monument" and tags.get("memorial") in _SMALL_MEMORIAL_TYPES)
        or tags.get("memorial") in _SMALL_MEMORIAL_TYPES
    ):
        return OBJECT_MEMORIAL
    return None


def classify_place(provider_tags: dict[str, Any] | None, category: str | None = None) -> PlaceClassification:
    tags: dict[str, str] = {k: str(v) for k, v in (provider_tags or {}).items() if v not in (None, "")}
    structured = bool(tags)
    if not structured:
        tags = _tags_from_category(category)

    signals = significance_signals(tags) if structured else ()
    tourism = tags.get("tourism")
    amenity = tags.get("amenity")
    historic = tags.get("historic")

    # A bare `wikidata` tag is carried by countless small local features (most
    # statues and memorials in a dense city), so it is only a WEAK signal:
    # exemptions below need strong evidence -- a wikipedia article, a heritage
    # designation, or a major historic type.
    strong = bool({"wikipedia", "heritage", "major_historic_type"} & set(signals))

    # -- unsuitable (Task 3): functional place types, structurally --------------
    exempt = (tourism in _TOURISM_ATTRACTION_LIKE) or strong
    unsuitable_reason: str | None = None
    if not exempt:
        if amenity in _UNSUITABLE_AMENITIES:
            unsuitable_reason = f"amenity={amenity}"
        elif tags.get("office"):
            unsuitable_reason = "office"
        elif tags.get("building") in _UNSUITABLE_BUILDINGS:
            unsuitable_reason = f"building={tags['building']}"
        elif tags.get("man_made") in _UNSUITABLE_MAN_MADE:
            unsuitable_reason = f"man_made={tags['man_made']}"

    # -- low-value micro objects (Task 4) ------------------------------------------
    obj = object_kind_of(tags)
    low_value = False
    notable_object = False
    if obj == OBJECT_TREE:
        # An isolated tree is low value even with a heritage/wikipedia tag
        # (those describe the tree itself) unless the provider ALSO marks
        # it as something bigger.
        stronger = (
            tourism in _TREE_STRONGER_TOURISM
            or tags.get("leisure") in _TREE_STRONGER_LEISURE
            or historic in _MAJOR_HISTORIC_TYPES
        )
        low_value = not stronger
    elif obj is not None:
        if not strong:
            low_value = True
        else:
            # Documented significant object (wikipedia / heritage / major
            # type): kept, but never behaves like a general landmark unless
            # the provider itself designates it a tourist attraction.
            # A single amusement ride stays a notable object even when the
            # provider tags it `tourism=attraction` (Section 202C.1C).
            notable_object = tourism != "attraction" or obj == OBJECT_RIDE

    commercial_gallery = (tourism == "gallery" or tags.get("shop") == "art") and not strong

    sub_feature_kind: str | None = None
    if tags.get("zoo") or tags.get("attraction") in {"animal", "exhibit"}:
        sub_feature_kind = "zoo"

    # -- categories, most specific first -----------------------------------------------
    categories: list[str] = []

    def add(category_name: str) -> None:
        if category_name not in categories:
            categories.append(category_name)

    if unsuitable_reason:
        add(UNSUITABLE)
    if low_value:
        add(LOW_VALUE_OBJECT)
    # A landmark needs richer structured evidence than a bare `wikidata`
    # tag (which small local features also carry): a wikipedia article, a
    # heritage designation, or a major historic type.
    if (
        ({"wikipedia", "heritage", "major_historic_type"} & set(signals))
        and (tourism == "attraction" or historic or tags.get("man_made"))
        and not low_value
        and not notable_object
    ):
        add(LANDMARK)
    if tourism == "museum":
        add(MUSEUM)
    if tourism == "gallery" or tags.get("shop") == "art" or amenity == "arts_centre":
        add(ART_CULTURE)
    if tourism == "artwork":
        # Artworks honestly serve an "art" request (even a plain one); the
        # low-value/notable flags keep them from dominating general trips.
        add(ART_CULTURE)
    if historic and not low_value:
        add(HISTORIC)
    # Section 202C.1C: architecture needs evidence about a BUILT STRUCTURE --
    # a building/structure type, a building-type historic value, a named
    # architect or architectural style, or a documented place of worship.
    # Never a single object, and never `tourism=attraction` / `historic=yes`
    # / a memorial on their own.
    if obj is None and (
        tags.get("building") in _ARCHITECTURE_BUILDINGS
        or tags.get("man_made") in _ARCHITECTURE_MAN_MADE
        or historic in _ARCHITECTURE_HISTORIC
        or tags.get("architect")
        or tags.get("building:architecture")
        or (amenity == "place_of_worship" and strong)
    ):
        add(ARCHITECTURE)
    if obj == OBJECT_RIDE:
        add(ENTERTAINMENT)
    if amenity == "place_of_worship":
        add(RELIGIOUS)
    if tags.get("leisure") in {"park", "garden", "nature_reserve"} or tags.get("natural") in {"beach", "peak"}:
        add(PARK_NATURE)
    if tourism == "viewpoint":
        add(VIEWPOINT)
    if tags.get("leisure") == "marina" or tags.get("man_made") == "pier" or tags.get("natural") == "beach":
        add(WATERFRONT)
    if amenity in {"marketplace", "food_court"}:
        add(FOOD_MARKET)
    if amenity in {"restaurant", "cafe", "fast_food", "food_court"}:
        add(RESTAURANT)
    if amenity in {"bar", "pub", "nightclub", "casino", "biergarten"}:
        add(NIGHTLIFE)
    if tourism in {"zoo", "theme_park", "aquarium"} or amenity in {"theatre", "cinema", "planetarium", "arts_centre"}:
        add(ENTERTAINMENT)
    if tags.get("shop"):
        add(SHOPPING)
    if tags.get("place") in {"neighbourhood", "suburb", "quarter", "city_block"}:
        add(NEIGHBORHOOD_AREA)
    if tourism == "attraction":
        add(GENERAL_ATTRACTION)
    if not categories:
        add(GENERAL_ATTRACTION)

    return PlaceClassification(
        primary_category=categories[0],
        categories=tuple(categories),
        unsuitable_reason=unsuitable_reason,
        low_value=low_value,
        commercial_gallery=commercial_gallery,
        significance_signals=signals,
        sub_feature_kind=sub_feature_kind,
        object_kind=obj,
        notable_object=notable_object,
    )


def classify_candidate(candidate: dict[str, Any]) -> PlaceClassification:
    """Classify a candidate dict (`destination_context.candidate_pois`
    shape, `NormalizedPlace.model_dump()`)."""
    return classify_place(candidate.get("provider_tags"), candidate.get("category"))


# -- interests ------------------------------------------------------------------------

# Audit of the real interest vocabulary: `TripRequest.interests` is free
# text ("museums, hiking, local food", "food", "history", "architecture",
# "nightlife", "outdoors", "parks"). Q1 added the `waterfront` family.
# A term maps only when one of its words is a recognised synonym; an
# unrecognised term maps to nothing (no claim is made about it).
_INTEREST_WORDS: dict[str, frozenset[str]] = {
    "food": frozenset({"food", "foodie", "culinary", "cuisine", "eat", "eating", "dining", "restaurant", "gastronomy", "cooking"}),
    "nightlife": frozenset({"nightlife", "bar", "pub", "club", "cocktail", "nightclub"}),
    "outdoors": frozenset({"outdoors", "outdoor", "nature", "park", "hike", "hiking", "trail", "beach", "garden", "scenic", "walk"}),
    "history": frozenset({"history", "historic", "historical", "heritage", "ancient", "castle", "monument"}),
    "museum": frozenset({"museum"}),
    "art": frozenset({"art", "gallery", "painting", "sculpture"}),
    "architecture": frozenset({"architecture", "architectural", "building", "skyscraper", "cathedral"}),
    # Section 203C.2B: an explicit request for markets is a shopping request
    # (generic words only -- no place or city name).
    "shopping": frozenset({"shopping", "shop", "boutique", "market", "bazaar", "souk"}),
    # Q1: one canonical interest for the waterfront family (generic words
    # only). It is its own interest, not a synonym of `outdoors`, so a request
    # for piers or a harbour can only be met by a place the provider describes
    # as waterfront -- never by a park.
    "waterfront": frozenset({"waterfront", "pier", "riverfront", "harbor", "harbour", "promenade"}),
}

INTEREST_CATEGORIES: dict[str, frozenset[str]] = {
    "food": frozenset({FOOD_MARKET, RESTAURANT}),
    # Section 202B.3 (Task 38): nightlife is claimed only for places whose
    # provider tags say nightlife (bar/pub/nightclub/casino/biergarten).
    # Theatres, cinemas, zoos and arts centres are `entertainment` -- a
    # different, honestly labelled category -- and no longer count as it.
    "nightlife": frozenset({NIGHTLIFE}),
    "outdoors": frozenset({PARK_NATURE, WATERFRONT, VIEWPOINT}),
    "history": frozenset({HISTORIC, LANDMARK, MUSEUM}),
    "museum": frozenset({MUSEUM}),
    "art": frozenset({ART_CULTURE, MUSEUM}),
    # Section 202C.1C: only places the provider describes as a built
    # structure (see the ARCHITECTURE rule in `classify_place`). LANDMARK /
    # HISTORIC / RELIGIOUS no longer imply architecture: a pillory, a
    # memorial or a restaurant in a `historic=yes` building is not it.
    "architecture": frozenset({ARCHITECTURE}),
    "shopping": frozenset({SHOPPING, FOOD_MARKET}),
    # Q1: only the provider-derived WATERFRONT category (see `classify_place`).
    # A waterfront place still serves `outdoors` above; an ordinary park,
    # garden or viewpoint does not serve `waterfront`.
    "waterfront": frozenset({WATERFRONT}),
}


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def canonical_interests(terms: Iterable[str]) -> list[str]:
    """Distinct canonical interest keys, in first-seen order, for the
    user's free-text interest terms. Unrecognised terms yield nothing."""
    result: list[str] = []
    for term in terms:
        for word in re.findall(r"[a-z]+", (term or "").lower()):
            stem = _stem(word)
            for key, words in _INTEREST_WORDS.items():
                if (word in words or stem in words) and key not in result:
                    result.append(key)
    return result


def matched_interests(classification: PlaceClassification, interests: Iterable[str]) -> list[str]:
    """Canonical interests a candidate serves, supported ONLY by its
    provider-derived categories. Unsuitable places match nothing."""
    if classification.is_unsuitable:
        return []
    categories = set(classification.categories)
    matched = [key for key in interests if categories & INTEREST_CATEGORIES.get(key, frozenset())]
    if classification.object_kind is not None:
        # Section 202C.1A: a single object never satisfies architecture or
        # outdoors merely by being tagged natural/historic; only "history"
        # (a documented memorial/monument) and "art" (an artwork) apply.
        matched = [key for key in matched if key in {"history", "art"}]
    return matched
