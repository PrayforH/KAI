from __future__ import annotations

from typing import Any

import pytest

from scripts.run_local_tests import (
    build_test_environment,
    ensure_local_test_database,
)
from scripts.wait_for_local_services import LocalServiceSettings


def settings() -> LocalServiceSettings:
    return LocalServiceSettings(
        postgres_password="local p@ssword",
        postgres_port=55432,
        redis_port=56379,
        minio_access_key="local-user",
        minio_secret_key="local-secret",
        minio_port=59000,
        minio_bucket="local-artifacts",
    )


def test_build_test_environment_only_adds_test_dependencies() -> None:
    environment = build_test_environment({"PATH": "/bin"}, settings())

    assert environment == {
        "PATH": "/bin",
        "HARNESS_TEST_DATABASE_URL": (
            "postgresql+asyncpg://harness:local%20p%40ssword@"
            "127.0.0.1:55432/harness_test"
        ),
        "HARNESS_TEST_REDIS_BASE_URL": "redis://127.0.0.1:56379",
        "HARNESS_TEST_REDIS_URL": "redis://127.0.0.1:56379/10",
        "HARNESS_TEST_MINIO_ENDPOINT": "127.0.0.1:59000",
        "HARNESS_TEST_MINIO_ACCESS_KEY": "local-user",
        "HARNESS_TEST_MINIO_SECRET_KEY": "local-secret",
        "HARNESS_TEST_MINIO_BUCKET": "local-artifacts",
        "HARNESS_OTEL_ENABLED": "false",
    }


def test_build_test_environment_preserves_explicit_ci_overrides() -> None:
    environment = build_test_environment(
        {
            "HARNESS_TEST_DATABASE_URL": "postgresql+asyncpg://ci/db",
            "HARNESS_TEST_MINIO_ACCESS_KEY": "ci-user",
            "HARNESS_OTEL_ENABLED": "true-from-runtime",
            "HARNESS_TEST_OTEL_ENABLED": "true",
        },
        settings(),
    )

    assert environment["HARNESS_TEST_DATABASE_URL"] == "postgresql+asyncpg://ci/db"
    assert environment["HARNESS_TEST_MINIO_ACCESS_KEY"] == "ci-user"
    assert environment["HARNESS_OTEL_ENABLED"] == "true"


@pytest.mark.asyncio
async def test_ensure_local_test_database_creates_only_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    class Connection:
        async def fetchval(self, statement: str, *parameters: Any) -> None:
            calls.append((statement, parameters))

        async def execute(self, statement: str) -> None:
            calls.append((statement, ()))

        async def close(self) -> None:
            calls.append(("close", ()))

    async def connect(**parameters: Any) -> Connection:
        assert parameters == {
            "host": "127.0.0.1",
            "port": 55432,
            "user": "harness",
            "password": "local p@ssword",
            "database": "harness",
        }
        return Connection()

    monkeypatch.setattr("scripts.run_local_tests.asyncpg.connect", connect)

    await ensure_local_test_database(settings())

    assert calls == [
        (
            "SELECT 1 FROM pg_database WHERE datname = $1",
            ("harness_test",),
        ),
        ("CREATE DATABASE harness_test", ()),
        ("close", ()),
    ]


@pytest.mark.asyncio
async def test_ensure_local_test_database_does_not_recreate_existing_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Connection:
        async def fetchval(self, _statement: str, *_parameters: Any) -> int:
            return 1

        async def execute(self, statement: str) -> None:
            calls.append(statement)

        async def close(self) -> None:
            calls.append("close")

    async def connect(**_parameters: Any) -> Connection:
        return Connection()

    monkeypatch.setattr("scripts.run_local_tests.asyncpg.connect", connect)

    await ensure_local_test_database(settings())

    assert calls == ["close"]
