from __future__ import annotations

import ast
import inspect

import pytest

from app.core.config import Settings
from app.models.common import ProviderStatus
from app.models.routing import RouteRequest
from app.providers.routing import NotConnectedRoutingProvider, get_routing_provider
from app.providers.routing import factory as factory_module
from app.providers.routing.osrm_adapter import OSRMRoutingAdapter

# Safety tests for the Step 165A routing provider factory. Mirrors
# test_ai_candidate_proposal_factory.py. Never calls a real network
# service; OSRM adapter behavior itself is covered separately in
# test_osrm_adapter.py.


def _request() -> RouteRequest:
    return RouteRequest(
        origin_lat=38.7223,
        origin_lon=-9.1393,
        destination_lat=38.7169,
        destination_lon=-9.1399,
    )


# ---------------------------------------------------------------------------
# Factory returns NotConnectedRoutingProvider by default.
# ---------------------------------------------------------------------------


def test_factory_returns_not_connected_provider_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROUTING_PROVIDER", raising=False)
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(_env_file=None))

    provider = get_routing_provider()

    assert isinstance(provider, NotConnectedRoutingProvider)


def test_factory_returns_not_connected_provider_for_explicit_name() -> None:
    provider = get_routing_provider("not_connected")
    assert isinstance(provider, NotConnectedRoutingProvider)


# ---------------------------------------------------------------------------
# Factory behavior for unsupported provider names is safe.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsupported_name", ["google_routes", "made_up_provider", "", "NOT_CONNECTED", "mapbox"]
)
def test_factory_falls_back_to_not_connected_for_unsupported_names(unsupported_name: str) -> None:
    provider = get_routing_provider(unsupported_name)

    assert isinstance(provider, NotConnectedRoutingProvider)
    result = provider.get_route(_request())
    assert result.status == ProviderStatus.NOT_CONNECTED
    assert result.distance_meters is None
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# "osrm" is a supported, non-default provider name.
# ---------------------------------------------------------------------------


def test_factory_returns_osrm_provider_for_explicit_osrm() -> None:
    provider = get_routing_provider("osrm")
    assert isinstance(provider, OSRMRoutingAdapter)


def test_factory_uses_settings_when_provider_is_osrm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory_module, "get_settings", lambda: Settings(ROUTING_PROVIDER="osrm"))
    provider = get_routing_provider()
    assert isinstance(provider, OSRMRoutingAdapter)


def test_osrm_provider_from_factory_is_not_connected_without_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when explicitly selected, the OSRM adapter itself stays
    conservative -- no base URL means no network call and an honest
    not_connected result (Step 165A's "OSRM live usage explicit via
    config" default)."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.delenv("OSRM_BASE_URL", raising=False)

    import app.providers.routing.osrm_adapter as osrm_adapter_module

    monkeypatch.setattr(
        osrm_adapter_module, "get_settings", lambda: Settings(_env_file=None, osrm_base_url=None)
    )

    provider = get_routing_provider("osrm")
    result = provider.get_route(_request())

    assert result.status == ProviderStatus.NOT_CONNECTED


# ---------------------------------------------------------------------------
# Config default is "not_connected".
# ---------------------------------------------------------------------------


def test_settings_default_routing_provider_is_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROUTING_PROVIDER", raising=False)
    settings = Settings(_env_file=None)
    assert settings.routing_provider == "not_connected"


def test_settings_reads_routing_provider_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTING_PROVIDER", "some_future_provider")
    settings = Settings()
    assert settings.routing_provider == "some_future_provider"


def test_factory_uses_settings_when_no_provider_name_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        factory_module,
        "get_settings",
        lambda: Settings(_env_file=None, routing_provider="not_connected"),
    )
    provider = get_routing_provider()
    assert isinstance(provider, NotConnectedRoutingProvider)


def test_factory_falls_back_when_settings_hold_unsupported_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        factory_module,
        "get_settings",
        lambda: Settings(_env_file=None, routing_provider="google_routes"),
    )
    provider = get_routing_provider()
    assert isinstance(provider, NotConnectedRoutingProvider)


# ---------------------------------------------------------------------------
# Factory module import safety.
# ---------------------------------------------------------------------------


def test_factory_module_has_no_disallowed_imports() -> None:
    source = inspect.getsource(factory_module)
    tree = ast.parse(source)

    vendor_disallowed_substrings = (
        "langgraph",
        "langsmith",
        "groq",
        "anthropic",
        "openai",
        "gemini",
        "google.generativeai",
        "kiwi",
        "mcp",
    )
    internal_disallowed_substrings = (
        "app.providers.places",
        "app.providers.weather",
        "app.providers.holidays",
        "app.providers.currency",
        "app.providers.gateway",
    )

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    for name in imported_names:
        lowered = name.lower()
        if not lowered.startswith("app."):
            for disallowed in vendor_disallowed_substrings:
                assert disallowed not in lowered, f"Disallowed import found: {name}"
        for disallowed in internal_disallowed_substrings:
            assert disallowed not in lowered, f"Disallowed import found: {name}"


# ---------------------------------------------------------------------------
# 10. As of Step 165B, ProviderGateway exposes routing through the factory
#     (`self.routing` + `get_route`) -- but nothing in the actual planning
#     pipeline (PlanningOrchestrator, ExperiencePlannerService,
#     PlanValidatorService) calls it yet.
# ---------------------------------------------------------------------------


def test_provider_gateway_wires_routing_factory() -> None:
    """Step 165B: `ProviderGateway` now imports and uses the routing
    factory -- this is the intentional wiring point for this step, unlike
    Step 165A where the gateway didn't reference routing at all."""
    import app.providers.gateway as gateway_module

    source = inspect.getsource(gateway_module)
    assert "get_routing_provider" in source
    assert "self.routing" in source
    assert "def get_route(" in source
    # Still no OSRM URL/timeout/profile detail leaks into the gateway's own
    # code -- that stays entirely inside the factory/adapter. The word
    # "osrm_base_url" may still appear in an explanatory docstring comment
    # (stating that the gateway does *not* know it) -- only an actual
    # settings/attribute read is disallowed.
    assert "settings.osrm_base_url" not in source
    assert "settings.osrm_timeout" not in source
    assert "self._base_url" not in source


def test_provider_gateway_default_routing_provider_is_not_connected() -> None:
    """Constructing `ProviderGateway()` with no explicit `routing=` must
    resolve the same conservative default the factory itself uses --
    confirming the gateway wiring didn't change the underlying default."""
    from app.providers.gateway import ProviderGateway
    from app.providers.routing import NotConnectedRoutingProvider

    gateway = ProviderGateway()

    assert isinstance(gateway.routing, NotConnectedRoutingProvider)


def test_planning_orchestrator_does_not_reference_routing_module() -> None:
    import app.services.planning_orchestrator as orchestrator_module

    source = inspect.getsource(orchestrator_module)
    assert "osrm" not in source.lower()
    assert "routingprovider" not in source.lower()
    assert "get_routing_provider" not in source


def test_experience_planner_does_not_reference_routing_module() -> None:
    import app.services.experience_planner_service as experience_planner_module

    source = inspect.getsource(experience_planner_module)
    assert "osrm" not in source.lower()
    assert "routingprovider" not in source.lower()
    assert "get_routing_provider" not in source
    assert "gateway.routing" not in source.lower()
    assert "gateway.get_route" not in source.lower()


def test_plan_validator_does_not_reference_routing_module() -> None:
    import app.services.plan_validator_service as plan_validator_module

    source = inspect.getsource(plan_validator_module)
    assert "osrm" not in source.lower()
    assert "routingprovider" not in source.lower()
    assert "get_routing_provider" not in source
    assert "gateway.routing" not in source.lower()
    assert "gateway.get_route" not in source.lower()
