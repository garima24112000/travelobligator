"""M2 Phase 1 (D1): the ISO 3166 -> ISO 4217 table that replaced the
eleven-name destination-text currency table. These tests are the vetting
step its module docstring names."""

from __future__ import annotations

import pycountry

from app.utils.country_currency import (
    AMBIGUOUS_CURRENCY_COUNTRIES,
    CURRENCY_AMBIGUOUS,
    CURRENCY_BY_COUNTRY_CODE,
    CURRENCY_NOT_DETERMINED,
    ISO_4217_AMENDMENTS_AFTER_PYCOUNTRY,
    NON_ISO_3166_COUNTRY_CODES,
    NO_UNIVERSAL_CURRENCY_COUNTRIES,
    currency_for_country,
)


def test_kosovo_uses_the_euro() -> None:
    assert currency_for_country("xk").currency == "EUR"


def test_every_iso_country_is_accounted_for_exactly_once() -> None:
    iso_countries = {country.alpha_2 for country in pycountry.countries}
    mapped = set(CURRENCY_BY_COUNTRY_CODE) - NON_ISO_3166_COUNTRY_CODES
    assert not NON_ISO_3166_COUNTRY_CODES & iso_countries  # the exception list holds only non-ISO codes
    ambiguous = set(AMBIGUOUS_CURRENCY_COUNTRIES)
    no_universal = set(NO_UNIVERSAL_CURRENCY_COUNTRIES)

    assert mapped | ambiguous | no_universal == iso_countries
    assert not mapped & ambiguous
    assert not mapped & no_universal
    assert not ambiguous & no_universal


def test_every_currency_is_an_iso_4217_code() -> None:
    known = {currency.alpha_3 for currency in pycountry.currencies} | ISO_4217_AMENDMENTS_AFTER_PYCOUNTRY
    used = set(CURRENCY_BY_COUNTRY_CODE.values()) | {
        code for codes in AMBIGUOUS_CURRENCY_COUNTRIES.values() for code in codes
    }
    assert used <= known
    # The allowance is only for codes the pinned pycountry really lacks.
    pycountry_codes = {currency.alpha_3 for currency in pycountry.currencies}
    assert not ISO_4217_AMENDMENTS_AFTER_PYCOUNTRY & pycountry_codes


def test_destinations_the_old_table_missed_now_resolve() -> None:
    # Japan, Turkey, Czechia, Vietnam, South Korea, Mexico, Germany: none of
    # these resolved with the old destination-name table.
    for code, currency in {
        "JP": "JPY",
        "TR": "TRY",
        "CZ": "CZK",
        "VN": "VND",
        "KR": "KRW",
        "MX": "MXN",
        "DE": "EUR",
        "TH": "THB",
    }.items():
        assert currency_for_country(code).currency == currency


def test_dated_euro_adoptions() -> None:
    assert currency_for_country("HR").currency == "EUR"
    assert currency_for_country("BG").currency == "EUR"


def test_lookup_is_case_and_space_insensitive() -> None:
    assert currency_for_country(" jp ").currency == "JPY"


def test_ambiguous_country_names_no_currency() -> None:
    for code in ("PA", "BT", "LS", "NA", "SV", "HT", "VE"):
        resolution = currency_for_country(code)
        assert resolution.currency is None
        assert resolution.reason == CURRENCY_AMBIGUOUS


def test_no_universal_or_unknown_country_names_no_currency() -> None:
    for code in ("AQ", "PS", "GS", "XX", "ZZ"):
        resolution = currency_for_country(code)
        assert resolution.currency is None
        assert resolution.reason == CURRENCY_NOT_DETERMINED
