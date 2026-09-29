# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownParameterType=false, reportMissingParameterType=false
"""Alembic replay of revision 0038 against a real PostgreSQL database.

Two paths must produce one identical schema: a fresh database initialised from
the ORM metadata, and an existing database upgraded from revision 0037. The
comparison is made through the database's own reflection, not through the
migration's declarations.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.storage.database import SessionFactory

DatabaseFixture = tuple[AsyncEngine, SessionFactory]

ROOT = Path(__file__).resolve().parents[3]
TABLE = "run_execution_commands"


async def reflected(engine: AsyncEngine, table: str) -> dict[str, object]:
    """Read the table back from PostgreSQL, so the assertion is about the schema."""

    def read(connection) -> dict[str, object]:  # type: ignore[no-untyped-def]
        inspector = inspect(connection)
        return {
            "columns": {
                column["name"]: (str(column["type"]), bool(column["nullable"]))
                for column in inspector.get_columns(table)
            },
            "indexes": sorted(index["name"] for index in inspector.get_indexes(table)),
            "unique": sorted(
                constraint["name"]
                for constraint in inspector.get_unique_constraints(table)
            ),
            "primary_key": list(
                inspector.get_pk_constraint(table)["constrained_columns"]
            ),
        }

    async with engine.connect() as connection:
        return await connection.run_sync(read)


async def has_table(engine: AsyncEngine, table: str) -> bool:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda inspected: inspect(inspected).has_table(table)
        )


async def reset_alembic_version(engine: AsyncEngine) -> None:
    """The version table is outside the ORM metadata, so the fixture leaves it."""

    async with engine.begin() as connection:
        await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))


def alembic(database: DatabaseFixture, *args: str) -> subprocess.CompletedProcess[str]:
    engine, _ = database
    environment = dict(os.environ)
    environment["HARNESS_DATABASE_URL"] = engine.url.render_as_string(
        hide_password=False
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.asyncio
async def test_upgrading_from_0037_matches_a_fresh_database(
    database: DatabaseFixture,
) -> None:
    engine, _ = database
    await reset_alembic_version(engine)
    fresh = await reflected(engine, TABLE)

    # Make this database look like a deployment at 0037 that never had the
    # dispatch table, then replay the revision under test.
    async with engine.begin() as connection:
        await connection.execute(text(f"DROP TABLE {TABLE}"))
    assert await has_table(engine, TABLE) is False

    stamped = alembic(database, "stamp", "0037")
    assert stamped.returncode == 0, stamped.stderr
    upgraded = alembic(database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    migrated = await reflected(engine, TABLE)
    assert migrated == fresh
    assert migrated["primary_key"] == ["command_id"]
    assert migrated["unique"] == ["uq_run_execution_command"]


@pytest.mark.asyncio
async def test_replaying_the_revision_on_a_fresh_database_changes_nothing(
    database: DatabaseFixture,
) -> None:
    """Revision 0001's legacy create_all already made the table."""

    engine, _ = database
    await reset_alembic_version(engine)
    before = await reflected(engine, TABLE)

    stamped = alembic(database, "stamp", "0037")
    assert stamped.returncode == 0, stamped.stderr
    replay = alembic(database, "upgrade", "head")

    assert replay.returncode == 0, replay.stderr
    assert await reflected(engine, TABLE) == before


@pytest.mark.asyncio
async def test_downgrading_removes_only_the_dispatch_table(
    database: DatabaseFixture,
) -> None:
    engine, _ = database
    await reset_alembic_version(engine)
    stamped = alembic(database, "stamp", "0038")
    assert stamped.returncode == 0, stamped.stderr

    downgraded = alembic(database, "downgrade", "-1")

    assert downgraded.returncode == 0, downgraded.stderr
    assert await has_table(engine, TABLE) is False
    # The rest of the schema is untouched, so a rollback stays bounded.
    assert await has_table(engine, "runs") is True
    assert await has_table(engine, "run_events") is True
