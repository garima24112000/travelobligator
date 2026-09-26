"""Typed `/ready` response (Section 200D).

Only operationally SAFE values appear here: fixed status enums, the backend NAME, and Alembic
revision identifiers (a 12-hex-character migration id -- deployment metadata, not a secret; a test
asserts nothing but the identifier is exposed). Never a URL, host, username, password, file path,
SQL, traceback or provider key.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ReadinessStatus(str, Enum):
    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"


class PersistenceCheck(BaseModel):
    status: str  # ready | unavailable
    backend: str  # postgres | local_json


class SchemaCheck(BaseModel):
    status: str  # ok | mismatch | unknown | not_applicable
    expected_head: str | None = None
    current_head: str | None = None


class ProviderCacheCheck(BaseModel):
    status: str  # healthy | recovering | degraded | disabled | local
    backend: str  # redis | sqlite | none


class ReadinessChecks(BaseModel):
    persistence: PersistenceCheck
    # Serialized as "schema" (the attribute name avoids shadowing pydantic's own `schema`).
    schema_: SchemaCheck = Field(serialization_alias="schema")
    provider_cache: ProviderCacheCheck


class ReadinessResponse(BaseModel):
    status: ReadinessStatus
    checks: ReadinessChecks
