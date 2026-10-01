import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.auth import router as auth_router
from app.api.routes.ops import router as ops_router
from app.api.routes.trips import router as trips_router
from app.core.config import get_settings
from app.core.errors import CONCURRENT_UPDATE_MESSAGE, AppError
from app.core.logging_config import configure_logging
from app.core.http_metrics_middleware import HttpMetricsMiddleware
from app.core.no_store_middleware import NoStoreMiddleware
from app.core.operational_config import cors_origins as configured_cors_origins
from app.core.request_id_middleware import RequestIdMiddleware
from app.core.response import error_response
from app.repositories.errors import ConcurrentStateUpdateError
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
    # Section 200D: contradictory/impossible configuration fails EARLY with a fixed message.
    from app.core import ops_events
    from app.core.metrics import registry
    from app.core.operational_config import (
        startup_config_summary,
        validate_runtime_configuration,
    )
    from app.core.persistence import alembic_head_revisions

    validate_runtime_configuration(get_settings())
    registry.enabled = get_settings().metrics_enabled
    # Section 200A: PostgreSQL is the default persistence backend. Verify the
    # configuration, connectivity and migration head BEFORE serving any
    # request; on failure the app refuses to start (with a credential-free
    # message) and NEVER falls back to Local JSON. `local_json` (explicit
    # dev/test fallback) has nothing to verify.
    from app.core.persistence import startup_persistence_check

    startup_persistence_check(get_settings())
    # Section 200B: Redis is only the provider-response cache. A malformed REDIS_URL
    # stops startup; an unreachable Redis does not (calls run uncached, status is
    # "degraded"). Never a hidden SQLite fallback.
    from app.core.provider_cache import shutdown_provider_cache, startup_provider_cache_check

    startup_provider_cache_check(get_settings())
    generation_job_service.recover_interrupted_jobs()
    try:
        heads = alembic_head_revisions()
        head = next(iter(heads)) if len(heads) == 1 else None
    except Exception:  # noqa: BLE001
        head = None
    # The ONLY configuration logged: a fixed set of safe scalars (never the settings object).
    ops_events.log_event(
        logging.getLogger("app.lifecycle"),
        logging.INFO,
        ops_events.APP_STARTUP,
        "Application started.",
        **startup_config_summary(get_settings(), head),
    )
    try:
        yield
    finally:
        # Section 200D graceful shutdown (short, bounded; full SIGTERM/container behaviour is 200E):
        # stop job heartbeat threads, close the Redis client, dispose the probe + request engines.
        stopped = generation_job_service.stop_all_heartbeats(1.0)
        # Jobs this process still owns are closed honestly as JOB_INTERRUPTED (lease-conditional), not left
        # `running` until the lease expires and never reported as succeeded.
        generation_job_service.interrupt_local_jobs()
        shutdown_provider_cache()
        from app.core.readiness import reset_readiness_engines

        reset_readiness_engines()
        try:
            from app.db.session import get_engine

            if get_settings().persistence_backend == "postgres":
                get_engine().dispose()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            pass
        ops_events.log_event(
            logging.getLogger("app.lifecycle"),
            logging.INFO,
            ops_events.APP_SHUTDOWN,
            "Application stopped.",
            retry_count=stopped,
        )


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.app_debug,
    lifespan=lifespan,
)

# Explicit origins only (`validate_runtime_configuration` rejects a wildcard/credentialed/non-origin
# entry at startup). CORS is not authentication: every route still checks the session itself.
cors_origins = configured_cors_origins(settings)

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
app.add_middleware(HttpMetricsMiddleware)
app.add_middleware(RequestIdMiddleware)
# Section 203B: outermost, so EVERY response (routes, error handlers, CORS preflights) is `no-store` --
# the production browser path is a CDN rewrite (Vercel -> this service) and nothing here is cacheable.
app.add_middleware(NoStoreMiddleware)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    response = error_response(
        errors=[ApiError(code=exc.code, field=exc.field, message=exc.message)],
        message=exc.message,
    )
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(response))


@app.exception_handler(ConcurrentStateUpdateError)
async def concurrent_update_handler(request: Request, exc: ConcurrentStateUpdateError) -> JSONResponse:
    """Section 200C: a stale writer was rejected by the database (optimistic concurrency /
    branch-head compare-and-set). HTTP 409 with ONE fixed message -- never SQL, versions or
    row details; only the exception class name is logged."""
    logging.getLogger("app.persistence").info(
        "Rejected a stale write.", extra={"error_class": type(exc).__name__}
    )
    response = error_response(
        errors=[ApiError(code=ErrorCode.CONCURRENT_UPDATE, field=None, message=CONCURRENT_UPDATE_MESSAGE)],
        message=CONCURRENT_UPDATE_MESSAGE,
    )
    return JSONResponse(status_code=409, content=jsonable_encoder(response))


@app.exception_handler(SQLAlchemyError)
async def persistence_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Section 200A: a database failure during a request is reported with ONE fixed,
    credential-free message (HTTP 503). The exception text is never returned or
    logged (driver errors can echo host/user/database) -- only its class name --
    and persistence is never silently switched to another backend."""
    logging.getLogger("app.persistence").error(
        "Database error while handling a request.", extra={"error_class": type(exc).__name__}
    )
    message = "The database is temporarily unavailable. Please try again shortly."
    response = error_response(
        errors=[ApiError(code=ErrorCode.PERSISTENCE_UNAVAILABLE, field=None, message=message)],
        message=message,
    )
    return JSONResponse(status_code=503, content=jsonable_encoder(response))


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
app.include_router(ops_router)
