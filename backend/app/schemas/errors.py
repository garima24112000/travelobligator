from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    TRIP_NOT_FOUND = "TRIP_NOT_FOUND"
    PLANNING_STATE_NOT_FOUND = "PLANNING_STATE_NOT_FOUND"
    LOCK_NOT_FOUND = "LOCK_NOT_FOUND"
    REGENERATION_NOT_AVAILABLE = "REGENERATION_NOT_AVAILABLE"
    REGENERATION_BLOCKED_BY_LOCKS = "REGENERATION_BLOCKED_BY_LOCKS"
    REGENERATION_NO_PENDING_FEEDBACK = "REGENERATION_NO_PENDING_FEEDBACK"
    # Section 197C: targeted-regeneration-specific outcomes the legacy
    # coarse path never produced (needs_clarification/version-conflict/
    # provider-unavailable are all honest states the AI interpretation
    # layer can reach that "REGENERATION_NOT_AVAILABLE" alone can't
    # distinguish for a caller).
    REGENERATION_NEEDS_CLARIFICATION = "REGENERATION_NEEDS_CLARIFICATION"
    REGENERATION_CONFLICT = "REGENERATION_CONFLICT"
    REGENERATION_PROVIDER_UNAVAILABLE = "REGENERATION_PROVIDER_UNAVAILABLE"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    PROVIDER_NOT_CONNECTED = "PROVIDER_NOT_CONNECTED"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    AI_OUTPUT_INVALID = "AI_OUTPUT_INVALID"
    STAGE_ALREADY_RUNNING = "STAGE_ALREADY_RUNNING"
    STAGE_FAILED = "STAGE_FAILED"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # Auth foundation (Step 184B) -- not raised by any route yet (no route
    # is auth-gated until Step 184D). Declared now alongside the rest of
    # this enum so app.auth's error constructors (core/errors.py) have a
    # real, tested code to use once they are wired in.
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EMAIL_ALREADY_REGISTERED = "EMAIL_ALREADY_REGISTERED"
    AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
    # Async job foundation (Step 186B) -- not raised by any route yet (no
    # job orchestration exists until Step 186C). Declared now alongside
    # the rest of this enum so a future route/dependency has a real,
    # tested code ready to use, mirroring how AUTHENTICATION_REQUIRED/
    # FORBIDDEN/etc. were added ahead of Step 184D actually wiring them in.
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_ALREADY_RUNNING = "JOB_ALREADY_RUNNING"


class ApiError(BaseModel):
    code: ErrorCode
    field: str | None = None
    message: str
