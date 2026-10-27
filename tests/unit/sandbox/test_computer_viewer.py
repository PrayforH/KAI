"""Browser access needs a live owner session, matching origin and sole input."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from harness.computer import viewer

ORIGIN = "https://kai.example.test"


@pytest.fixture
def gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state: dict[str, Any] = {"active": True, "user": "owner", "tenant": "private", "connections": 0}

    def authenticate(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer fixture" or not state["active"]:
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={
                "user": {"user_id": state["user"]},
                "membership": {"tenant_id": state["tenant"]},
            },
        )

    async def connect(_self: viewer.DesktopSsh) -> asyncio.subprocess.Process:
        state["connections"] += 1
        # A real child with pipe lifetime, no SSH keys or external desktop.
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            "import os; os.write(1,b'RFB 003.008\\n'); "
            "data=os.read(0,4); os.write(1,data); os.read(0,1)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )

    monkeypatch.setattr(viewer.DesktopSsh, "connect", connect)
    monkeypatch.setattr(viewer, "AUTH_CHECK_SECONDS", 0.02)
    settings = viewer.ViewerSettings(
        api_url="http://api",
        tenant_id="private",
        user_id="owner",
        computer_id="computer_" + "a" * 32,
        incarnation="b" * 32,
        ssh_host="127.0.0.1",
        identities=tmp_path,
        origins=frozenset({ORIGIN}),
        public_origin=ORIGIN,
    )
    auth = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(authenticate))
    with TestClient(viewer.create_viewer_app(settings, client=auth)) as client:
        client.cookies.set("harness_access_token", "fixture")
        yield client, state


def test_metadata_requires_the_correct_owner_and_tenant(gateway: tuple[TestClient, dict[str, Any]]):
    client, state = gateway
    assert client.get("/computer/status").json() == {
        "enabled": True,
        "mode": "preview",
        "busy": False,
        "input_frames": {"keyboard": 0, "pointer": 0},
        "desktop_url": ORIGIN + "/assistant/computer",
    }
    state["user"] = "other"
    assert client.get("/computer/status").status_code == 403
    state.update(user="owner", tenant="other")
    assert client.get("/computer/status").status_code == 403
    state.update(tenant="private", active=False)
    assert client.get("/computer/status").status_code == 403
    assert state["connections"] == 0


@pytest.mark.parametrize("origin", [None, "https://evil.example.test", "null"])
def test_cross_origin_socket_never_opens_ssh(
    gateway: tuple[TestClient, dict[str, Any]], origin: str | None
) -> None:
    client, state = gateway
    headers = {"origin": origin} if origin is not None else {}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/computer/desktop", headers=headers):
            pytest.fail("cross-origin socket was accepted")
    assert state["connections"] == 0


def test_binary_transport_is_exclusive_and_revocation_closes_it(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        socket.send_bytes(b"PING")
        assert socket.receive_bytes() == b"PING"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}):
                pytest.fail("a second input owner was accepted")
        assert state["connections"] == 1
        state["active"] = False
        assert socket.receive()["type"] == "websocket.close"
    assert client.get("/computer/status").status_code == 403


def test_unauthenticated_socket_never_opens_ssh(gateway: tuple[TestClient, dict[str, Any]]):
    client, state = gateway
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}):
            pytest.fail("anonymous socket was accepted")
    assert state["connections"] == 0
