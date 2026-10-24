"""Venue status from the provider's own record (M2 Phase 3, Workstream A).

What the providers actually supply (checked 2026-10-24): Geoapify documents
no operational-status field and no "closed" condition. The underlying
OpenStreetMap record, which Geoapify Places and the development Overpass
adapter both return, can carry:

  * lifecycle prefixes -- `disused:amenity=theatre`, `was:tourism=museum`,
    `abandoned:shop=*`, `demolished:*`, `razed:*`, `removed:*`, `destroyed:*`
    (the use is over) and `proposed:*`, `construction:*`, `planned:*` (it has
    not started) -- and the bare `disused=yes` / `abandoned=yes`;
  * `opening_hours`, including the whole-value `off` / `closed`;
  * `check_date` / `check_date:opening_hours`: when a mapper last confirmed it.

`venue_evidence_tags` keeps exactly those keys (nothing else -- no name,
phone, website or address), beside the taxonomy's own tags. `venue_status`
reads them. It separates what the record STATES from what is unknown:

  * `not_operational` -- the record's visitor-facing use carries a "use is over"
    lifecycle prefix and no current use, or a bare disused/abandoned flag on a
    service (amenity, shop, leisure);
  * `not_yet_open` -- the only visitor-facing use is a "not started" prefix;
  * `listed_closed` -- the whole opening_hours value is `off` / `closed`
    (optionally with a quoted comment, e.g. "temporarily closed");
  * `unknown` -- everything else, INCLUDING a record with no such tag at all.

A place record existing is never evidence that the place is open, and nothing
here says "open": the best this module can say is that nothing in the record
reports it closed. A record's statement is still a mapper's statement, which
may be outdated; the status keeps the tag keys it rests on and the record's
check date, when there is one, so that can be judged. Names are never read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# The one whitelist, shared with the models (`app/models/venue_keys.py`).
from app.models.venue_keys import DATE_KEYS as _DATE_KEYS
from app.models.venue_keys import GONE_PREFIXES, KEPT_KEYS, NOT_STARTED_PREFIXES, VISITOR_KEYS

NOT_OPERATIONAL = "not_operational"
NOT_YET_OPEN = "not_yet_open"
LISTED_CLOSED = "listed_closed"
UNKNOWN = "unknown"

# Statuses that keep a place out of the schedule (a must-visit is kept and disclosed).
EXCLUDING_STATUSES = frozenset({NOT_OPERATIONAL, NOT_YET_OPEN, LISTED_CLOSED})

# Services: a bare disused/abandoned flag on one of these means the service is
# gone. On `tourism` / `historic` it often describes the visitable thing itself
# (an abandoned mill as a sight), so it is not read there.
_SERVICE_KEYS = frozenset({"amenity", "shop", "leisure"})
_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
# The whole opening_hours value says closed, optionally with a quoted comment
# (`closed "temporarily closed"`): the comment never reopens it.
_WHOLE_VALUE_CLOSED = re.compile(r'^(?:off|closed)(?:\s+"[^"]*")?$', re.IGNORECASE)
_MAX_VALUE_LENGTH = 255


def venue_evidence_tags(raw: Any) -> dict[str, str]:
    """The venue-status keys of a raw OpenStreetMap tag set, as strings
    (empty when there is none). A date key is kept only in a date form."""
    if not isinstance(raw, dict):
        return {}
    kept: dict[str, str] = {}
    for key in KEPT_KEYS:
        value = raw.get(key)
        if value in (None, ""):
            continue
        text = str(value).strip()[:_MAX_VALUE_LENGTH]
        if key in _DATE_KEYS and not _DATE.match(text):
            continue
        kept[key] = text
    return kept


@dataclass(frozen=True)
class VenueStatus:
    status: str
    # The tag keys the status rests on (never their values' free text).
    evidence: tuple[str, ...] = field(default_factory=tuple)
    # The record's own check date, when it has one ("YYYY", "YYYY-MM" or "YYYY-MM-DD").
    checked_on: str | None = None


def _yes(value: Any) -> bool:
    return str(value or "").strip().lower() == "yes"


def checked_on(venue_tags: dict[str, Any] | None, *, hours: bool = False) -> str | None:
    tags = venue_tags or {}
    keys = ("check_date:opening_hours", "check_date", "survey:date") if hours else ("check_date", "survey:date")
    return next((str(tags[key]) for key in keys if tags.get(key)), None)


def venue_status(provider_tags: dict[str, Any] | None, venue_tags: dict[str, Any] | None) -> VenueStatus:
    """What the provider's record states about whether the place operates.
    `provider_tags` are the taxonomy tags (the current uses); `venue_tags`
    the lifecycle / hours keys of `venue_evidence_tags`."""
    current = provider_tags or {}
    venue = venue_tags or {}
    date = checked_on(venue)
    current_uses = [key for key in VISITOR_KEYS if current.get(key)]

    gone = [f"{p}:{k}" for p in GONE_PREFIXES for k in VISITOR_KEYS if venue.get(f"{p}:{k}")]
    if gone and not current_uses:
        return VenueStatus(NOT_OPERATIONAL, tuple(gone), date)
    flags = [flag for flag in ("disused", "abandoned") if _yes(venue.get(flag))]
    if flags and current_uses and all(key in _SERVICE_KEYS for key in current_uses):
        return VenueStatus(NOT_OPERATIONAL, tuple(flags), date)
    not_started = [f"{p}:{k}" for p in NOT_STARTED_PREFIXES for k in VISITOR_KEYS if venue.get(f"{p}:{k}")]
    if not_started and not current_uses:
        return VenueStatus(NOT_YET_OPEN, tuple(not_started), date)
    if _WHOLE_VALUE_CLOSED.match(str(venue.get("opening_hours") or "").strip()):
        return VenueStatus(LISTED_CLOSED, ("opening_hours",), checked_on(venue, hours=True))
    return VenueStatus(UNKNOWN, (), date)


def status_of_poi(poi: dict[str, Any]) -> VenueStatus:
    return venue_status(poi.get("provider_tags"), poi.get("venue_tags"))
