import os
from typing import cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.api.app import create_app
from harness.api.dependencies import ApiContainer
from harness.composition import build_production_container
from harness.config import Settings
from harness.storage.database import SessionFactory

DatabaseFixture = tuple[AsyncEngine, SessionFactory]
SERVICE_TOKEN = "studio-production-token-with-at-least-32-characters"


def production_settings() -> Settings:
    return Settings(
        environment="production",
        runtime="claude-sdk",
        api_bearer_token=SecretStr(SERVICE_TOKEN),
        database_url=os.getenv(
            "HARNESS_TEST_DATABASE_URL",
            "postgresql+asyncpg://harness:harness@127.0.0.1:5432/harness_test",
        ),
        redis_url=f"{os.getenv('HARNESS_TEST_REDIS_BASE_URL', 'redis://localhost:6379')}/0",
        minio_access_key=SecretStr("test-minio-access"),
        minio_secret_key=SecretStr("test-minio-secret"),
    )


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }


def draft_request() -> dict[str, str]:
    return {
        "name": "durable-researcher",
        "domain": "policy-research",
        "displayName": "持久化研究助手",
        "description": "验证生产组合根重建后的草稿恢复。",
        "template": "analyst",
    }


async def close(container: ApiContainer) -> None:
    assert container.close is not None
    await container.close()


@pytest.mark.asyncio
async def test_production_studio_api_restores_draft_after_container_restart(
    database: DatabaseFixture,
) -> None:
    _engine, _sessions = database
    first = build_production_container(production_settings(), execution_enabled=False)
    first_app: FastAPI = create_app(first)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=first_app), base_url="http://test"
        ) as client:
            configured = await client.put(
                "/v1/studio/models/test-default",
                headers=headers(),
                json={
                    "expectedRevision": 1,
                    "label": "Test default model",
                    "modelType": "chat",
                    "provider": "Test provider",
                    "model": "test-model",
                    "baseUrl": "https://models.example.test/v1",
                    "apiFormat": "openai_compatible",
                    "authScheme": "bearer",
                    "apiKey": "test-model-secret",
                    "enabled": True,
                },
            )
            first_catalog = await client.get("/v1/studio/catalog", headers=headers())
            second_seed = await client.get("/v1/studio/catalog", headers=headers())
            created = await client.post(
                "/v1/studio/drafts", headers=headers(), json=draft_request()
            )
            preview = await client.post(
                "/v1/studio/previews",
                headers=headers(),
                json={
                    "draftId": created.json()["draftId"],
                    "expectedRevision": 1,
                    "idempotencyKey": "durable-preview-r1",
                    "ttlSeconds": 600,
                },
            )
            disabled = await client.delete(
                "/v1/studio/models/test-default",
                headers=headers(),
                params={"expectedRevision": configured.json()["revision"]},
            )
    finally:
        await close(first)

    assert created.status_code == 201
    assert configured.status_code == 200
    assert preview.status_code == 202
    assert preview.json()["status"] == "queued"
    assert first_catalog.json() == second_seed.json()
    assert disabled.status_code == 200
    draft_id = cast(str, created.json()["draftId"])

    second = build_production_container(production_settings(), execution_enabled=False)
    second_app: FastAPI = create_app(second)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=second_app), base_url="http://test"
        ) as client:
            restored = await client.get(f"/v1/studio/drafts/{draft_id}", headers=headers())
            restored_preview = await client.get(
                f"/v1/studio/previews/{preview.json()['previewId']}",
                headers=headers(),
            )
            restored_catalog = await client.get("/v1/studio/catalog", headers=headers())
            reconciled_preview = await second.preview_controller.reconcile(
                "tenant-a", preview.json()["previewId"]
            )
    finally:
        await close(second)

    assert restored.status_code == 200
    assert restored.json() == created.json()
    assert restored_preview.status_code == 200
    assert restored_preview.json()["status"] == "queued"
    assert reconciled_preview.status.value == "failed"
    # The catalog changed after the Preview snapshot was queued, so the durable
    # content/package hashes must fail closed as bundle drift after restart.
    assert reconciled_preview.error_code == "preflight_bundle_drift"
    assert reconciled_preview.preflight_result is not None
    assert reconciled_preview.preflight_result.status.value == "failed"
    assert restored_catalog.status_code == 200
    assert restored_catalog.json()["revision"] == configured.json()["revision"] + 1
    route = next(
        item
        for item in restored_catalog.json()["catalog"]["modelRoutes"]
        if item["routeId"] == "test-default"
    )
    assert route["enabled"] is False
