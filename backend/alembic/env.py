from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Step 183B: read the app's own config/normalization rather than
# duplicating URL logic here. Importing these does not open a connection --
# `get_settings()` just constructs a `Settings()` (reading env/.env), and
# `normalize_database_url` is pure string manipulation. No connection is
# made until `run_migrations_online()` below actually calls `connect()`,
# which only happens when a real `alembic` command is invoked.
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import normalize_database_url

# Step 183C: importing this registers TripRow/PlanningStateRow onto
# Base.metadata (see app/db/models.py's own docstring for why this module
# is otherwise never imported by app runtime) -- purely so a future
# `alembic revision --autogenerate` has real metadata to diff against.
# This import alone never creates a table or opens a connection.
import app.db.models  # noqa: F401

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Step 183B: `Base.metadata` is currently empty -- no ORM models exist yet
# (that's Step 183C's job). Autogenerate has nothing to diff against until
# then; this wiring is here so 183C only has to add models, not env.py
# plumbing.
target_metadata = Base.metadata

# Step 183B: override whatever (unset/placeholder) `sqlalchemy.url` is in
# alembic.ini with the real app config, so both the app and Alembic read
# the same source of truth. This still doesn't connect to anything --
# `set_main_option` just changes a config value in memory.
config.set_main_option(
    "sqlalchemy.url", normalize_database_url(get_settings().database_url)
)

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
