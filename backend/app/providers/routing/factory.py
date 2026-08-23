from __future__ import annotations

from app.core.config import get_settings
from app.providers.routing.base import RoutingProvider
from app.providers.routing.not_connected_adapter import NotConnectedRoutingProvider
from app.providers.routing.osrm_adapter import OSRMRoutingAdapter

# Config-gated provider-selection boundary (Step 165A), mirroring
# `app.providers.ai_candidate_proposal.factory`. No LangGraph, Groq,
# Anthropic, or Kiwi/MCP dependency is added here -- this module only
# selects between `RoutingProvider` adapters that already exist. The
# default remains `"not_connected"`.
#
# `"not_connected"` maps to `NotConnectedRoutingProvider`. `"osrm"` maps to
# `OSRMRoutingAdapter`, which is itself safe by default: with no
# `OSRM_BASE_URL` configured it returns an honest `not_connected` result
# instead of calling the network.
#
# Not wired into `ProviderGateway`, `PlanningOrchestrator`,
# `ExperiencePlannerService`, or `PlanValidatorService` yet -- nothing in
# the app calls this factory outside its own tests.

_SUPPORTED_PROVIDERS: dict[str, type[RoutingProvider]] = {
    "not_connected": NotConnectedRoutingProvider,
    "osrm": OSRMRoutingAdapter,
}


def get_routing_provider(provider_name: str | None = None) -> RoutingProvider:
    """Resolves a `RoutingProvider` from `provider_name`, or from
    `Settings.routing_provider` (default `"not_connected"`) when
    `provider_name` is omitted.

    An unsupported/unrecognized provider name can never silently create a
    fake route: it falls back to the same honest `NotConnectedRoutingProvider`
    used when nothing is configured at all, rather than raising or
    guessing. Unknown configuration is functionally identical to "not
    connected," so it is treated identically.
    """
    resolved_name = provider_name if provider_name is not None else get_settings().routing_provider

    provider_cls = _SUPPORTED_PROVIDERS.get(resolved_name, NotConnectedRoutingProvider)
    return provider_cls()
