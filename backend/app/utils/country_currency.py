from __future__ import annotations

from dataclasses import dataclass

# M2 Phase 1 (D1): ISO 3166-1 alpha-2 country code -> ISO 4217 currency code.
#
# Source: the ISO 4217 "list one" (current currency & funds), entity by
# entity, reduced to each entity's circulating national currency (fund codes
# such as USN/CLF/COU and precious metals are not listed). It replaces the
# eleven-name destination-text table the currency adapter used before, and
# is keyed by the country code the destination geocoder already returned
# (`DestinationContextService` reads it from the cached resolution -- no new
# geocoding request).
#
# Vetting: `tests/utils/test_country_currency.py` checks that every key is an
# ISO 3166-1 alpha-2 code known to `pycountry`, every value is an ISO 4217
# code known to `pycountry` (or one of `ISO_4217_AMENDMENTS_AFTER_PYCOUNTRY`),
# and that every country `pycountry` knows is accounted for exactly once:
# here, in `AMBIGUOUS_CURRENCY_COUNTRIES`, or in `NO_UNIVERSAL_CURRENCY_COUNTRIES`.
#
# Dated facts (keep in step with ISO 4217 amendments): Croatia uses EUR since
# 2023-01-01, Bulgaria since 2026-01-01; Curacao and Sint Maarten use the
# Caribbean guilder XCG since 2025-03-31; Zimbabwe's ISO code is ZWG since
# 2024-06-25; Sierra Leone's is SLE.

# ISO 4217 codes newer than the ISO data shipped with the pinned `pycountry`.
ISO_4217_AMENDMENTS_AFTER_PYCOUNTRY: frozenset[str] = frozenset({"XCG", "ZWG"})

CURRENCY_BY_COUNTRY_CODE: dict[str, str] = {
    "AD": "EUR", "AE": "AED", "AF": "AFN", "AG": "XCD", "AI": "XCD", "AL": "ALL",
    "AM": "AMD", "AO": "AOA", "AR": "ARS", "AS": "USD", "AT": "EUR", "AU": "AUD",
    "AW": "AWG", "AX": "EUR", "AZ": "AZN",
    "BA": "BAM", "BB": "BBD", "BD": "BDT", "BE": "EUR", "BF": "XOF", "BG": "EUR",
    "BH": "BHD", "BI": "BIF", "BJ": "XOF", "BL": "EUR", "BM": "BMD", "BN": "BND",
    "BO": "BOB", "BQ": "USD", "BR": "BRL", "BS": "BSD", "BV": "NOK", "BW": "BWP",
    "BY": "BYN", "BZ": "BZD",
    "CA": "CAD", "CC": "AUD", "CD": "CDF", "CF": "XAF", "CG": "XAF", "CH": "CHF",
    "CI": "XOF", "CK": "NZD", "CL": "CLP", "CM": "XAF", "CN": "CNY", "CO": "COP",
    "CR": "CRC", "CU": "CUP", "CV": "CVE", "CW": "XCG", "CX": "AUD", "CY": "EUR",
    "CZ": "CZK",
    "DE": "EUR", "DJ": "DJF", "DK": "DKK", "DM": "XCD", "DO": "DOP", "DZ": "DZD",
    "EC": "USD", "EE": "EUR", "EG": "EGP", "EH": "MAD", "ER": "ERN", "ES": "EUR",
    "ET": "ETB",
    "FI": "EUR", "FJ": "FJD", "FK": "FKP", "FM": "USD", "FO": "DKK", "FR": "EUR",
    "GA": "XAF", "GB": "GBP", "GD": "XCD", "GE": "GEL", "GF": "EUR", "GG": "GBP",
    "GH": "GHS", "GI": "GIP", "GL": "DKK", "GM": "GMD", "GN": "GNF", "GP": "EUR",
    "GQ": "XAF", "GR": "EUR", "GT": "GTQ", "GU": "USD", "GW": "XOF", "GY": "GYD",
    "HK": "HKD", "HM": "AUD", "HN": "HNL", "HR": "EUR", "HU": "HUF",
    "ID": "IDR", "IE": "EUR", "IL": "ILS", "IM": "GBP", "IN": "INR", "IO": "USD",
    "IQ": "IQD", "IR": "IRR", "IS": "ISK", "IT": "EUR",
    "JE": "GBP", "JM": "JMD", "JO": "JOD", "JP": "JPY",
    "KE": "KES", "KG": "KGS", "KH": "KHR", "KI": "AUD", "KM": "KMF", "KN": "XCD",
    "KP": "KPW", "KR": "KRW", "KW": "KWD", "KY": "KYD", "KZ": "KZT",
    "LA": "LAK", "LB": "LBP", "LC": "XCD", "LI": "CHF", "LK": "LKR", "LR": "LRD",
    "LT": "EUR", "LU": "EUR", "LV": "EUR", "LY": "LYD",
    "MA": "MAD", "MC": "EUR", "MD": "MDL", "ME": "EUR", "MF": "EUR", "MG": "MGA",
    "MH": "USD", "MK": "MKD", "ML": "XOF", "MM": "MMK", "MN": "MNT", "MO": "MOP",
    "MP": "USD", "MQ": "EUR", "MR": "MRU", "MS": "XCD", "MT": "EUR", "MU": "MUR",
    "MV": "MVR", "MW": "MWK", "MX": "MXN", "MY": "MYR", "MZ": "MZN",
    "NC": "XPF", "NE": "XOF", "NF": "AUD", "NG": "NGN", "NI": "NIO", "NL": "EUR",
    "NO": "NOK", "NP": "NPR", "NR": "AUD", "NU": "NZD", "NZ": "NZD",
    "OM": "OMR",
    "PE": "PEN", "PF": "XPF", "PG": "PGK", "PH": "PHP", "PK": "PKR", "PL": "PLN",
    "PM": "EUR", "PN": "NZD", "PR": "USD", "PT": "EUR", "PW": "USD", "PY": "PYG",
    "QA": "QAR",
    "RE": "EUR", "RO": "RON", "RS": "RSD", "RU": "RUB", "RW": "RWF",
    "SA": "SAR", "SB": "SBD", "SC": "SCR", "SD": "SDG", "SE": "SEK", "SG": "SGD",
    "SH": "SHP", "SI": "EUR", "SJ": "NOK", "SK": "EUR", "SL": "SLE", "SM": "EUR",
    "SN": "XOF", "SO": "SOS", "SR": "SRD", "SS": "SSP", "ST": "STN", "SX": "XCG",
    "SY": "SYP", "SZ": "SZL",
    "TC": "USD", "TD": "XAF", "TF": "EUR", "TG": "XOF", "TH": "THB", "TJ": "TJS",
    "TK": "NZD", "TL": "USD", "TM": "TMT", "TN": "TND", "TO": "TOP", "TR": "TRY",
    "TT": "TTD", "TV": "AUD", "TW": "TWD", "TZ": "TZS",
    "UA": "UAH", "UG": "UGX", "UM": "USD", "US": "USD", "UY": "UYU", "UZ": "UZS",
    "VA": "EUR", "VC": "XCD", "VG": "USD", "VI": "USD", "VN": "VND", "VU": "VUV",
    "WF": "XPF", "WS": "WST",
    "YE": "YER", "YT": "EUR",
    "ZA": "ZAR", "ZM": "ZMW", "ZW": "ZWG",
    # Kosovo: not in ISO 3166-1 (`XK` is the user-assigned code geocoders
    # return for it), and not a separate ISO 4217 entity; it uses the euro.
    "XK": "EUR",
}

# Keys above that are not ISO 3166-1 codes, each documented where it is set.
NON_ISO_3166_COUNTRY_CODES: frozenset[str] = frozenset({"XK"})

# Entities for which ISO 4217 lists more than one circulating currency. No
# single exchange rate would be honest for them, so none is requested.
AMBIGUOUS_CURRENCY_COUNTRIES: dict[str, tuple[str, ...]] = {
    "BT": ("BTN", "INR"),
    "HT": ("HTG", "USD"),
    "LS": ("LSL", "ZAR"),
    "NA": ("NAD", "ZAR"),
    "PA": ("PAB", "USD"),
    "SV": ("SVC", "USD"),
    "VE": ("VES", "VED"),
}

# Entities ISO 4217 lists as having "no universal currency".
NO_UNIVERSAL_CURRENCY_COUNTRIES: frozenset[str] = frozenset({"AQ", "GS", "PS"})

CURRENCY_AMBIGUOUS = "currency_ambiguous"
CURRENCY_NOT_DETERMINED = "currency_not_determined"


@dataclass(frozen=True)
class CurrencyResolution:
    """`currency` is set only when exactly one currency applies; otherwise
    `reason` says why none is named (never a guess)."""

    currency: str | None
    reason: str | None


def currency_for_country(country_code: str) -> CurrencyResolution:
    code = country_code.strip().upper()
    if code in AMBIGUOUS_CURRENCY_COUNTRIES:
        return CurrencyResolution(currency=None, reason=CURRENCY_AMBIGUOUS)
    currency = CURRENCY_BY_COUNTRY_CODE.get(code)
    if currency is None:
        return CurrencyResolution(currency=None, reason=CURRENCY_NOT_DETERMINED)
    return CurrencyResolution(currency=currency, reason=None)
