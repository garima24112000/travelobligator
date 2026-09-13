from __future__ import annotations

from app.models.scraping import ScrapingSourcePolicy, ScrapingSourceRegistry

# Step 185B: populates a real, named `ScrapingSourceRegistry` for every
# commercial accommodation/flight/ratings source this app tracks --
# closing the gap Step 185A's audit found (`app.models.scraping.
# default_scraping_source_registry` exists but is empty, and nothing
# constructs a real `ScrapingSourcePolicy` for any of these sites).
#
# This module is purely descriptive/documentary as of Step 185B:
# - No live fetcher exists anywhere in this codebase. No entry here is
#   ever consulted by a network call.
# - Every entry's `enabled` is `False` -- "all sources represented" is
#   this step's goal, never "all sources active for live fetch." A
#   future live scraper (none exists) would need to consult
#   `active_sources()`, which stays empty for this entire registry until
#   a specific source is both explicitly flipped to `enabled=True` *and*
#   passes `ScrapingSourcePolicy`'s own safety validation -- which
#   structurally rejects `enabled=True` for anything not
#   `approved_for_personal_use=True` (see `is_unsafe`).
# - The six sources CLAUDE.md's Core Rules name as restricted from
#   scraping (Airbnb, Booking.com, Expedia, Vrbo, Tripadvisor, Google
#   Flights) are recorded with `approved_for_personal_use=False` for
#   that reason alone -- this is a project-policy fact, not a claim
#   about any of those sites' actual bot-detection/CAPTCHA/login
#   behavior, which this module never asserts one way or the other
#   since nobody has verified it. `requires_login`/`paywalled`/
#   `captcha_expected` are left at their honest, unverified default
#   (`False`) for every entry in this module -- `approved_for_personal_use
#   =False` alone is what keeps every one of them `is_unsafe`.
# - The three non-restricted sources with no live-fetch path yet
#   (Hotelbeds, Hostelworld, Skyscanner) are recorded the same way: no
#   human has reviewed their `robots.txt`/ToS for a specific page, so
#   `approved_for_personal_use` stays `False` until that review happens
#   and a real decision is recorded here -- this module does not
#   perform, or claim to have performed, that review.
# - `kiwi_manual` is a deliberately distinct entry from the real, live
#   `FLIGHT_PROVIDER=kiwi_mcp`/`KIWI_MCP_ENABLED` integration
#   (`app/providers/flights/kiwi_mcp_adapter.py`) -- this entry only ever
#   describes a manually-labeled *local file*, never Kiwi MCP data.
# - `google_places_ratings` is included per explicit request as a
#   tracked *future, official-API* candidate, not a scraping target --
#   Google Places is never scraped by this app. It is recorded here with
#   the same `approved_for_personal_use=False`/`enabled=False` shape as
#   every other not-yet-implemented source purely for registry
#   completeness/consistency, not because it is unsafe in the same way
#   the restricted six are.
#
# Every source's real, honest MVP path today is the pre-existing generic
# manual/local HTML adapters (`ScrapedAccommodationProvider`/
# `ScrapedLocalFlightProvider`) -- this registry is consulted by those
# adapters (Step 185B) only for *display naming*, never to gate whether
# the adapter may read a local file the operator already possesses (see
# `app.providers.accommodation.scraped_adapter`/`app.providers.flights.
# scraped_adapter` for why that's a categorically different, always-safe
# operation from "is this named website approved for live scraping").
#
# No source-specific *parsing* exists yet -- every source here still
# produces offers via the one existing generic HTML micro-format parser
# (Step 185C/185D's job to add real per-source parser modules).

_RATE_LIMIT_SECONDS = 10  # Mirrors Settings.scraping_default_rate_limit_seconds's
# own default value -- duplicated as a plain constant (not imported from
# Settings) so this module never depends on app.core.config/a real .env
# and stays safe to import at any time, including at test collection.

_RESTRICTED_SOURCE_NOTE_TEMPLATE = (
    "Restricted from live scraping per project policy (CLAUDE.md Core Rules: "
    '"Do not scrape restricted providers"). Manual/local HTML supply only, '
    "via {config_var}={label} -- never a live fetch of {display_name}'s own "
    "site. {future_path}"
)

_UNREVIEWED_SOURCE_NOTE_TEMPLATE = (
    "Not restricted by project policy, but no human has reviewed "
    "{display_name}'s robots.txt/ToS for a specific page yet, so "
    "approved_for_personal_use stays False and no live fetch is "
    "implemented or approved. Manual/local HTML supply is the practical "
    "path today, via {config_var}={label}. {future_path}"
)


def _restricted_accommodation_policy(
    source_id: str,
    display_name: str,
    base_url: str,
    label: str,
    future_path: str,
) -> ScrapingSourcePolicy:
    return ScrapingSourcePolicy(
        source_id=source_id,
        source_name=display_name,
        base_url=base_url,
        allows_lodging=True,
        requires_login=False,
        paywalled=False,
        captcha_expected=False,
        approved_for_personal_use=False,
        enabled=False,
        rate_limit_seconds=_RATE_LIMIT_SECONDS,
        notes=_RESTRICTED_SOURCE_NOTE_TEMPLATE.format(
            config_var="ACCOMMODATION_MANUAL_HTML_SOURCE",
            label=label,
            display_name=display_name,
            future_path=future_path,
        ),
    )


def _unreviewed_accommodation_policy(
    source_id: str,
    display_name: str,
    base_url: str,
    label: str,
    future_path: str,
) -> ScrapingSourcePolicy:
    return ScrapingSourcePolicy(
        source_id=source_id,
        source_name=display_name,
        base_url=base_url,
        allows_lodging=True,
        requires_login=False,
        paywalled=False,
        captcha_expected=False,
        approved_for_personal_use=False,
        enabled=False,
        rate_limit_seconds=_RATE_LIMIT_SECONDS,
        notes=_UNREVIEWED_SOURCE_NOTE_TEMPLATE.format(
            config_var="ACCOMMODATION_MANUAL_HTML_SOURCE",
            label=label,
            display_name=display_name,
            future_path=future_path,
        ),
    )


def _restricted_flight_policy(
    source_id: str,
    display_name: str,
    base_url: str,
    label: str,
    future_path: str,
) -> ScrapingSourcePolicy:
    return ScrapingSourcePolicy(
        source_id=source_id,
        source_name=display_name,
        base_url=base_url,
        allows_flights=True,
        requires_login=False,
        paywalled=False,
        captcha_expected=False,
        approved_for_personal_use=False,
        enabled=False,
        rate_limit_seconds=_RATE_LIMIT_SECONDS,
        notes=_RESTRICTED_SOURCE_NOTE_TEMPLATE.format(
            config_var="FLIGHT_MANUAL_HTML_SOURCE",
            label=label,
            display_name=display_name,
            future_path=future_path,
        ),
    )


def _unreviewed_flight_policy(
    source_id: str,
    display_name: str,
    base_url: str,
    label: str,
    future_path: str,
) -> ScrapingSourcePolicy:
    return ScrapingSourcePolicy(
        source_id=source_id,
        source_name=display_name,
        base_url=base_url,
        allows_flights=True,
        requires_login=False,
        paywalled=False,
        captcha_expected=False,
        approved_for_personal_use=False,
        enabled=False,
        rate_limit_seconds=_RATE_LIMIT_SECONDS,
        notes=_UNREVIEWED_SOURCE_NOTE_TEMPLATE.format(
            config_var="FLIGHT_MANUAL_HTML_SOURCE",
            label=label,
            display_name=display_name,
            future_path=future_path,
        ),
    )


# --- Accommodation sources -------------------------------------------------

_BOOKING = _restricted_accommodation_policy(
    source_id="booking",
    display_name="Booking.com",
    base_url="https://www.booking.com",
    label="booking",
    future_path=(
        "A real Booking Demand API partner integration (see "
        "BOOKING_DEMAND_API_KEY in config.py) would be an official API "
        "call, not scraping, and remains a possible separate future path "
        "if partner access is ever obtained."
    ),
)

_EXPEDIA = _restricted_accommodation_policy(
    source_id="expedia",
    display_name="Expedia",
    base_url="https://www.expedia.com",
    label="expedia",
    future_path=(
        "A real Expedia Rapid API partner integration (see "
        "EXPEDIA_RAPID_API_KEY in config.py) would be an official API "
        "call, not scraping, and remains a possible separate future path "
        "if partner access is ever obtained."
    ),
)

_VRBO = _restricted_accommodation_policy(
    source_id="vrbo",
    display_name="Vrbo",
    base_url="https://www.vrbo.com",
    label="vrbo",
    future_path=(
        "A real Vrbo partner API integration (see VRBO_PARTNER_API_KEY "
        "in config.py, Vrbo shares Expedia Group's partner ecosystem) "
        "would be an official API call, not scraping, and remains a "
        "possible separate future path if partner access is ever "
        "obtained."
    ),
)

_AIRBNB = _restricted_accommodation_policy(
    source_id="airbnb",
    display_name="Airbnb",
    base_url="https://www.airbnb.com",
    label="airbnb",
    future_path=(
        "Airbnb's official API is invitation-only/property-management-"
        "focused, not a general search API for a small app -- a real "
        "Airbnb partner integration (see AIRBNB_PARTNER_API_KEY in "
        "config.py) is tracked for completeness but has no realistic "
        "near-term path today."
    ),
)

_HOTELBEDS = _unreviewed_accommodation_policy(
    source_id="hotelbeds",
    display_name="Hotelbeds",
    base_url="https://www.hotelbeds.com",
    label="hotelbeds",
    future_path=(
        "Hotelbeds runs an open developer/partner API signup (see "
        "HOTELBEDS_API_KEY/HOTELBEDS_SECRET in config.py) that is the "
        "realistic, honest official path -- Hotelbeds is a B2B wholesale "
        "platform, not a consumer search site, so a live scrape was "
        "never really the right target here anyway."
    ),
)

_HOSTELWORLD = _unreviewed_accommodation_policy(
    source_id="hostelworld",
    display_name="Hostelworld",
    base_url="https://www.hostelworld.com",
    label="hostelworld",
    future_path=(
        "A real Hostelworld affiliate/API integration (see "
        "HOSTELWORLD_API_KEY in config.py) remains a possible future "
        "path if that access is ever obtained."
    ),
)

# --- Flight sources ----------------------------------------------------

_SKYSCANNER = _unreviewed_flight_policy(
    source_id="skyscanner",
    display_name="Skyscanner",
    base_url="https://www.skyscanner.com",
    label="skyscanner",
    future_path=(
        "Skyscanner's public partner API program is largely closed to "
        "new developers today; a real integration (see "
        "SKYSCANNER_API_KEY in config.py) remains a possible future path "
        "if that access is ever obtained."
    ),
)

_GOOGLE_FLIGHTS = _restricted_flight_policy(
    source_id="google_flights",
    display_name="Google Flights",
    base_url="https://www.google.com/travel/flights",
    label="google_flights",
    future_path=(
        "Google does not offer a general-developer Google Flights API "
        "today (Google's older QPX Express API was discontinued) -- no "
        "realistic official-API future path is tracked for this source "
        "beyond this manual/local fallback."
    ),
)

_KIWI_MANUAL = ScrapingSourcePolicy(
    source_id="kiwi_manual",
    source_name="Kiwi",
    base_url="https://www.kiwi.com",
    allows_flights=True,
    requires_login=False,
    paywalled=False,
    captcha_expected=False,
    approved_for_personal_use=False,
    enabled=False,
    rate_limit_seconds=_RATE_LIMIT_SECONDS,
    notes=(
        "Manual/local HTML labeling only, via FLIGHT_MANUAL_HTML_SOURCE="
        "kiwi -- deliberately distinct from the separate, real, live "
        "FLIGHT_PROVIDER=kiwi_mcp/KIWI_MCP_ENABLED integration "
        "(app/providers/flights/kiwi_mcp_adapter.py). This entry never "
        "enables, represents, or implies live Kiwi MCP data; a manually-"
        "labeled offer's `provider` field always starts with "
        "\"scraped:\", never \"kiwi_mcp\"."
    ),
)

# --- Ratings/review sources ----------------------------------------------

_TRIPADVISOR = ScrapingSourcePolicy(
    source_id="tripadvisor",
    source_name="Tripadvisor",
    base_url="https://www.tripadvisor.com",
    allows_reviews=True,
    requires_login=False,
    paywalled=False,
    captcha_expected=False,
    approved_for_personal_use=False,
    enabled=False,
    rate_limit_seconds=_RATE_LIMIT_SECONDS,
    notes=(
        "Restricted from live scraping per project policy (CLAUDE.md "
        'Core Rules: "Do not scrape restricted providers"). No manual/'
        "local ratings-HTML adapter exists yet (Step 185E's job); "
        "HOTEL_RATINGS_MANUAL_HTML_SOURCE=tripadvisor is a config-only "
        "placeholder until then. The real, honest long-term path here is "
        "the official Tripadvisor Content API (see TRIPADVISOR_API_KEY/"
        "TRIPADVISOR_API_BASE_URL in config.py), which would be an "
        "official API call, not scraping."
    ),
)

_GOOGLE_PLACES_RATINGS = ScrapingSourcePolicy(
    source_id="google_places_ratings",
    source_name="Google Places",
    base_url="https://developers.google.com/maps/documentation/places/web-service",
    allows_reviews=True,
    requires_login=False,
    paywalled=False,
    captcha_expected=False,
    approved_for_personal_use=False,
    enabled=False,
    rate_limit_seconds=_RATE_LIMIT_SECONDS,
    notes=(
        "Tracked as a future *official-API* ratings candidate, not a "
        "scraping target -- Google Places is never scraped anywhere in "
        "this app. If ever implemented, this would call the real Google "
        "Places API (see the existing GOOGLE_PLACES_API_KEY in "
        "config.py, already used elsewhere for open-data POI candidates, "
        "not ratings) rather than reading a local file. Recorded here "
        "with the same disabled/not-yet-approved shape as every other "
        "not-yet-implemented source purely for registry consistency, not "
        "because Google Places carries the same restriction as the "
        "policy-restricted six above."
    ),
)

# --- Generic (no real site claimed) fallbacks -----------------------------
#
# The "generic" label (the default for every *_MANUAL_HTML_SOURCE
# setting) means "an anonymous, locally-supplied HTML file -- no real
# commercial site's identity is being claimed at all." There is no real
# website's safety to approve or restrict here, so these three are the
# only entries in this registry that could ever honestly be
# `approved_for_personal_use=True` -- they still default `enabled=False`
# simply because no live fetcher of any kind exists yet in this
# codebase, not because anything about them is unsafe.

_GENERIC_ACCOMMODATION = ScrapingSourcePolicy(
    source_id="generic_accommodation",
    source_name="Manual local scraped accommodation source",
    base_url="file://local-manual-accommodation",
    allows_lodging=True,
    approved_for_personal_use=True,
    enabled=False,
    rate_limit_seconds=_RATE_LIMIT_SECONDS,
    notes=(
        "No real commercial site is claimed by the 'generic' label -- "
        "this describes only an anonymous, locally-supplied HTML file. "
        "`enabled` stays False purely because no live fetcher exists yet "
        "in this codebase, not because anything here is unsafe."
    ),
)

_GENERIC_FLIGHT = ScrapingSourcePolicy(
    source_id="generic_flight",
    source_name="Manual local scraped flight source",
    base_url="file://local-manual-flight",
    allows_flights=True,
    approved_for_personal_use=True,
    enabled=False,
    rate_limit_seconds=_RATE_LIMIT_SECONDS,
    notes=(
        "No real commercial site is claimed by the 'generic'/'other' "
        "label -- this describes only an anonymous, locally-supplied "
        "HTML file. `enabled` stays False purely because no live fetcher "
        "exists yet in this codebase, not because anything here is "
        "unsafe."
    ),
)

_GENERIC_HOTEL_RATINGS = ScrapingSourcePolicy(
    source_id="generic_hotel_ratings",
    source_name="Manual local scraped hotel ratings source",
    base_url="file://local-manual-hotel-ratings",
    allows_reviews=True,
    approved_for_personal_use=True,
    enabled=False,
    rate_limit_seconds=_RATE_LIMIT_SECONDS,
    notes=(
        "No real commercial site is claimed by the 'generic'/'other' "
        "label -- this describes only an anonymous, locally-supplied "
        "HTML file. No adapter reads this yet (Step 185E's job)."
    ),
)


default_scraping_source_registry = ScrapingSourceRegistry(
    [
        _BOOKING,
        _EXPEDIA,
        _HOTELBEDS,
        _HOSTELWORLD,
        _VRBO,
        _AIRBNB,
        _SKYSCANNER,
        _GOOGLE_FLIGHTS,
        _KIWI_MANUAL,
        _TRIPADVISOR,
        _GOOGLE_PLACES_RATINGS,
        _GENERIC_ACCOMMODATION,
        _GENERIC_FLIGHT,
        _GENERIC_HOTEL_RATINGS,
    ]
)


_ACCOMMODATION_LABEL_TO_SOURCE_ID: dict[str, str] = {
    "generic": "generic_accommodation",
    "booking": "booking",
    "expedia": "expedia",
    "hotelbeds": "hotelbeds",
    "hostelworld": "hostelworld",
    "vrbo": "vrbo",
    "airbnb": "airbnb",
}

_FLIGHT_LABEL_TO_SOURCE_ID: dict[str, str] = {
    "generic": "generic_flight",
    "skyscanner": "skyscanner",
    "google_flights": "google_flights",
    "kiwi": "kiwi_manual",
    "other": "generic_flight",
}

_HOTEL_RATINGS_LABEL_TO_SOURCE_ID: dict[str, str] = {
    "generic": "generic_hotel_ratings",
    "tripadvisor": "tripadvisor",
    "google_places": "google_places_ratings",
    "other": "generic_hotel_ratings",
}

_LABEL_MAPS_BY_CATEGORY: dict[str, dict[str, str]] = {
    "accommodation": _ACCOMMODATION_LABEL_TO_SOURCE_ID,
    "flight": _FLIGHT_LABEL_TO_SOURCE_ID,
    "hotel_ratings": _HOTEL_RATINGS_LABEL_TO_SOURCE_ID,
}

_GENERIC_POLICY_BY_CATEGORY: dict[str, ScrapingSourcePolicy] = {
    "accommodation": _GENERIC_ACCOMMODATION,
    "flight": _GENERIC_FLIGHT,
    "hotel_ratings": _GENERIC_HOTEL_RATINGS,
}


def get_default_scraping_source_registry() -> ScrapingSourceRegistry:
    """Returns the process-wide, pre-populated registry of every named
    commercial source this app tracks (Step 185B). Never performs a
    network call, never depends on `Settings`/a real `.env` -- safe to
    import and call from any test or module at any time, including
    during test collection.

    Distinct from `app.models.scraping.default_scraping_source_registry`,
    which stays intentionally empty -- that module defines the generic
    `ScrapingSourcePolicy`/`ScrapingSourceRegistry` contract itself and is
    never mutated by this one.
    """
    return default_scraping_source_registry


def get_scraping_source_policy(source_id: str) -> ScrapingSourcePolicy | None:
    """Looks up one named source's policy by its registry `source_id`
    (e.g. `"booking"`, `"kiwi_manual"`, `"tripadvisor"`,
    `"generic_accommodation"`), or `None` if unregistered. Never raises,
    never performs a network call.
    """
    return default_scraping_source_registry.get(source_id)


def resolve_manual_source_policy(category: str, source_label: str) -> ScrapingSourcePolicy:
    """Resolves a `*_MANUAL_HTML_SOURCE` config label (e.g. `"booking"`,
    `"kiwi"`, `"generic"`) to its registered `ScrapingSourcePolicy`,
    scoped to `category` (`"accommodation"`, `"flight"`, or
    `"hotel_ratings"`).

    Falls back to that category's generic policy for an unrecognized
    `category` or `source_label` -- never raises, never returns `None`.
    This mirrors (and is intended to replace) each scraped adapter's own
    small inline display-name dict with one shared, testable, real
    `ScrapingSourcePolicy` lookup; callers use only this policy's naming/
    notes metadata for display purposes; they do not use it to gate
    whether their own local-file-read operation may proceed -- reading a
    file the operator already supplied is a categorically different,
    always-safe operation from "is this named website approved for live
    scraping," which is what this policy's `enabled`/
    `approved_for_personal_use` actually describe.
    """
    generic_policy = _GENERIC_POLICY_BY_CATEGORY.get(category, _GENERIC_ACCOMMODATION)
    label_map = _LABEL_MAPS_BY_CATEGORY.get(category)
    if label_map is None:
        return generic_policy

    source_id = label_map.get(source_label)
    if source_id is None:
        return generic_policy

    return default_scraping_source_registry.get(source_id) or generic_policy


__all__ = [
    "default_scraping_source_registry",
    "get_default_scraping_source_registry",
    "get_scraping_source_policy",
    "resolve_manual_source_policy",
]
