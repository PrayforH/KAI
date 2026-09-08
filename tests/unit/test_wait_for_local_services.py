from pathlib import Path

import pytest

from scripts.wait_for_local_services import load_local_service_settings


def test_local_service_settings_follow_compose_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env.docker"
    env_file.write_text(
        "\n".join(
            (
                "POSTGRES_PASSWORD='compose-password'",
                "POSTGRES_PORT=55432",
                "REDIS_PORT=56379",
                "MINIO_ROOT_USER=compose-user",
                "MINIO_ROOT_PASSWORD='compose-secret'",
                "MINIO_PORT=59000",
                "HARNESS_MINIO_BUCKET=compose-artifacts",
            )
        ),
        encoding="utf-8",
    )

    settings = load_local_service_settings(env_file)

    assert settings.postgres_password == "compose-password"
    assert settings.postgres_port == 55432
    assert settings.redis_port == 56379
    assert settings.minio_access_key == "compose-user"
    assert settings.minio_secret_key == "compose-secret"
    assert settings.minio_port == 59000
    assert settings.minio_bucket == "compose-artifacts"

    monkeypatch.setenv("POSTGRES_PORT", "65432")
    assert load_local_service_settings(env_file).postgres_port == 65432
