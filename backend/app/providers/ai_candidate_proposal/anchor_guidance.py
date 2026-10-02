"""Shared prompt text for bounded semantic anchor discovery (Section
203C.2B), used by every AI candidate proposal adapter.

The factual provider returns a broad pool of real places in NO particular
order of importance. The model's job is the semantic one a provider
listing cannot do: name the places a visitor to this destination would
most likely want, across a spread of kinds. Every name is a hypothesis --
it becomes eligible only after a provider grounds it to a real place
inside the destination, with the provider's own identity and coordinates.
"""

from __future__ import annotations

ANCHOR_DISCOVERY_GUIDANCE = (
    "Your main job is anchor discovery: propose the specific, well-known named places a "
    "visitor to this destination would most likely want to see, sized to the trip length "
    "and matched to the traveler's interests, constraints and must-visit requests. Use "
    "named_place for these, with the place's commonly used name as candidate_name and a "
    "short search_query containing that name. Cover a spread of kinds where the "
    "destination genuinely has them: landmark, historic site, architecture, museum, "
    "park or garden, viewpoint, market or neighbourhood, cultural venue, and a food-area "
    "anchor. Do not fill the list with one kind, and do not propose minor objects "
    "(individual statues, plaques, fountains, small memorials).\n\n"
    "The provider's own pool is broad but unranked, so do propose important places even "
    "if a provider may already list them -- your proposals are matched against that pool "
    "and deduplicated. A place you name is only a hypothesis: it is used only if a "
    "provider finds a real place with that name inside the destination. Never invent a "
    "name to fill the list; propose fewer when you are unsure."
)

PROVIDER_POOL_NOTE = (
    "Real places a provider already returned (counts only; broad, in no order of importance): "
)
