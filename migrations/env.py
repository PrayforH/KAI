import os
from asyncio import run
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from harness.computer import (
    storage as _computer_storage,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from harness.storage.models import Base

config = context.config
if database_url := os.getenv("HARNESS_DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy."
    )
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    run(run_online())
