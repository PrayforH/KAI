"""Wait for the phase-one local persistence services to become ready."""

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import asyncpg  # pyright: ignore[reportMissingTypeStubs]
from dotenv import dotenv_values
from minio import Minio
from redis.asyncio import Redis

ROOT = Path(__file__).parents[1]
DEFAULT_ENV_FILE = ROOT / "deploy/docker-compose/.env.docker"


@dataclass(frozen=True)
class LocalServiceSettings:
    postgres_password: str
    postgres_port: int
    redis_port: int
    minio_access_key: str
    minio_secret_key: str
    minio_port: int
    minio_bucket: str


def load_local_service_settings(
    env_file: Path | None = None,
) -> LocalServiceSettings:
    selected_env_file = env_file or Path(
        os.getenv("HARNESS_COMPOSE_ENV_FILE", str(DEFAULT_ENV_FILE))
    )
    file_values = dotenv_values(selected_env_file) if selected_env_file.exists() else {}

    def value(name: str, default: str) -> str:
        configured = os.getenv(name)
        if configured is None:
            configured = file_values.get(name)
        return configured if isinstance(configured, str) and configured else default

    return LocalServiceSettings(
        postgres_password=value("POSTGRES_PASSWORD", "harness"),
        postgres_port=int(value("POSTGRES_PORT", "5432")),
        redis_port=int(value("REDIS_PORT", "6379")),
        minio_access_key=value("MINIO_ROOT_USER", "minioadmin"),
        minio_secret_key=value("MINIO_ROOT_PASSWORD", "minioadmin"),
        minio_port=int(value("MINIO_PORT", "9000")),
        minio_bucket=value("HARNESS_MINIO_BUCKET", "harness-artifacts"),
    )


SETTINGS = load_local_service_settings()


async def retry[T](name: str, check: Callable[[], Awaitable[T]], timeout: float = 90) -> T:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return await check()
        except Exception as error:  # noqa: BLE001 - readiness boundary
            last_error = error
            await asyncio.sleep(1)
    raise RuntimeError(f"{name} did not become ready: {last_error}")


async def postgres_ready() -> None:
    connection = await asyncpg.connect(  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        host="127.0.0.1",
        port=SETTINGS.postgres_port,
        user="harness",
        password=SETTINGS.postgres_password,
        database="harness",
    )
    await connection.close()  # pyright: ignore[reportUnknownMemberType]


async def redis_ready() -> None:
    client: Redis = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        f"redis://127.0.0.1:{SETTINGS.redis_port}/0"
    )
    try:
        await client.ping()  # pyright: ignore[reportUnknownMemberType]
    finally:
        await client.aclose()


async def minio_ready() -> None:
    client = Minio(
        f"127.0.0.1:{SETTINGS.minio_port}",
        access_key=SETTINGS.minio_access_key,
        secret_key=SETTINGS.minio_secret_key,
        secure=False,
    )
    exists = await asyncio.to_thread(client.bucket_exists, SETTINGS.minio_bucket)
    if not exists:
        raise RuntimeError(f"{SETTINGS.minio_bucket} bucket is missing")


async def main() -> None:
    await asyncio.gather(
        retry("PostgreSQL", postgres_ready),
        retry("Redis", redis_ready),
        retry("MinIO", minio_ready),
    )
    print("PostgreSQL, Redis and MinIO are ready")


if __name__ == "__main__":
    asyncio.run(main())
