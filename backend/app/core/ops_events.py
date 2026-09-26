"""Operational event taxonomy + emit helper (Section 200D).

One vocabulary for infrastructure behaviour, emitted through the EXISTING structured-logging
pipeline (`app.core.logging_config`: JSON lines, request-id filter, field allowlist, redaction) --
no second logging system. Each event is a stable dotted name in the `event` field; everything else
is a small allowlisted field. Never put user content (feedback text, itinerary/narrative text, AI
prompts or outputs, provider payloads), tokens, URLs or secrets in an event.

Levels (avoid floods): high-volume normal traffic is DEBUG (cache hit/miss/bypass, transaction
commit); meaningful lifecycle transitions are INFO (job.created/claimed/succeeded, persistence.ready);
degraded dependencies, conflicts and retries are WARNING; unexpected failures are ERROR.
"""

from __future__ import annotations

import logging
from typing import Any

# persistence
PERSISTENCE_READY = "persistence.ready"
PERSISTENCE_UNAVAILABLE = "persistence.unavailable"
PERSISTENCE_SCHEMA_MISMATCH = "persistence.schema_mismatch"
# cache (200B outcomes)
CACHE_HIT = "cache.hit"
CACHE_MISS = "cache.miss"
CACHE_BYPASS = "cache.bypass"
CACHE_ERROR = "cache.error"
# transactions (200C)
TRANSACTION_COMMITTED = "transaction.committed"
TRANSACTION_ROLLBACK = "transaction.rollback"
TRANSACTION_CONFLICT = "transaction.conflict"
TRANSACTION_RETRY = "transaction.retry"
TRANSACTION_DEADLOCK = "transaction.deadlock"
# jobs
JOB_CREATED = "job.created"
JOB_CLAIMED = "job.claimed"
JOB_HEARTBEAT_FAILED = "job.heartbeat_failed"
JOB_SUCCEEDED = "job.succeeded"
JOB_FAILED = "job.failed"
JOB_INTERRUPTED = "job.interrupted"
JOB_CLAIM_CONFLICT = "job.claim_conflict"
JOB_CREATE_CONFLICT = "job.create_conflict"
JOB_STALE_OWNER_REJECTED = "job.stale_owner_rejected"
# providers
PROVIDER_SUCCESS = "provider.success"
PROVIDER_FAILURE = "provider.failure"
# process lifecycle
APP_STARTUP = "app.startup"
APP_SHUTDOWN = "app.shutdown"
READINESS_CHANGED = "readiness.changed"

ALL_EVENTS = frozenset(
    v for k, v in globals().items() if k.isupper() and isinstance(v, str) and "." in v
)


def log_event(logger: logging.Logger, level: int, event: str, message: str, /, **fields: Any) -> None:
    """Emit `message` with `event` + allowlisted `fields`. Never raises."""
    try:
        logger.log(level, message, extra={"event": event, **fields}, stacklevel=2)
    except Exception:  # noqa: BLE001 - logging must never break a request
        return
