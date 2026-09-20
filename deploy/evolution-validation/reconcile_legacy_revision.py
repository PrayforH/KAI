"""Repair only the isolated pre-develop evolution database's 0035 collision.

Stop API/worker and take a pg_dump first. This updates migration bookkeeping,
never downgrades schema. Run Alembic upgrade head immediately afterward.
Ordinary develop databases must NOT run this repair.
"""

import asyncio
import os
import sys

import asyncpg


async def main() -> None:
    if sys.argv[1:] != ["--apply-after-backup"]:
        raise SystemExit("Requires --apply-after-backup; see module prerequisites")
    url = os.environ["HARNESS_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(url)
    try:
        async with connection.transaction():
            await connection.execute("LOCK TABLE alembic_version IN EXCLUSIVE MODE")
            revisions = await connection.fetch("SELECT version_num FROM alembic_version")
            evolution_exists = await connection.fetchval(
                "SELECT to_regclass('public.evolution_jobs')"
            )
            projects_exists = await connection.fetchval("SELECT to_regclass('public.projects')")
            project_column = await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='agui_thread_bindings' "
                "AND column_name='project_id')"
            )
            if (
                [row["version_num"] for row in revisions] != ["0035"]
                or not evolution_exists
                or projects_exists
                or project_column
            ):
                raise SystemExit("Schema is not the legacy isolated evolution revision; no changes")
            await connection.execute("UPDATE alembic_version SET version_num='0034'")
        print("Legacy revision marker reconciled to 0034; now run alembic upgrade head")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
