"""Run pytest with test-only service settings derived from the local Compose env."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from urllib.parse import quote

import asyncpg  # pyright: ignore[reportMissingTypeStubs]

from scripts.wait_for_local_services import (
    LocalServiceSettings,
    load_local_service_settings,
)

TEST_DATABASE_NAME = "harness_test"


def build_test_environment(
    current: Mapping[str, str],
    settings: LocalServiceSettings,
) -> dict[str, str]:
    """Return a child environment without importing unrelated Compose secrets."""
    environment = dict(current)
    password = quote(settings.postgres_password, safe="")
    environment.setdefault(
        "HARNESS_TEST_DATABASE_URL",
        (
            "postgresql+asyncpg://harness:"
            f"{password}@127.0.0.1:{settings.postgres_port}/{TEST_DATABASE_NAME}"
        ),
    )
    environment.setdefault(
        "HARNESS_TEST_REDIS_BASE_URL",
        f"redis://127.0.0.1:{settings.redis_port}",
    )
    environment.setdefault(
        "HARNESS_TEST_REDIS_URL",
        f"redis://127.0.0.1:{settings.redis_port}/10",
    )
    environment.setdefault(
        "HARNESS_TEST_MINIO_ENDPOINT",
        f"127.0.0.1:{settings.minio_port}",
    )
    environment.setdefault("HARNESS_TEST_MINIO_ACCESS_KEY", settings.minio_access_key)
    environment.setdefault("HARNESS_TEST_MINIO_SECRET_KEY", settings.minio_secret_key)
    environment.setdefault("HARNESS_TEST_MINIO_BUCKET", settings.minio_bucket)
    environment["HARNESS_OTEL_ENABLED"] = current.get(
        "HARNESS_TEST_OTEL_ENABLED", "false"
    )
    return environment


async def ensure_local_test_database(settings: LocalServiceSettings) -> None:
    """Create the isolated local test database once without resetting app data."""
    try:
        connection = await asyncpg.connect(  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            host="127.0.0.1",
            port=settings.postgres_port,
            user="harness",
            password=settings.postgres_password,
            database="harness",
        )
    except Exception as error:  # noqa: BLE001 - convert infrastructure failure
        raise RuntimeError(
            "Local PostgreSQL is unavailable; start it with `make docker-up` "
            "or set HARNESS_TEST_DATABASE_URL explicitly."
        ) from error

    try:
        exists = await connection.fetchval(  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            "SELECT 1 FROM pg_database WHERE datname = $1",
            TEST_DATABASE_NAME,
        )
        if not exists:
            try:
                await connection.execute(  # pyright: ignore[reportUnknownMemberType]
                    "CREATE DATABASE harness_test"
                )
            except asyncpg.exceptions.DuplicateDatabaseError:
                # Another local verification command won the create race.
                pass
    finally:
        await connection.close()  # pyright: ignore[reportUnknownMemberType]


def main(arguments: Sequence[str] | None = None) -> int:
    current = dict(os.environ)
    settings = load_local_service_settings()
    if not current.get("HARNESS_TEST_DATABASE_URL"):
        asyncio.run(ensure_local_test_database(settings))
    environment = build_test_environment(current, settings)
    command = [sys.executable, "-m", "pytest", *(arguments or ())]
    completed = subprocess.run(command, env=environment, check=False)  # noqa: S603
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
