"""Authentication and validation failures never echo private command input."""

import httpx
import pytest
from pydantic import SecretStr

from harness.computer.control_server import ControllerSettings, create_app


@pytest.mark.asyncio
async def test_private_control_routes_require_auth_before_parsing() -> None:
    app = create_app(
        ControllerSettings(
            database_url="postgresql+asyncpg://harness:harness@127.0.0.1:1/unused",
            secret=SecretStr("a" * 48),
            image="computer:v0",
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            base_url="http://control", transport=httpx.ASGITransport(app)
        ) as client:
            for path in ["/v1/computer/control", "/v1/computer/lifecycle"]:
                response = await client.post(path, content=b"not-json private-fixture")
                assert response.status_code == 403
                assert "private-fixture" not in response.text
            response = await client.post(
                "/v1/computer/control",
                headers={"Authorization": "Bearer " + "a" * 48},
                json={
                    "token": "invalid",
                    "action": "exec",
                    "payload": {"secret": "private-fixture"},
                },
            )
            assert response.status_code == 400 and "private-fixture" not in response.text


def test_control_service_refuses_a_missing_private_authentication_secret() -> None:
    with pytest.raises(ValueError, match="private authentication"):
        create_app(
            ControllerSettings(
                database_url="postgresql+asyncpg://harness:harness@127.0.0.1:1/unused",
                secret=SecretStr(""),
                image="computer:v0",
            )
        )
