"""PostgreSQL connection foundation (Step 183B).

This package exists purely as a foundation: `session.py` provides a sync
SQLAlchemy engine/session factory that reads `Settings.database_url`, but
nothing in `app/api`, `app/services`, or `app/repositories` imports from
this package yet. `LocalJsonStore` (see `app/storage/local_json_store.py`)
remains the actual persistence layer for every route today, regardless of
`Settings.persistence_backend`.

Deliberately empty otherwise: importing this package must never create a
database engine or open a connection, so no submodule is re-exported here.
"""
