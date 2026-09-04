from __future__ import annotations

import time
from datetime import datetime
from enum import Enum
from typing import Callable, Literal

from pydantic import BaseModel, Field, model_validator

# Scraping policy and scraped-data provenance foundation (Step 168A,
# docs/12_provider_architecture.md, docs/13_llm_reasoning_pipeline.md,
# docs/14_backend_architecture.md). This is a contract only:
#
# - No live scraper exists. No real external HTML is ever parsed. No
#   website is ever called by this module.
# - Nothing here is wired into `ProviderGateway`, `PlanningOrchestrator`,
#   any stage service, or the frontend yet.
# - No real Booking/Expedia/Hotelbeds/Hostelworld/Amadeus/Vrbo/Airbnb
#   integration is added, called, or implied.
#
# Scraping is disabled by default and stays that way until a source is
# both explicitly enabled *and* passes every safety check below -- see
# `ScrapingSourcePolicy.is_unsafe` and `ScrapingSourceRegistry.active_sources`.
# Scraped data is never official-provider data (`ScrapedDataProvenance.
# official_provider` can only ever be `False`), must be labeled
# `scraped_public_page`/`experimental`/`fragile`, must never silently
# overwrite official provider-backed data, and every optional fact field
# on a future scraped item must stay `None`/empty unless a real, approved
# scrape actually returned it -- this module defines no such fact fields
# itself (no price, rating, availability, amenity, opening-hours, route
# time, or safety-claim field exists here), so there is nothing for a
# default to fabricate.


class ScrapingSourceType(str, Enum):
    SCRAPED_PUBLIC_PAGE = "scraped_public_page"


class ScrapedDataConfidence(str, Enum):
    EXPERIMENTAL = "experimental"
    FRAGILE = "fragile"


class ScrapingExtractionMethod(str, Enum):
    STATIC_HTML_PARSER = "static_html_parser"
    MANUAL_LOCAL_SCRAPER = "manual_local_scraper"
    UNKNOWN = "unknown"


class ScrapingSourcePolicy(BaseModel):
    """One explicitly-approved (or explicitly-not-yet-approved) scraping
    source. Construction itself enforces the core safety rule: a source
    can never validate as `enabled=True` while it is unsafe (see
    `is_unsafe`) -- there is no code path that can silently activate a
    login-required, paywalled, captcha-expected, or not-personally-
    approved source.

    `rate_limit_seconds` has no default -- every source must state an
    explicit, positive rate limit rather than inheriting a silently
    permissive one, matching the "no aggressive crawling" policy rule.
    """

    source_id: str = Field(min_length=1, max_length=160)
    source_name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    source_type: ScrapingSourceType = ScrapingSourceType.SCRAPED_PUBLIC_PAGE

    enabled: bool = False
    approved_for_personal_use: bool = False

    allows_lodging: bool = False
    allows_restaurants: bool = False
    allows_attractions: bool = False
    # Step 169C -- gates the flight parser (`app.providers.flights.
    # scraped_parser.parse_scraped_flight_html`) the same way
    # `allows_lodging` gates the accommodation parser. Defaults to
    # `False`, matching every other `allows_*` flag: no existing source
    # policy silently gains flight-scraping permission just because this
    # field was added.
    allows_flights: bool = False

    requires_login: bool = False
    paywalled: bool = False
    captcha_expected: bool = False

    rate_limit_seconds: int = Field(gt=0)
    notes: str | None = Field(default=None, max_length=2000)

    @property
    def is_unsafe(self) -> bool:
        """True if this source must never be scraped, regardless of
        `enabled` -- login-required, paywalled, and captcha-expected pages
        are never scraped by this app (bot-detection/captcha bypass is
        never implemented), and a source the user hasn't explicitly
        approved for personal use is never scraped either.
        """
        return (
            self.requires_login
            or self.paywalled
            or self.captcha_expected
            or not self.approved_for_personal_use
        )

    @model_validator(mode="after")
    def validate_enabled_source_is_safe(self) -> "ScrapingSourcePolicy":
        if self.enabled and self.is_unsafe:
            raise ValueError(
                f"ScrapingSourcePolicy '{self.source_id}' cannot be enabled: it is "
                "unsafe (requires_login, paywalled, or captcha_expected is True, "
                "or approved_for_personal_use is not True)."
            )
        return self


class ScrapedDataProvenance(BaseModel):
    """Provenance metadata a future scraped item would carry alongside its
    real fields -- this model itself has no price/rating/availability/
    amenity/booking-link/opening-hours/route-time/safety-claim field, so
    there is nothing here for a default to fabricate.

    `official_provider` is typed `Literal[False]` -- not just defaulted to
    `False` -- so no scraped item can ever be constructed claiming to be
    official-provider data; pydantic itself rejects `True` at validation
    time, the same way `GenerationProgress.is_real_backend_stage_progress`
    is a fixed safety marker rather than a mutable flag.
    """

    source_id: str = Field(min_length=1, max_length=160)
    source_name: str = Field(min_length=1, max_length=200)
    source_type: ScrapingSourceType = ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    provenance: ScrapingSourceType = ScrapingSourceType.SCRAPED_PUBLIC_PAGE
    confidence: ScrapedDataConfidence
    fetched_at: datetime | None = None
    parser_version: str | None = Field(default=None, max_length=100)
    source_url: str | None = Field(default=None, max_length=1000)
    extraction_method: ScrapingExtractionMethod = ScrapingExtractionMethod.UNKNOWN
    official_provider: Literal[False] = False


class ScrapingSourceRegistry:
    """Holds a fixed set of `ScrapingSourcePolicy` entries in memory.

    No source is registered by default (`default_scraping_source_registry`
    below starts empty) -- nothing is approved for scraping out of the
    box, and this step adds no source. `active_sources` is the only
    method a future scraper should ever consult before fetching anything;
    it re-checks `is_unsafe` defensively even though `ScrapingSourcePolicy`
    already refuses to validate as `enabled=True` while unsafe, so a
    registry holding sources built or mutated by some other path still
    can never return an unsafe source as active.
    """

    def __init__(self, sources: list[ScrapingSourcePolicy] | None = None) -> None:
        self._sources: dict[str, ScrapingSourcePolicy] = {
            source.source_id: source for source in (sources or [])
        }

    def all_sources(self) -> list[ScrapingSourcePolicy]:
        return list(self._sources.values())

    def get(self, source_id: str) -> ScrapingSourcePolicy | None:
        return self._sources.get(source_id)

    def active_sources(self) -> list[ScrapingSourcePolicy]:
        """Sources that are both explicitly `enabled=True` and safe.
        Empty by default, and empty whenever no registered source is both
        enabled and safe -- never a fallback list, never a guessed
        approval.
        """
        return [
            source
            for source in self._sources.values()
            if source.enabled and not source.is_unsafe
        ]

    def register(self, source: ScrapingSourcePolicy) -> None:
        """Adds or replaces one source by `source_id`. Registering a
        source never implies scraping starts -- only `active_sources()`
        consulted by a future scraper (none exists yet) would ever act on
        it, and only if that source is also safe.
        """
        self._sources[source.source_id] = source


# Empty by default -- no source is approved for scraping out of the box.
default_scraping_source_registry = ScrapingSourceRegistry()


class ScrapingRateLimitGuard:
    """Deterministic in-process guard enforcing a minimum gap between two
    scrape attempts for the same source, keyed by `source_id` (Step 168D,
    docs/12_provider_architecture.md, docs/14_backend_architecture.md).

    This is foundation for a *future* live scraper adapter -- no live
    fetching exists anywhere in this codebase yet, and nothing currently
    calls this guard outside its own tests. It purely tracks timing: it
    has no knowledge of `ScrapingSourcePolicy.is_unsafe`/`enabled` and
    cannot grant permission to scrape anything. A caller must still
    perform its own safety checks (mirroring `ScrapingSourceRegistry.
    active_sources`/the parser's own `_refusal_reason`) before ever
    calling `wait_if_needed` -- this guard only ever adds a wait, never
    removes a safety requirement.

    `clock`/`sleep` are injectable so tests can exercise real waiting
    behavior deterministically without a real `time.sleep` call --
    defaulting to `time.monotonic`/`time.sleep` in production use.
    """

    def __init__(
        self,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._last_attempt_at: dict[str, float] = {}

    def wait_if_needed(self, source_policy: ScrapingSourcePolicy) -> float:
        """Waits (via the injected `sleep`) just long enough that at least
        `source_policy.rate_limit_seconds` has elapsed since the last
        attempt recorded for this exact `source_id`, then records this
        attempt's time. Returns the number of seconds actually waited
        (`0.0` when no wait was needed, e.g. the first attempt for a
        source, or enough real time has already passed).
        """
        now = self._clock()
        last_attempt_at = self._last_attempt_at.get(source_policy.source_id)
        wait_seconds = 0.0

        if last_attempt_at is not None:
            elapsed = now - last_attempt_at
            remaining = source_policy.rate_limit_seconds - elapsed
            if remaining > 0:
                wait_seconds = remaining
                self._sleep(wait_seconds)
                now = self._clock()

        self._last_attempt_at[source_policy.source_id] = now
        return wait_seconds

    def reset(self, source_id: str | None = None) -> None:
        """Clears recorded attempt timing for one `source_id`, or every
        source when `source_id` is `None`. Never used to bypass a safety
        check -- only to forget stale timing state (e.g. between test
        cases)."""
        if source_id is None:
            self._last_attempt_at.clear()
        else:
            self._last_attempt_at.pop(source_id, None)
