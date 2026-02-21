"""Alembic environment configuration."""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all ORM models so Alembic autogenerate can detect schema changes.
# This must come after config/logging setup to avoid early import side-effects.
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "apps", "api"))

from app.infrastructure.db.models import Base  # noqa: E402

target_metadata = Base.metadata

DATABASE_URL = config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
