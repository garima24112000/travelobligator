from __future__ import annotations

from app.models.common import ProviderStatus
from app.models.routing import RouteRequest, RouteResult
from app.providers.routing.base import RoutingProvider

# Default routing adapter (Step 165A). No routing provider is connected
# yet, so `get_route` always returns an honest `not_connected` result -- it
# never calls a network service, never inspects `request` beyond echoing
# nothing back from it, and never invents a distance or duration.


class NotConnectedRoutingProvider(RoutingProvider):
    """`RoutingProvider` implementation used until a real routing adapter
    is configured. `get_route` is deterministic: given any request, it
    always returns the same `not_connected` `RouteResult`.
    """

    provider_name = "routing_provider"

    def get_route(self, request: RouteRequest) -> RouteResult:
        return RouteResult(
            provider=self.provider_name,
            status=ProviderStatus.NOT_CONNECTED,
            distance_meters=None,
            duration_seconds=None,
            geometry=None,
            source=self.provider_name,
            confidence=0.0,
            message="Routing provider is not connected.",
        )
