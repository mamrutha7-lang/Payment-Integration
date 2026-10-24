"""Alembic environment — wired to the app's Base metadata and settings."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  — registers every model on Base.metadata
from app.config import settings
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the normalised (psycopg3) URL rather than hard-coding it in alembic.ini.
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)

target_metadata = Base.metadata

#: Tables a migration created to PRESERVE DATA it was about to destroy. They
#: have no model by design — they are an operator's receipt, not part of the
#: schema — so autogenerate sees them as tables to drop and helpfully proposes
#: `op.drop_table(...)` in the next migration somebody generates. That would
#: quietly delete the only copy of the rows, which is the exact opposite of why
#: the table exists.
#:
#: The prefix is the contract: a migration that stashes rows before a
#: destructive statement names the table `<something>_orphaned_*`, and it then
#: survives every future autogenerate untouched. Dropping one is a deliberate
#: act by a person who has looked at the rows.
_PRESERVED_DATA_TABLES = ("students_orphaned_cohort_ids",)


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Keep data-preservation tables out of autogenerate's diff."""
    if type_ == "table" and name in _PRESERVED_DATA_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
