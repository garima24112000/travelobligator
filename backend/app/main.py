from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.app_debug,
)

cors_origins = [
    origin.strip()
    for origin in settings.backend_cors_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {
        "success": True,
        "data": {
            "status": "ok",
            "app_name": settings.app_name,
            "environment": settings.app_env,
            "use_real_providers": settings.use_real_providers,
            "allow_mock_travel_facts": settings.allow_mock_travel_facts,
        },
        "message": "TravelObligator backend is running.",
        "errors": [],
    }