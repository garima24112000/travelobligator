"""Live canary for the narrator's `getting_around_advisory` only (manual, live Groq).

Calls the REAL Groq narrator adapter (production prompt + structured-output
schema) once per city with a minimal one-day request, then runs the REAL
`sanitize_getting_around_advisory` over the returned advisory. No itinerary
pipeline, no Geoapify, no persistence. Nothing here is imported by
`backend/app`.

    cd backend
    python scripts/canary_getting_around_advisory.py

Requires GROQ_API_KEY in the environment (GROQ_MODEL / ITINERARY_NARRATOR_MODEL
optional). Never prints a key, a URL, the prompt or raw model output. The
sanitizer returns only kept/dropped, so no rejection reason is printed.
Exits 1 when fewer than 5 of 6 cities keep an advisory.
"""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.models.itinerary_narrative import (  # noqa: E402
    ItineraryNarrativeDayInput,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
)
from app.providers.itinerary_narrator.groq_adapter import GroqItineraryNarratorProvider  # noqa: E402
from app.services.itinerary_narrative_grounding import (  # noqa: E402
    safe_narrator_message,
    sanitize_getting_around_advisory,
)

CITIES = (
    "New York, USA",
    "London, UK",
    "Tokyo, Japan",
    "Jaipur, India",
    "Lisbon, Portugal",
    "Los Angeles, USA",
)
_REQUIRED_INCLUDED = 5
_REQUIRED_PROFILES = 3


def _request(city: str) -> ItineraryNarrativeRequest:
    start = date.today() + timedelta(days=7)
    return ItineraryNarrativeRequest(
        destination=city,
        start_date=start,
        end_date=start,
        travelers_count=2,
        days=[ItineraryNarrativeDayInput(day_number=1, date=start)],
    )


def _sentence_count(text: str) -> int:
    return len([s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()])


def main() -> int:
    if not get_settings().groq_api_key:
        print("GROQ_API_KEY is not set; not running.")
        return 2

    provider = GroqItineraryNarratorProvider()
    included = 0
    profiles: set[str] = set()
    for city in CITIES:
        request = _request(city)
        report = provider.narrate(request)
        succeeded = report.status == ItineraryNarrativeStatus.SUCCESS
        advisory = sanitize_getting_around_advisory(request, report.getting_around_advisory) if succeeded else None
        # The adapter has already validated the profile against the enum.
        profile = report.getting_around_profile.value if report.getting_around_profile else ""
        if advisory:
            included += 1
            if profile:
                profiles.add(profile)
        text = advisory or ""
        print(city)
        print(f"- model call succeeded: {'yes' if succeeded else 'no'}"
              + ("" if succeeded else f" (status: {report.status.value})"))
        if not succeeded:
            # The production allowlist of fixed, secret-free failure sentences.
            print(f"- failure: {safe_narrator_message(report) or 'none given'}")
        print(f"- selected transport profile: {profile or '(none / not in enum)'}")
        print(f"- advisory included: {'yes' if advisory else 'no'}")
        print(f"- sanitized advisory: {text}")
        print(f"- character count: {len(text)}")
        print(f"- sentence count: {_sentence_count(text) if text else 0}")
        if succeeded and not advisory:
            print(f"- sanitizer: {'dropped' if report.getting_around_advisory else 'model returned no advisory'}")
        print()

    print(f"Included: {included}/{len(CITIES)} (need >= {_REQUIRED_INCLUDED})")
    print(f"Distinct profiles among included: {len(profiles)} {sorted(profiles)} (need >= {_REQUIRED_PROFILES})")
    print("Template/sensibility check is manual: read the advisories above.")
    return 0 if included >= _REQUIRED_INCLUDED and len(profiles) >= _REQUIRED_PROFILES else 1


if __name__ == "__main__":
    raise SystemExit(main())
