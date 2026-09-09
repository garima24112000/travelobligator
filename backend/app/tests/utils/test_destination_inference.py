from __future__ import annotations

from app.utils.destination_inference import (
    US_STATE_OR_TERRITORY_TO_COUNTRY_CODE,
    infer_us_country_code_from_state_segment,
)


def test_full_state_name_with_multiple_segments_resolves_to_us() -> None:
    assert (
        infer_us_country_code_from_state_segment("florida", has_multiple_segments=True)
        == "US"
    )
    assert (
        infer_us_country_code_from_state_segment("new jersey", has_multiple_segments=True)
        == "US"
    )


def test_two_letter_abbreviation_with_multiple_segments_resolves_to_us() -> None:
    assert infer_us_country_code_from_state_segment("fl", has_multiple_segments=True) == "US"
    assert infer_us_country_code_from_state_segment("nj", has_multiple_segments=True) == "US"
    assert infer_us_country_code_from_state_segment("ma", has_multiple_segments=True) == "US"
    assert infer_us_country_code_from_state_segment("ny", has_multiple_segments=True) == "US"
    assert infer_us_country_code_from_state_segment("ca", has_multiple_segments=True) == "US"


def test_washington_dc_variants_resolve_to_us() -> None:
    assert infer_us_country_code_from_state_segment("dc", has_multiple_segments=True) == "US"
    assert (
        infer_us_country_code_from_state_segment(
            "district of columbia", has_multiple_segments=True
        )
        == "US"
    )


def test_single_bare_segment_never_resolves_even_if_it_is_a_state_name() -> None:
    """The safety gate: a single ambiguous word must never resolve on its
    own, even if it happens to be a real US state name -- "Georgia" is
    both a US state and a sovereign country, so guessing here would be an
    unsafe false positive, not a conservative inference."""
    assert (
        infer_us_country_code_from_state_segment("georgia", has_multiple_segments=False)
        is None
    )
    assert (
        infer_us_country_code_from_state_segment("florida", has_multiple_segments=False)
        is None
    )
    assert (
        infer_us_country_code_from_state_segment("fl", has_multiple_segments=False) is None
    )


def test_unrecognized_segment_returns_none() -> None:
    assert (
        infer_us_country_code_from_state_segment("testland", has_multiple_segments=True)
        is None
    )
    assert infer_us_country_code_from_state_segment("", has_multiple_segments=True) is None


def test_table_has_exactly_fifty_states_plus_dc_entries() -> None:
    # 50 full state names + 50 two-letter abbreviations + 3 DC variants
    # ("district of columbia", "dc", "washington dc"). This is a coverage
    # sanity check, not a claim that every possible DC spelling is covered.
    assert len(US_STATE_OR_TERRITORY_TO_COUNTRY_CODE) == 103
    assert all(value == "US" for value in US_STATE_OR_TERRITORY_TO_COUNTRY_CODE.values())
