from __future__ import annotations

from pathlib import Path
from typing import Any

import pycountry
import pytest

from app.providers.geocoding.geoapify_adapter import GeoapifyGeocoder
from app.providers.places import country_identity
from app.providers.places import destination_resolution as resolution

# Section 3C.2: two generic destination-identity rules.
#   A. a settlement name that differs only in its SEPARATORS is the same name;
#   B. a country is compared by ISO 3166 identity, not by display name.
# The two shapes confirmed live are reproduced once each as fixtures; every
# other case is synthetic, and the production modules name no place at all.

_PRODUCTION_MODULES = (
    Path(resolution.__file__),
    Path(country_identity.__file__),
)


def _geoapify(**fields: Any) -> dict[str, Any]:
    return {"place_id": "abc123", "lat": 10.0, "lon": 20.0, **fields}


def _reason(query: str, result: dict[str, Any]) -> str | None:
    hit = GeoapifyGeocoder()._hit(result)
    assert hit is not None
    return resolution.destination_rejection_reason(query, resolution._hit_evidence(hit))


def _city(name: str, country: str = "Fixtureland", code: str | None = "fx", **extra: Any) -> dict[str, Any]:
    fields = {"result_type": "city", "name": name, "city": name, "country": country,
              "formatted": f"{name}, {country}", **extra}
    if code is not None:
        fields["country_code"] = code
    return _geoapify(**fields)


# =====================================================================================
# A. Separator variation
# =====================================================================================


# 1. the confirmed live shape
def test_a_name_the_provider_writes_as_two_words_matches_the_one_word_query() -> None:
    confirmed = _geoapify(
        result_type="city", name="Hà Nội", city="Hà Nội", country="Vietnam", country_code="vn",
        formatted="Hà Nội, Vietnam",
    )
    assert _reason("Hanoi, Vietnam", confirmed) is None
    assert _reason("Ha Noi, Vietnam", confirmed) is None  # already accepted before: unchanged


# 2. accent + separator variation, both directions
@pytest.mark.parametrize(
    ("query", "provider_name"),
    [
        ("Tamrakesh, Fixtureland", "Tam Rakesh"),
        ("Tamrakesh, Fixtureland", "Tâm Rákesh"),
        ("Tam Rakesh, Fixtureland", "Tamrakesh"),
        ("Tâm-Rákesh, Fixtureland", "Tamrakesh"),
        ("Tamrakesh, Fixtureland", "Tam-Rakesh"),
        ("Tamrakesh, Fixtureland", "Tam Ra Kesh"),
        ("TAMRAKESH, fixtureland", "tam rakesh"),
    ],
)
def test_accent_and_separator_variation_is_the_same_name(query: str, provider_name: str) -> None:
    assert _reason(query, _city(provider_name)) is None


# 3. unrelated names that share partial characters
@pytest.mark.parametrize(
    "provider_name",
    [
        "Tam Rakeshi",  # the query is only a prefix of the compact form
        "Rakesh",  # ... or contains it
        "Tam Rakand",  # different letters
        "Kesh Tamra",  # the same words in another order
        "Rakesh Tam",
    ],
)
def test_compact_forms_must_be_identical_never_a_substring_or_a_reordering(provider_name: str) -> None:
    assert _reason("Tamrakesh, Fixtureland", _city(provider_name)) == resolution.REJECT_LOCALITY_MISMATCH


# 4. wrong country
def test_a_separator_variant_in_the_wrong_country_is_rejected() -> None:
    assert _reason("Tamrakesh, Fixtureland", _city("Tam Rakesh", "Otherland", "ot")) == resolution.REJECT_LOCALITY_MISMATCH
    # the query must carry country evidence at all
    assert _reason("Tamrakesh", _city("Tam Rakesh")) == resolution.REJECT_LOCALITY_MISMATCH
    assert _reason("Tamrakesh, FX", _city("Tam Rakesh")) == resolution.REJECT_LOCALITY_MISMATCH
    # ... and the provider must state its country
    no_country = _geoapify(result_type="city", name="Tam Rakesh", city="Tam Rakesh", formatted="Tam Rakesh")
    assert _reason("Tamrakesh, Fixtureland", no_country) == resolution.REJECT_LOCALITY_MISMATCH


# 5. non-city result
@pytest.mark.parametrize("result_type", ["suburb", "district", "county", "state"])
def test_a_separator_variant_is_only_accepted_for_a_city_level_result(result_type: str) -> None:
    area = _geoapify(
        result_type=result_type, **{result_type: "Tam Rakesh"}, name="Tam Rakesh", country="Fixtureland",
        country_code="fx", formatted="Tam Rakesh, Fixtureland",
    )
    assert _reason("Tamrakesh, Fixtureland", area) == resolution.REJECT_LOCALITY_MISMATCH
    poi = _geoapify(
        result_type="amenity", name="Tam Rakesh", city="Tam Rakesh", country="Fixtureland", country_code="fx",
        formatted="Tam Rakesh, Fixtureland",
    )
    assert _reason("Tamrakesh, Fixtureland", poi) == resolution.REJECT_UNSUPPORTED_RESULT_TYPE


def test_only_the_settlement_name_is_compacted_never_other_address_components() -> None:
    # the compact form matches the STATE, not the city: that is not the requested settlement
    result = _city("Belvarro", state="Tam Rakesh")
    assert _reason("Tamrakesh, Fixtureland", result) == resolution.REJECT_LOCALITY_MISMATCH
    # and a region segment is never matched by its compact form
    assert _reason("Belvarro, Tamrakesh, Fixtureland", result) == resolution.REJECT_REGION_MISMATCH


# 6. short / ambiguous compact strings
@pytest.mark.parametrize(("query", "provider_name"), [("Abcd, Fixtureland", "Ab Cd"), ("Ab, Fixtureland", "A B"), ("Xyz, Fixtureland", "X Yz")])
def test_short_compact_strings_never_match(query: str, provider_name: str) -> None:
    assert _reason(query, _city(provider_name)) == resolution.REJECT_LOCALITY_MISMATCH


def test_the_compact_rule_has_a_minimum_length_and_ignores_empty_names() -> None:
    assert resolution._COMPACT_NAME_MIN_LENGTH >= 5
    assert _reason("Abcde, Fixtureland", _city("Ab Cde")) is None  # exactly at the minimum
    # an empty place name never matches anything
    assert _reason(" , Fixtureland", _city("Tam Rakesh")) == resolution.REJECT_LOCALITY_MISMATCH


# =====================================================================================
# B. Country identity (ISO 3166)
# =====================================================================================


def _country_with_two_names() -> tuple[str, str, str]:
    """`(short name, another ISO name of the same country, alpha-2)` taken
    from the standard's own data, so no country is hand-picked here."""
    for country in pycountry.countries:
        other = getattr(country, "common_name", None) or getattr(country, "official_name", None)
        if other and country_identity._normalize(other) != country_identity._normalize(country.name):
            if country_identity.iso_country_code(country.name) and country_identity.iso_country_code(other):
                return country.name, other, country.alpha_2.lower()
    raise AssertionError("the ISO data has no country with two names")


# 1. standards-equivalent names resolve to the same ISO identity
def test_two_iso_names_of_one_country_are_the_same_country() -> None:
    name, other, code = _country_with_two_names()
    assert country_identity.iso_country_code(name) == country_identity.iso_country_code(other) == code
    # the provider answers with yet another display name for it, and the same country code
    result = _city("Tamrakesh", "A Former Display Name", code)
    assert _reason(f"Tamrakesh, {name}", result) is None
    assert _reason(f"Tamrakesh, {other}", result) is None


def test_the_confirmed_live_shape_resolves() -> None:
    confirmed = _geoapify(
        result_type="city", name="Istanbul", city="Istanbul", country="Turkey", country_code="tr",
        formatted="Istanbul, Turkey",
    )
    assert _reason("Istanbul, Türkiye", confirmed) is None
    assert _reason("Istanbul, Turkey", confirmed) is None  # exact display name: unchanged


# 2. exact country name still works (with or without a country code)
def test_an_exact_country_display_name_still_agrees() -> None:
    assert _reason("Tamrakesh, Fixtureland", _city("Tamrakesh")) is None
    assert _reason("Tamrakesh, Fixtureland", _city("Tamrakesh", code=None)) is None
    assert _reason("Tamrakesh, Fixtureland", _city("Tamrakesh", code="zz")) is None  # an unknown code never vetoes


# 3. wrong ISO country
def test_a_different_iso_country_is_rejected() -> None:
    name, _other, code = _country_with_two_names()
    wrong = next(c.alpha_2.lower() for c in pycountry.countries if c.alpha_2.lower() != code)
    assert _reason(f"Tamrakesh, {name}", _city("Tamrakesh", "A Former Display Name", wrong)) == resolution.REJECT_COUNTRY_MISMATCH
    assert country_identity.same_country(name, wrong) is False


# 4. unknown query country: conservative
def test_an_unknown_query_country_keeps_the_display_name_rule() -> None:
    assert country_identity.iso_country_code("Fixtureland") is None
    assert country_identity.iso_country_code("") is None
    name, _other, code = _country_with_two_names()
    # the provider's code is a real country, the query's country is not one: no identity, no pass
    assert _reason("Tamrakesh, Fixtureland", _city("Tamrakesh", name, code)) == resolution.REJECT_COUNTRY_MISMATCH
    # a short segment is never read as a country code (it may be a state abbreviation)
    for abbreviation in ("US", "USA", "IL", "GA", "CA", "DE"):
        assert country_identity.iso_country_code(abbreviation) is None
    # never fuzzy: a misspelt or partial country name does not resolve
    assert country_identity.iso_country_code(name + "x") is None
    assert country_identity.iso_country_code(name[: max(4, len(name) - 2)]) in (None, code)


# 5. missing / malformed provider country_code: conservative
@pytest.mark.parametrize("bad_code", [None, "", "t", "trk", "12", "t1", " ", 792])
def test_a_missing_or_malformed_provider_country_code_never_passes(bad_code: Any) -> None:
    name, _other, _code = _country_with_two_names()
    assert country_identity.normalized_country_code(bad_code) is None
    assert country_identity.same_country(name, bad_code) is False
    result = _city("Tamrakesh", "A Former Display Name", None)
    if bad_code is not None:
        result["country_code"] = bad_code
    assert _reason(f"Tamrakesh, {name}", result) == resolution.REJECT_COUNTRY_MISMATCH


# 6. case / accent normalisation
def test_country_names_and_codes_are_normalised() -> None:
    name, _other, code = _country_with_two_names()
    for variant in (name.upper(), name.lower(), f"  {name}  "):
        assert country_identity.iso_country_code(variant) == code
    for provider_code in (code.upper(), code.lower(), f" {code.upper()} "):
        assert country_identity.normalized_country_code(provider_code) == code
        assert _reason(f"Tamrakesh, {name.upper()}", _city("Tamrakesh", "A Former Display Name", provider_code)) is None
    # an accented ISO name resolves with or without its accents
    accented = next(c for c in pycountry.countries if c.name.isascii() is False and len(c.name) >= 4)
    stripped = "".join(ch for ch in country_identity.unicodedata.normalize("NFKD", accented.name)
                       if not country_identity.unicodedata.combining(ch))
    assert country_identity.iso_country_code(stripped) == accented.alpha_2.lower()


def test_iso_identity_is_an_extra_way_to_agree_never_a_new_way_to_reject() -> None:
    # A last segment that names a REGION which happens to share a country's ISO name still
    # agrees through the provider's own address component, exactly as before.
    name, _other, code = _country_with_two_names()
    other_code = next(c.alpha_2.lower() for c in pycountry.countries if c.alpha_2.lower() != code)
    regional = _city("Tamrakesh", "Fixtureland", other_code, state=name)
    assert _reason(f"Tamrakesh, {name}", regional) is None


def test_country_identity_also_supports_the_equivalent_locality_rules() -> None:
    name, _other, code = _country_with_two_names()
    result = _city("Tam Rakesh", "A Former Display Name", code)
    assert _reason(f"Tamrakesh, {name}", result) is None  # separator variant + ISO country
    assert _reason(f"Tamrakech, {name}", _city("Tamrakesh", "A Former Display Name", code)) is None
    wrong = next(c.alpha_2.lower() for c in pycountry.countries if c.alpha_2.lower() != code)
    assert _reason(f"Tamrakesh, {name}", _city("Tam Rakesh", "A Former Display Name", wrong)) == resolution.REJECT_LOCALITY_MISMATCH


def test_without_the_data_package_nothing_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    name, _other, code = _country_with_two_names()
    monkeypatch.setattr(country_identity, "_iso_names", lambda: {})
    assert country_identity.iso_country_code(name) is None
    assert _reason(f"Tamrakesh, {name}", _city("Tamrakesh", "A Former Display Name", code)) == resolution.REJECT_COUNTRY_MISMATCH


def test_production_code_names_no_city_or_country() -> None:
    for path in _PRODUCTION_MODULES:
        source = path.read_text().casefold()
        for literal in ("hanoi", "ha noi", "hà nội", "istanbul", "turkey", "türkiye", "turkiye", "vietnam", "viet nam"):
            assert literal not in source, (path.name, literal)
