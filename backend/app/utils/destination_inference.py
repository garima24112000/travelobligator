from __future__ import annotations

# Deterministic US state/territory name and postal-abbreviation -> ISO
# 3166-1 alpha-2 country code mapping (Step 182B,
# docs/12_provider_architecture.md, docs/14_backend_architecture.md).
#
# This exists because the pre-existing country/currency inference in
# `app.providers.holidays.nager_date_adapter`/
# `app.providers.currency.frankfurter_adapter` only recognized a small set
# of full country names (e.g. "united states") as the trailing
# comma-segment of a destination string. A destination written the way
# almost every US traveler actually writes one -- "Orlando, FL",
# "Jersey City, NJ", "Boston, MA" -- has a US state, not a country name, as
# that trailing segment, so it never matched and Nager.Date/Frankfurter
# were skipped entirely (reported honestly as `unavailable`, never
# guessed, but avoidably so for the single largest class of domestic trips
# this app is likely to plan).
#
# No LLM, no fuzzy/substring matching, no geocoding call: every key below
# is an exact, lowercased state name or two-letter postal abbreviation.
# Every value is the literal string "US".
US_STATE_OR_TERRITORY_TO_COUNTRY_CODE: dict[str, str] = {
    # Full state names.
    "alabama": "US", "alaska": "US", "arizona": "US", "arkansas": "US",
    "california": "US", "colorado": "US", "connecticut": "US", "delaware": "US",
    "florida": "US", "georgia": "US", "hawaii": "US", "idaho": "US",
    "illinois": "US", "indiana": "US", "iowa": "US", "kansas": "US",
    "kentucky": "US", "louisiana": "US", "maine": "US", "maryland": "US",
    "massachusetts": "US", "michigan": "US", "minnesota": "US", "mississippi": "US",
    "missouri": "US", "montana": "US", "nebraska": "US", "nevada": "US",
    "new hampshire": "US", "new jersey": "US", "new mexico": "US", "new york": "US",
    "north carolina": "US", "north dakota": "US", "ohio": "US", "oklahoma": "US",
    "oregon": "US", "pennsylvania": "US", "rhode island": "US", "south carolina": "US",
    "south dakota": "US", "tennessee": "US", "texas": "US", "utah": "US",
    "vermont": "US", "virginia": "US", "washington": "US", "west virginia": "US",
    "wisconsin": "US", "wyoming": "US",
    # Washington, D.C. -- a real US jurisdiction, not a state, but the same
    # "City, X" trailing segment shape (e.g. "Washington, DC").
    "district of columbia": "US", "dc": "US", "washington dc": "US",
    # Two-letter USPS state abbreviations. Kept in a clearly-marked block
    # since a bare two-letter code is the part of this mapping most likely
    # to collide with an unrelated country's own shorthand (e.g. "IN" is
    # both Indiana's abbreviation and India's ISO 3166-1 alpha-2 code).
    # `infer_country_code`/`infer_destination_currency` always check the
    # existing full-country-name mapping first and only ever consult this
    # abbreviation table when the destination was written in "City, X"
    # form (never from a single bare word) -- see each function's own
    # docstring for the exact safety gate.
    "al": "US", "ak": "US", "az": "US", "ar": "US", "ca": "US", "co": "US",
    "ct": "US", "de": "US", "fl": "US", "ga": "US", "hi": "US", "id": "US",
    "il": "US", "in": "US", "ia": "US", "ks": "US", "ky": "US", "la": "US",
    "me": "US", "md": "US", "ma": "US", "mi": "US", "mn": "US", "ms": "US",
    "mo": "US", "mt": "US", "ne": "US", "nv": "US", "nh": "US", "nj": "US",
    "nm": "US", "ny": "US", "nc": "US", "nd": "US", "oh": "US", "ok": "US",
    "or": "US", "pa": "US", "ri": "US", "sc": "US", "sd": "US", "tn": "US",
    "tx": "US", "ut": "US", "vt": "US", "va": "US", "wa": "US", "wv": "US",
    "wi": "US", "wy": "US",
}


def infer_us_country_code_from_state_segment(
    candidate: str, *, has_multiple_segments: bool
) -> str | None:
    """Returns `"US"` if `candidate` (an already-lowercased, already-
    stripped destination segment) is a recognized US state/territory name
    or postal abbreviation, `has_multiple_segments` is `True`, and no
    country-name match was already found -- otherwise returns `None`.

    `has_multiple_segments` must be `True` (i.e. the destination was
    written as "City, State", not just a single bare word) before this
    ever returns a match. This is the safety gate that stops a single
    ambiguous word from resolving to the US: "Georgia" alone must stay
    unresolved (it could be the US state or the country), but
    "Atlanta, Georgia" or "Atlanta, GA" safely resolves to `"US"` because
    the comma-separated form itself is the signal that `candidate` is
    being used as a state/region qualifier, not as a bare, possibly
    country-named destination on its own.
    """
    if not has_multiple_segments:
        return None
    return US_STATE_OR_TERRITORY_TO_COUNTRY_CODE.get(candidate)
