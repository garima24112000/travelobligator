from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.trips import router as trips_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.response import error_response
from app.schemas.errors import ApiError, ErrorCode

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


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    response = error_response(
        errors=[ApiError(code=exc.code, field=exc.field, message=exc.message)],
        message=exc.message,
    )
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(response))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = [
        ApiError(
            code=ErrorCode.VALIDATION_ERROR,
            field=".".join(str(part) for part in error["loc"][1:]) or None,
            message=error["msg"],
        )
        for error in exc.errors()
    ]
    response = error_response(errors=errors, message="Request validation failed.")
    return JSONResponse(status_code=422, content=jsonable_encoder(response))


app.include_router(trips_router)


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