"""Standards-based country identity (Section 3C.2).

A country is the same country whatever display name is used for it: a
traveller may type the current ISO short name while the geocoder answers
with an older English name (or the other way round). Comparing display
names calls that a different country.

This module resolves a country NAME to its ISO 3166-1 alpha-2 code using
the ISO 3166 data shipped by `pycountry` (short name, official name and
common name of every country) -- no hand-written alias table, and no
country is named in this code. The geocoder's own `country_code` can then
be compared as an identity.

Deliberately conservative:

  * only full names resolve. A two- or three-letter code is never read as
    a country here, because the same letters are also subdivision
    abbreviations (a state, a province) in ordinary destination strings;
  * matching is exact after Unicode/case/punctuation normalisation -- never
    fuzzy;
  * a name that is not in the standard, or that the standard gives to more
    than one country, resolves to nothing, and the caller keeps its
    existing display-name behaviour;
  * if the data package is not installed, nothing resolves.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_MIN_NAME_LENGTH = 4
_NAME_FIELDS = ("name", "official_name", "common_name")


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).strip()


@lru_cache(maxsize=1)
def _iso_names() -> dict[str, str]:
    """`{normalised ISO 3166 country name: alpha-2 code (lower case)}`."""
    try:
        import pycountry
    except Exception:  # the data package is optional: without it nothing resolves
        return {}
    index: dict[str, str] = {}
    ambiguous: set[str] = set()
    for country in pycountry.countries:
        code = str(country.alpha_2).lower()
        for field in _NAME_FIELDS:
            key = _normalize(getattr(country, field, "") or "")
            if not key:
                continue
            if index.get(key, code) != code:
                ambiguous.add(key)
            index[key] = code
    for key in ambiguous:
        del index[key]
    return index


def iso_country_code(name: str) -> str | None:
    """The ISO 3166-1 alpha-2 code (lower case) of the country `name`
    names, or None when the standard does not resolve it."""
    key = _normalize(name)
    if len(key.replace(" ", "")) < _MIN_NAME_LENGTH:
        return None
    return _iso_names().get(key)


def normalized_country_code(value: object) -> str | None:
    """A provider's `country_code` as a lower-case ISO alpha-2 code, or None
    when it is missing or not two ASCII letters."""
    if not isinstance(value, str):
        return None
    code = value.strip().lower()
    return code if len(code) == 2 and code.isascii() and code.isalpha() else None


def same_country(name: str, provider_country_code: object) -> bool:
    """True only when `name` resolves to an ISO identity AND the provider's
    country code is that same identity. Anything unresolved is False."""
    requested = iso_country_code(name)
    return requested is not None and requested == normalized_country_code(provider_country_code)
