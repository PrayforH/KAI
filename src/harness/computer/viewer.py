"""Account-authenticated private desktop preview, isolated from Run execution.

This first viewer owns no lifecycle or Docker socket. Its fixed target and SSH
identity are node configuration. The API remains the authority for each login
and membership; browser input never selects a host, key, user, or command.
"""

import asyncio
import contextlib
import ipaddress
import os
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, field_validator

from harness.computer.vm_keys import VmSeedStore

AUTH_CHECK_SECONDS = 1
AUTH_GRACE_SECONDS = 3


class ViewerSettings(BaseModel):
    api_url: str
    tenant_id: str
    user_id: str
    computer_id: str
    incarnation: str
    ssh_host: str
    ssh_port: int = Field(default=2222, ge=1, le=65535)
    identities: Path
    origins: frozenset[str]
    public_origin: str
    cookie_name: str = "harness_access_token"

    @field_validator("public_origin")
    @classmethod
    def https_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("desktop needs its trusted HTTPS origin")
        return value.rstrip("/")

    @field_validator("ssh_host")
    @classmethod
    def private_address(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            # Docker's private DNS keeps the verified VM reachable on restart.
            if re.fullmatch(r"kai-computer-[a-z0-9-]{1,48}", value):
                return value
            raise ValueError("desktop target must name a private computer") from None
        if not address.is_private:
            raise ValueError("desktop target must be on the private node network")
        return value


class DesktopSsh:
    def __init__(self, settings: ViewerSettings) -> None:
        self.settings = settings

    async def connect(self) -> asyncio.subprocess.Process:
        settings = self.settings
        # prepare is idempotent only for an existing, complete private identity.
        path = settings.identities / f"{settings.computer_id}-{settings.incarnation}"
        if not path.exists():
            raise RuntimeError("desktop identity has not been provisioned")
        seed = VmSeedStore(settings.identities).prepare(settings.computer_id, settings.incarnation)
        return await asyncio.create_subprocess_exec(
            "ssh",
            "-T",
            "-F",
            "/dev/null",
            "-p",
            str(settings.ssh_port),
            "-i",
            str(path / "viewer"),
            "-o",
            "UserKnownHostsFile=" + str(path / "known_hosts"),
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "HostKeyAlias=" + seed.host_key_alias,
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UpdateHostKeys=no",
            "-o",
            "CheckHostIP=no",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=10",
            "-o",
            "ServerAliveCountMax=2",
            "--",
            "kai-view@" + settings.ssh_host,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # SSH errors may contain private node paths; do not reflect them.
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )


def create_viewer_app(
    settings: ViewerSettings, *, client: httpx.AsyncClient | None = None
) -> FastAPI:
    auth = client or httpx.AsyncClient(base_url=settings.api_url, trust_env=False, timeout=5)
    desktop = DesktopSsh(settings)
    busy = asyncio.Lock()
    input_frames = {"keyboard": 0, "pointer": 0}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            if client is None:
                await auth.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def authorized(token: str | None) -> bool:
        if not token:
            return False
        try:
            response = await auth.get("/v1/auth/me", headers={"Authorization": "Bearer " + token})
            if response.status_code != 200:
                return False
            value: dict[str, Any] = response.json()
            return (
                value.get("user", {}).get("user_id") == settings.user_id
                and value.get("membership", {}).get("tenant_id") == settings.tenant_id
            )
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    async def status(request: Request) -> dict[str, object]:
        token = request.cookies.get(settings.cookie_name)
        if not token and request.headers.get("authorization", "").startswith("Bearer "):
            token = request.headers["authorization"][7:]
        if not await authorized(token):
            raise HTTPException(403, "Desktop access requires its owner login")
        return {
            "enabled": True,
            "mode": "preview",
            "busy": busy.locked(),
            "input_frames": dict(input_frames),
            "desktop_url": settings.public_origin + "/assistant/computer",
        }

    app.add_api_route("/computer/status", status, methods=["GET"])

    async def stream(websocket: WebSocket) -> None:
        token = websocket.cookies.get(settings.cookie_name)
        if websocket.headers.get("origin") not in settings.origins or not await authorized(token):
            await websocket.close(code=1008)
            return
        # One viewer for this preview. No Agent GUI endpoint exists yet.
        if busy.locked():
            await websocket.close(code=1013)
            return
        async with busy:
            process: asyncio.subprocess.Process | None = None
            tasks: list[asyncio.Task[None]] = []
            try:
                process = await desktop.connect()
                assert process.stdout is not None and process.stdin is not None
                output, input_stream = process.stdout, process.stdin
                header = await asyncio.wait_for(output.readexactly(12), timeout=15)
                if not header.startswith(b"RFB 003."):
                    raise RuntimeError("desktop did not confirm RFB transport")
                await websocket.accept()
                await websocket.send_bytes(header)
                valid_until = time.monotonic() + AUTH_GRACE_SECONDS

                async def to_browser() -> None:
                    while data := await output.read(65536):
                        await websocket.send_bytes(data)

                async def to_desktop() -> None:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        data = message.get("bytes")
                        if not isinstance(data, bytes) or len(data) > 65536:
                            raise ValueError("desktop input exceeds frame size")
                        # Check immediately before forwarding each input frame.
                        if time.monotonic() >= valid_until:
                            return
                        if data:
                            if data[0] in {4, 255}:
                                input_frames["keyboard"] += 1
                            elif data[0] == 5:
                                input_frames["pointer"] += 1
                        input_stream.write(data)
                        await input_stream.drain()

                async def watch_authorization() -> None:
                    nonlocal valid_until
                    while True:
                        await asyncio.sleep(AUTH_CHECK_SECONDS)
                        if not await authorized(token):
                            return
                        valid_until = time.monotonic() + AUTH_GRACE_SECONDS

                tasks = [
                    asyncio.create_task(job())
                    for job in (to_browser, to_desktop, watch_authorization)
                ]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            except (
                OSError,
                RuntimeError,
                ValueError,
                TimeoutError,
                asyncio.IncompleteReadError,
                WebSocketDisconnect,
            ):
                pass
            finally:
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                if process is not None:
                    if process.stdin is not None:
                        process.stdin.close()
                    if process.returncode is None:
                        with contextlib.suppress(ProcessLookupError):
                            process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=5)
                        except TimeoutError:
                            with contextlib.suppress(ProcessLookupError):
                                process.kill()
                            await process.wait()
                with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.close(code=1000)

    app.add_api_websocket_route("/computer/desktop", stream)
    return app


def main() -> None:
    settings = ViewerSettings.model_validate_json(
        Path(os.environ["KAI_VIEWER_CONFIG"]).read_bytes()
    )
    # Exactly one process owns the preview's input lock.
    uvicorn.run(
        create_viewer_app(settings),
        host="0.0.0.0",
        port=8842,
        workers=1,
        ws_max_size=65536,
        access_log=False,
    )


if __name__ == "__main__":
    main()
