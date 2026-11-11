"""Authentication and validation failures never echo private command input."""

import httpx
import pytest
from pydantic import SecretStr

from harness.computer.control import HttpComputerControl
from harness.computer.control_server import ControllerSettings, create_app


@pytest.mark.parametrize("body", ["{}", "[]", '{"available":false}', "not-json"])
@pytest.mark.asyncio
async def test_image_preflight_requires_a_positive_acknowledgement(body: str) -> None:
    async with httpx.AsyncClient(
        base_url="http://control",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)),
    ) as client:
        with pytest.raises(RuntimeError, match="acknowledgement"):
            await HttpComputerControl(client).check_image("sha256:" + "a" * 64)


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
            for path in [
                "/v1/computer/control",
                "/v1/computer/lifecycle",
                "/v1/computer/image/check",
                "/v1/computer/vm/suspend",
            ]:
                response = await client.post(path, content=b"not-json private-fixture")
                assert response.status_code == 403
                assert "private-fixture" not in response.text
            response = await client.post(
                "/v1/computer/vm/suspend",
                headers={"Authorization": "Bearer " + "a" * 48},
                json={"tenant_id": "t", "user_id": "u", "idle_seconds": 1800},
            )
            assert response.status_code == 404  # no cloud VM is declared here
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
            response = await client.post(
                "/v1/computer/image/check",
                headers={"Authorization": "Bearer " + "a" * 48},
                json={"image": "private-fixture:latest"},
            )
            assert response.status_code == 422 and "private-fixture" not in response.text


def test_control_service_refuses_a_missing_private_authentication_secret() -> None:
    with pytest.raises(ValueError, match="private authentication"):
        create_app(
            ControllerSettings(
                database_url="postgresql+asyncpg://harness:harness@127.0.0.1:1/unused",
                secret=SecretStr(""),
                image="computer:v0",
            )
        )
