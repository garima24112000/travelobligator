from __future__ import annotations

from app.schemas.trips import TransportStrategySchema


def get_mock_transport_strategy(pace: str, destination: str) -> TransportStrategySchema:
    city = destination.title()
    if pace == "packed":
        local_transport = f"Mock demo private rides and short taxi hops around {city}."
        rationale = [
            "Packed pacing favors the lowest-friction local movement.",
            "Mock/demo strategy prioritizes time efficiency over cost.",
        ]
    elif pace == "relaxed":
        local_transport = f"Mock demo public transport plus walking in {city}."
        rationale = [
            "Relaxed pacing supports slower movement and longer stops.",
            "Mock/demo strategy keeps the day flexible and budget-friendly.",
        ]
    else:
        local_transport = f"Mock demo mix of public transport and taxis in {city}."
        rationale = [
            "Balanced pacing mixes convenience and cost control.",
            "Mock/demo strategy keeps movement realistic without overfitting.",
        ]

    return TransportStrategySchema(
        localTransport=local_transport,
        intercityTransport="Mock demo rail or flight connection if needed.",
        rationale=rationale,
    )
