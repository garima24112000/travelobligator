from fastapi import APIRouter, status

from app.schemas.trips import TripGenerationResponseSchema, TripRequestSchema
from app.services.trip_generation_service import build_mock_itinerary

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post(
    "/generate",
    response_model=TripGenerationResponseSchema,
    status_code=status.HTTP_200_OK,
)
def generate_trip(trip_request: TripRequestSchema) -> TripGenerationResponseSchema:
    return build_mock_itinerary(trip_request)