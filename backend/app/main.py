from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes.auth import router as auth_router
from app.api.routes.trips import router as trips_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging_config import configure_logging
from app.core.request_id_middleware import RequestIdMiddleware
from app.core.response import error_response
from app.schemas.errors import ApiError, ErrorCode
from app.services import generation_job_service

settings = get_settings()

# Step 187B (docs/14_backend_architecture.md section 121): configure the
# stdlib-only structured-logging foundation once, at import time, before
# any route below can run. `configure_logging()` is idempotent -- see its
# own docstring -- so this is safe even if `app.main` is imported more
# than once in a single process (it never is in real usage; Python's
# module cache guarantees that; some test-collection scenarios can still
# re-run module-level code via `importlib.reload`, which this guards
# against). Adds no new logging call site and changes no route/response
# behavior -- only how the process's *existing* `logger.*` calls are
# rendered.
configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Step 186E (docs/14_backend_architecture.md section 118):
    async-job restart recovery, run once per process start, before any
    request is served.

    FastAPI's own `BackgroundTasks` (the only executor this codebase
    uses -- no Redis/Celery/RQ/separate worker process) hold no durable
    state across a process restart. Without this, a job left `queued`/
    `running` by a previous process (crash, redeploy, `--reload` picking
    up a code change, a manual restart) would sit that way forever in
    the persisted local JSON store, permanently blocking every future
    generate/regenerate attempt for its trip via
    `generation_job_service.check_no_duplicate_running_job`.
    `recover_interrupted_jobs()` marks every such job `failed`
    (`JOB_INTERRUPTED`) instead -- it never resumes a job and never
    fabricates a completed plan. A complete no-op, safe on every normal
    startup, when `ASYNC_GENERATION_ENABLED=false` (the default) or when
    no non-terminal jobs happen to be persisted -- with the default
    local_json backend this reads/writes only the already-in-memory job
    repository, never a network call.
    """
    generation_job_service.recover_interrupted_jobs()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.app_debug,
    lifespan=lifespan,
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
    # Step 187G (docs/14_backend_architecture.md section 126): browsers
    # hide every response header from cross-origin `fetch` code by default
    # except a small always-safe allowlist -- `X-Request-Id` is not on
    # it, so without this the frontend could only ever read a failed
    # call's request_id from the JSON body's own `metadata.request_id`
    # (which every response already carries -- see `ResponseMetadata`),
    # never from `response.headers`. Exposing just this one header changes
    # nothing about which origins/credentials/methods/request headers are
    # allowed -- it only makes an already-public, non-sensitive response
    # header (a correlation label, never a token/cookie/secret) readable
    # from frontend JavaScript.
    expose_headers=["X-Request-Id"],
)

# Step 187C (docs/14_backend_architecture.md section 122): added *after*
# CORSMiddleware so it nests inside CORS but outside Starlette's own
# ExceptionMiddleware -- see RequestIdMiddleware's own docstring for why
# that ordering is what lets AppError/RequestValidationError responses
# (not just fully successful ones) carry the same X-Request-Id header
# and share the same id with ResponseMetadata.request_id/every
# structured log line emitted while the route ran.
app.add_middleware(RequestIdMiddleware)


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
app.include_router(auth_router)


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