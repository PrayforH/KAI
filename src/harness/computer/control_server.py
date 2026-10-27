"""Dedicated internal Computer control service; never exposed by the user sandbox."""

import hmac
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from harness.computer.access import CanonicalComputerAccess
from harness.computer.control import MAX_BYTES, ControlAction, DockerComputerControl
from harness.computer.host import ComputerLimits, DockerComputerLifecycle
from harness.computer.models import (
    ComputerLease,
    ComputerLeaseLostError,
    ComputerStartRejectedError,
)
from harness.computer.registry import ComputerRegistry
from harness.core.errors import NotFoundError
from harness.storage.database import create_database


class ControllerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_COMPUTER_CONTROL_")
    database_url: str
    secret: SecretStr
    image: str
    authorization: Literal["canonical", "probe"] = "canonical"
    owners: dict[str, frozenset[str]] = Field(default_factory=dict)
    memory_bytes: int = 4 * 1024**3
    nano_cpus: int = 2_000_000_000
    docker_socket: str = "/var/run/docker.sock"
    port: int = 8840


class ControlRequest(BaseModel):
    token: ComputerLease
    action: ControlAction
    payload: dict[str, Any]


class LifecycleRequest(BaseModel):
    token: ComputerLease
    operation: str
    image: str | None = None


class ImageCheckRequest(BaseModel):
    image: str


def create_app(settings: ControllerSettings) -> FastAPI:
    secret = settings.secret.get_secret_value()
    if len(secret) < 32:
        raise ValueError("computer controller requires a private authentication secret")
    if (
        settings.authorization == "probe"
        and make_url(settings.database_url).database != "harness_computer_probe"
    ):
        raise ValueError("probe authorization requires the isolated Computer probe database")
    engine, sessions = create_database(settings.database_url)
    access = (
        CanonicalComputerAccess(sessions, settings.owners)
        if settings.authorization == "canonical"
        else None
    )
    docker = httpx.AsyncClient(
        base_url="http://docker",
        transport=httpx.AsyncHTTPTransport(uds=settings.docker_socket),
        trust_env=False,
        timeout=30,
    )
    controller = DockerComputerControl(ComputerRegistry(sessions), docker, authorizer=access)
    host = DockerComputerLifecycle(
        controller.registry,
        docker,
        ComputerLimits(
            settings.image, memory_bytes=settings.memory_bytes, nano_cpus=settings.nano_cpus
        ),
        authorizer=access,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            await docker.aclose()
            await engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route("/health", health, methods=["GET"])

    async def private_body(request: Request) -> bytearray:
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + secret).encode()):
            raise HTTPException(403, "Computer control is not authorized")
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > MAX_BYTES:
                raise HTTPException(413, "Computer control request is too large")
        return body

    async def control(request: Request):
        try:
            command = ControlRequest.model_validate_json(await private_body(request))
        except ValidationError:
            raise HTTPException(400, "Computer control request is invalid") from None
        try:
            return await controller.call(command.token, command.action, command.payload)
        except (ComputerLeaseLostError, NotFoundError):
            raise HTTPException(409, "Computer execution ownership was lost") from None
        except (httpx.HTTPError, RuntimeError, ValueError):
            # Never return Docker messages, request input, private paths or env.
            raise HTTPException(503, "Computer control could not confirm execution") from None

    app.add_api_route("/v1/computer/control", control, methods=["POST"])

    async def check_image(request: Request):
        try:
            command = ImageCheckRequest.model_validate_json(await private_body(request))
        except ValidationError:
            raise HTTPException(400, "Computer image request is invalid") from None
        try:
            await host.check_image(command.image)
            return {"available": True}
        except ComputerStartRejectedError:
            raise HTTPException(422, "Computer image is unavailable") from None
        except (httpx.HTTPError, RuntimeError, ValueError):
            raise HTTPException(503, "Computer image validation could not be confirmed") from None

    app.add_api_route("/v1/computer/image/check", check_image, methods=["POST"])

    async def lifecycle(request: Request):
        try:
            command = LifecycleRequest.model_validate_json(await private_body(request))
        except ValidationError:
            raise HTTPException(400, "Computer lifecycle request is invalid") from None
        try:
            return {
                "provider_ref": await host.lifecycle(
                    command.token, command.operation, image=command.image
                )
            }
        except ComputerStartRejectedError:
            raise HTTPException(422, "Computer image is unavailable") from None
        except (ComputerLeaseLostError, NotFoundError):
            raise HTTPException(409, "Computer execution ownership was lost") from None
        except (httpx.HTTPError, RuntimeError, ValueError):
            raise HTTPException(503, "Computer lifecycle could not be confirmed") from None

    app.add_api_route("/v1/computer/lifecycle", lifecycle, methods=["POST"])
    return app


def main() -> None:
    settings = ControllerSettings(
        database_url=os.environ["HARNESS_COMPUTER_CONTROL_DATABASE_URL"],
        secret=SecretStr(os.environ["HARNESS_COMPUTER_CONTROL_SECRET"]),
        image=os.environ["HARNESS_COMPUTER_CONTROL_IMAGE"],
    )
    uvicorn.run(create_app(settings), host="0.0.0.0", port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
