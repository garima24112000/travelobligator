from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.trips import router as trips_router

app = FastAPI(
    title="TravelObligator API",
    description="Backend API for personalized AI travel itinerary planning.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trips_router, prefix="/api")


@app.get("/")
def root():
    return {
        "message": "TravelObligator API is running",
        "status": "ok",
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
    }
