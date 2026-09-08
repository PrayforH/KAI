import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app


@pytest.mark.asyncio
async def test_personal_web_settings_are_persisted_without_returning_secrets() -> None:
    app = create_memory_app()
    headers = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-a"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.put("/v1/studio/web-configuration", headers=headers,
            json={"enabled": False, "provider": "tavily", "apiKey": "test-only-secret"})
        assert saved.status_code == 200
        assert "test-only-secret" not in saved.text
        assert saved.json()["personalKeyConfigured"] is True
        restored = await client.get("/v1/studio/web-configuration", headers=headers)
        assert restored.json()["effectiveEnabled"] is False
        other = await client.get("/v1/studio/web-configuration",
            headers={**headers, "X-User-ID": "user-b"})
        assert other.json()["enabled"] is True
        assert other.json()["personalKeyConfigured"] is False
        invalid = await client.put("/v1/studio/web-configuration", headers=headers,
            json={"provider": "https://arbitrary-endpoint.test"})
        assert invalid.status_code == 422
