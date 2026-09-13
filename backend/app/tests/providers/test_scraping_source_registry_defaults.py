from __future__ import annotations

import ast
import inspect

from app.models.scraping import ScrapingSourcePolicy
from app.providers.scraping_source_registry_defaults import (
    default_scraping_source_registry,
    get_default_scraping_source_registry,
    get_scraping_source_policy,
    resolve_manual_source_policy,
)

# Tests for Step 185B's populated scraping source registry. Never calls a
# real network service, never parses real HTML -- these only exercise
# in-memory registry/policy construction and lookup logic.

_RESTRICTED_SOURCE_IDS = {
    "booking",
    "expedia",
    "vrbo",
    "airbnb",
    "tripadvisor",
    "google_flights",
}
_UNREVIEWED_SOURCE_IDS = {"hotelbeds", "hostelworld", "skyscanner"}
_ALL_NAMED_SOURCE_IDS = _RESTRICTED_SOURCE_IDS | _UNREVIEWED_SOURCE_IDS | {
    "kiwi_manual",
    "google_places_ratings",
}
_GENERIC_SOURCE_IDS = {"generic_accommodation", "generic_flight", "generic_hotel_ratings"}


# ---------------------------------------------------------------------------
# Registry population: every target source is represented.
# ---------------------------------------------------------------------------


def test_default_registry_includes_every_target_source() -> None:
    registered_ids = {source.source_id for source in default_scraping_source_registry.all_sources()}
    assert _ALL_NAMED_SOURCE_IDS <= registered_ids
    assert _GENERIC_SOURCE_IDS <= registered_ids


def test_get_default_scraping_source_registry_returns_the_populated_instance() -> None:
    registry = get_default_scraping_source_registry()
    assert registry is default_scraping_source_registry
    assert len(registry.all_sources()) >= len(_ALL_NAMED_SOURCE_IDS) + len(_GENERIC_SOURCE_IDS)


def test_get_scraping_source_policy_returns_registered_source() -> None:
    policy = get_scraping_source_policy("booking")
    assert policy is not None
    assert policy.source_name == "Booking.com"


def test_get_scraping_source_policy_returns_none_for_unknown_source() -> None:
    assert get_scraping_source_policy("does_not_exist") is None


def test_default_registry_is_distinct_from_the_empty_foundational_registry() -> None:
    """`app.models.scraping.default_scraping_source_registry` must stay
    empty (its own test, test_default_registry_starts_empty, asserts
    this) -- this populated registry lives in a separate module/instance
    entirely and never mutates that one."""
    import app.models.scraping as scraping_models_module

    assert scraping_models_module.default_scraping_source_registry.all_sources() == []
    assert default_scraping_source_registry is not scraping_models_module.default_scraping_source_registry


# ---------------------------------------------------------------------------
# Restricted sources are represented but never active for live fetch.
# ---------------------------------------------------------------------------


def test_restricted_sources_are_all_registered() -> None:
    for source_id in _RESTRICTED_SOURCE_IDS:
        assert default_scraping_source_registry.get(source_id) is not None


def test_restricted_sources_are_not_approved_for_personal_use() -> None:
    for source_id in _RESTRICTED_SOURCE_IDS:
        policy = default_scraping_source_registry.get(source_id)
        assert policy is not None
        assert policy.approved_for_personal_use is False


def test_restricted_sources_are_unsafe() -> None:
    for source_id in _RESTRICTED_SOURCE_IDS:
        policy = default_scraping_source_registry.get(source_id)
        assert policy is not None
        assert policy.is_unsafe is True


def test_restricted_sources_are_disabled() -> None:
    for source_id in _RESTRICTED_SOURCE_IDS:
        policy = default_scraping_source_registry.get(source_id)
        assert policy is not None
        assert policy.enabled is False


def test_restricted_sources_reference_the_project_policy_in_notes() -> None:
    for source_id in _RESTRICTED_SOURCE_IDS:
        policy = default_scraping_source_registry.get(source_id)
        assert policy is not None
        assert policy.notes is not None
        assert "restricted" in policy.notes.lower()


# ---------------------------------------------------------------------------
# Unreviewed (not policy-restricted, but not yet human-approved) sources.
# ---------------------------------------------------------------------------


def test_unreviewed_sources_are_all_registered() -> None:
    for source_id in _UNREVIEWED_SOURCE_IDS:
        assert default_scraping_source_registry.get(source_id) is not None


def test_unreviewed_sources_are_not_approved_for_personal_use() -> None:
    for source_id in _UNREVIEWED_SOURCE_IDS:
        policy = default_scraping_source_registry.get(source_id)
        assert policy is not None
        assert policy.approved_for_personal_use is False
        assert policy.is_unsafe is True
        assert policy.enabled is False


# ---------------------------------------------------------------------------
# active_sources() excludes every unsafe/restricted source -- none of the
# 14 registered sources are active for live fetch in Step 185B.
# ---------------------------------------------------------------------------


def test_active_sources_is_empty_for_the_whole_populated_registry() -> None:
    """'All sources represented' is the goal, not 'all unsafe sources
    active for live fetch.' No live fetcher exists yet, so nothing in
    this registry is a candidate for one."""
    assert default_scraping_source_registry.active_sources() == []


# ---------------------------------------------------------------------------
# Generic fallback policies exist and are honestly distinct from named
# brands (no real site's safety is being asserted for them).
# ---------------------------------------------------------------------------


def test_generic_fallback_policies_exist() -> None:
    for source_id in _GENERIC_SOURCE_IDS:
        policy = default_scraping_source_registry.get(source_id)
        assert policy is not None


def test_generic_fallback_policies_are_not_unsafe() -> None:
    for source_id in _GENERIC_SOURCE_IDS:
        policy = default_scraping_source_registry.get(source_id)
        assert policy is not None
        assert policy.approved_for_personal_use is True
        assert policy.is_unsafe is False


def test_generic_fallback_policies_are_still_disabled() -> None:
    """Safe, but not enabled -- no live fetcher exists yet in this
    codebase for any source, generic included."""
    for source_id in _GENERIC_SOURCE_IDS:
        policy = default_scraping_source_registry.get(source_id)
        assert policy is not None
        assert policy.enabled is False


# ---------------------------------------------------------------------------
# kiwi_manual and kiwi_mcp are distinct concepts.
# ---------------------------------------------------------------------------


def test_kiwi_manual_is_registered_and_distinct_from_kiwi_mcp() -> None:
    policy = default_scraping_source_registry.get("kiwi_manual")
    assert policy is not None
    assert policy.source_id == "kiwi_manual"
    assert "kiwi_mcp" in (policy.notes or "")
    assert "distinct" in (policy.notes or "").lower()


def test_kiwi_manual_module_never_imports_kiwi_mcp_modules() -> None:
    """Checks actual `import`/`from ... import` statements only -- this
    module's own comments legitimately *mention* `kiwi_mcp_adapter.py` in
    prose (to explain how `kiwi_manual` is distinct from it), which must
    not be confused with actually importing it."""
    import app.providers.scraping_source_registry_defaults as registry_module

    source = inspect.getsource(registry_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        assert "kiwi_mcp" not in name.lower()


# ---------------------------------------------------------------------------
# No registered source claims official-provider status or a scraped
# travel fact.
# ---------------------------------------------------------------------------


def test_no_registered_source_has_an_official_provider_claim_field() -> None:
    """`ScrapingSourcePolicy` itself has no `official_provider`-style
    field at all (that lives only on `ScrapedDataProvenance`, which is
    structurally fixed to `False`) -- confirms this registry population
    step didn't add one."""
    assert "official_provider" not in ScrapingSourcePolicy.model_fields


def test_no_registered_source_defines_a_travel_fact_field() -> None:
    forbidden_field_names = {
        "price",
        "rating",
        "availability",
        "booking_url",
        "review_score",
        "verified",
    }
    assert forbidden_field_names.isdisjoint(ScrapingSourcePolicy.model_fields)


# ---------------------------------------------------------------------------
# No policy for a restricted/unreviewed source allows login/CAPTCHA/
# paywall bypass -- every one of them is unsafe via approved_for_personal_
# use=False alone, and none asserts requires_login/paywalled/
# captcha_expected=True as an unverified technical claim.
# ---------------------------------------------------------------------------


def test_no_restricted_or_unreviewed_source_asserts_unverified_bypass_flags() -> None:
    for source_id in _RESTRICTED_SOURCE_IDS | _UNREVIEWED_SOURCE_IDS:
        policy = default_scraping_source_registry.get(source_id)
        assert policy is not None
        assert policy.requires_login is False
        assert policy.paywalled is False
        assert policy.captcha_expected is False
        # is_unsafe is still True -- driven by approved_for_personal_use
        # alone, an honest policy decision rather than an unverified
        # technical claim about the site's own protections.
        assert policy.is_unsafe is True


# ---------------------------------------------------------------------------
# resolve_manual_source_policy: label -> registry entry resolution.
# ---------------------------------------------------------------------------


def test_resolve_manual_source_policy_accommodation_labels() -> None:
    for label, expected_source_id in [
        ("booking", "booking"),
        ("expedia", "expedia"),
        ("hotelbeds", "hotelbeds"),
        ("hostelworld", "hostelworld"),
        ("vrbo", "vrbo"),
        ("airbnb", "airbnb"),
    ]:
        policy = resolve_manual_source_policy("accommodation", label)
        assert policy.source_id == expected_source_id


def test_resolve_manual_source_policy_flight_labels() -> None:
    assert resolve_manual_source_policy("flight", "skyscanner").source_id == "skyscanner"
    assert resolve_manual_source_policy("flight", "google_flights").source_id == "google_flights"
    assert resolve_manual_source_policy("flight", "kiwi").source_id == "kiwi_manual"


def test_resolve_manual_source_policy_hotel_ratings_labels() -> None:
    assert resolve_manual_source_policy("hotel_ratings", "tripadvisor").source_id == "tripadvisor"
    assert (
        resolve_manual_source_policy("hotel_ratings", "google_places").source_id
        == "google_places_ratings"
    )


def test_resolve_manual_source_policy_generic_label_returns_generic_policy() -> None:
    assert resolve_manual_source_policy("accommodation", "generic").source_id == "generic_accommodation"
    assert resolve_manual_source_policy("flight", "generic").source_id == "generic_flight"
    assert resolve_manual_source_policy("flight", "other").source_id == "generic_flight"
    assert resolve_manual_source_policy("hotel_ratings", "generic").source_id == "generic_hotel_ratings"
    assert resolve_manual_source_policy("hotel_ratings", "other").source_id == "generic_hotel_ratings"


def test_resolve_manual_source_policy_unknown_category_falls_back_to_generic_accommodation() -> None:
    policy = resolve_manual_source_policy("not_a_real_category", "anything")
    assert policy.source_id == "generic_accommodation"


def test_resolve_manual_source_policy_unknown_label_falls_back_to_generic() -> None:
    policy = resolve_manual_source_policy("accommodation", "not_a_real_label")
    assert policy.source_id == "generic_accommodation"


def test_resolve_manual_source_policy_never_raises() -> None:
    # Exercises every combination without crashing -- purely a lookup
    # helper, never validation logic that could reject bad input.
    for category in ["accommodation", "flight", "hotel_ratings", "unknown"]:
        for label in ["generic", "booking", "kiwi", "tripadvisor", "nonsense", ""]:
            resolve_manual_source_policy(category, label)


# ---------------------------------------------------------------------------
# Module import safety: no live-fetch/browser-automation dependency, no
# network call anywhere in this module.
# ---------------------------------------------------------------------------


def test_registry_defaults_module_has_no_disallowed_imports() -> None:
    import app.providers.scraping_source_registry_defaults as registry_module

    source = inspect.getsource(registry_module)
    tree = ast.parse(source)

    disallowed_substrings = (
        "httpx",
        "requests",
        "selenium",
        "playwright",
        "bs4",
        "beautifulsoup",
        "groq",
        "anthropic",
        "kiwi_mcp",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        for disallowed in disallowed_substrings:
            assert disallowed not in lowered, f"disallowed import found: {name}"


def test_registry_defaults_module_does_not_import_settings() -> None:
    """No import-time env contamination -- this module must never import
    `app.core.config`, so populating it can never depend on, or be
    affected by, a real `.env`. Checks actual import statements only
    (this module's own comments legitimately *mention* `app.core.config`
    in prose)."""
    import app.providers.scraping_source_registry_defaults as registry_module

    source = inspect.getsource(registry_module)
    tree = ast.parse(source)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        assert "app.core.config" not in name


# ---------------------------------------------------------------------------
# Safety: no source's notes/display text implies a booking confirmation,
# official-provider status, a guarantee, a verified review, or a "top
# rated" claim.
# ---------------------------------------------------------------------------

_BANNED_PHRASES = (
    "confirmed booking",
    "booking confirmation",
    "official provider data",
    "official-provider data",
    "guaranteed",
    "verified review",
    "top rated",
    "best hotels",
    "production-ready",
)


def test_no_source_notes_or_names_contain_banned_overclaiming_phrases() -> None:
    for source in default_scraping_source_registry.all_sources():
        haystack = f"{source.source_name} {source.notes or ''}".lower()
        for phrase in _BANNED_PHRASES:
            assert phrase not in haystack, f"banned phrase {phrase!r} found in source {source.source_id!r}"
