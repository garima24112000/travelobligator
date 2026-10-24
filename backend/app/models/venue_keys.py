"""The venue-status keys a provider record may contribute (M2 Phase 3).

Constants only, so models can validate `venue_tags` without importing a
service. `services/venue_status` reads and documents them."""

from __future__ import annotations

# A record's visitor-facing use lives under one of these keys.
VISITOR_KEYS: tuple[str, ...] = ("tourism", "amenity", "leisure", "shop", "historic")
GONE_PREFIXES: tuple[str, ...] = ("disused", "abandoned", "was", "demolished", "removed", "razed", "destroyed")
NOT_STARTED_PREFIXES: tuple[str, ...] = ("proposed", "construction", "planned")
DATE_KEYS: tuple[str, ...] = ("check_date:opening_hours", "check_date", "survey:date")
PLAIN_KEYS: tuple[str, ...] = ("opening_hours", "disused", "abandoned", *DATE_KEYS)
KEPT_KEYS: tuple[str, ...] = (
    *PLAIN_KEYS,
    *(f"{prefix}:{key}" for prefix in (*GONE_PREFIXES, *NOT_STARTED_PREFIXES) for key in VISITOR_KEYS),
)
