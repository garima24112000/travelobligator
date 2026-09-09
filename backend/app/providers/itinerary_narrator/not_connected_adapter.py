from __future__ import annotations

from app.models.itinerary_narrative import (
    ItineraryNarrativeReport,
    ItineraryNarrativeRequest,
    ItineraryNarrativeStatus,
)
from app.providers.itinerary_narrator.base import ItineraryNarratorProvider

# Default itinerary narrator adapter (Step 182F). No LLM provider is
# connected yet, so `narrate` always returns an honest `not_connected`
# result -- it never calls a network service, never inspects `request`
# beyond preserving nothing from it, and never produces narrative text.


class NotConnectedItineraryNarratorProvider(ItineraryNarratorProvider):
    """`ItineraryNarratorProvider` implementation used until a real
    LLM-backed adapter is configured and enabled. `narrate` is
    deterministic: it always returns the same empty, `not_connected`
    `ItineraryNarrativeReport`.
    """

    provider_name = "itinerary_narrator_provider"

    def narrate(self, request: ItineraryNarrativeRequest) -> ItineraryNarrativeReport:
        return ItineraryNarrativeReport(
            status=ItineraryNarrativeStatus.NOT_CONNECTED,
            provider=self.provider_name,
            model=None,
            message="Itinerary narrator is not connected.",
        )
