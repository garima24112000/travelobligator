"""Declarative base for ORM models (Step 183B foundation, Step 183C models).

`app/db/models.py` (Step 183C) defines `TripRow`/`PlanningStateRow`
against this `Base` -- see that module's docstring for why it still isn't
imported by any route/service/repository. `alembic/env.py` imports
`app.db.models` so `Base.metadata` (this class's `target_metadata`) is
populated for future `alembic revision --autogenerate` diffs; the actual
Step 183C migration itself is hand-written and self-contained, not
generated from this metadata.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
