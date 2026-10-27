"""Browser access needs a live owner session, matching origin and sole input."""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, cast

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
        if request.url.path == "/v1/studio/assistant/computer/desktop":
            return httpx.Response(
                200,
                json={"state": state.get("control", "human"), "epoch": state.get("epoch", 1)},
            )
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
        "control": {"state": "human", "epoch": 1},
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


def status_json(client: TestClient) -> dict[str, Any]:
    # The unparameterized TestClient reports unknown members under strict
    # typing; the casts keep the assertions themselves fully typed.
    payload = cast(Any, client).get("/computer/status").json()
    return cast(dict[str, Any], payload)


def wait_for_control(client: TestClient, expected: str) -> None:
    """The socket gate mirrors the lease on its next poll tick."""

    deadline = time.monotonic() + 5
    while True:
        if str(status_json(client)["control"]["state"]) == expected:
            break
        assert time.monotonic() < deadline, f"desktop lease never reached {expected}"
        time.sleep(0.02)
    time.sleep(0.1)


def test_binary_transport_is_exclusive_and_revocation_closes_it(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        wait_for_control(client, "human")
        socket.send_bytes(b"PING")
        assert socket.receive_bytes() == b"PING"

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}):
                pytest.fail("a second input owner was accepted")
        assert state["connections"] == 1
        state["active"] = False
        assert socket.receive()["type"] == "websocket.close"
    assert client.get("/computer/status").status_code == 403


def test_observation_gate_drops_input_and_forwards_after_takeover(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        # While the assistant owns the desktop (observe here) input is dropped;
        # the five-byte forwarded frame later completes the child's trailing
        # single-byte read, which closes the transport from the desktop side.
        state["control"] = "observe"
        socket.send_bytes(b"\x05AAA")
        state["control"] = "human"
        wait_for_control(client, "human")
        socket.send_bytes(b"\x05BBB")
        # The desktop echoes the first frame it actually received: the frame
        # sent while observing was dropped, so the echo is the takeover one.
        assert socket.receive_bytes() == b"\x05BBB"
        assert status_json(client)["input_frames"] == {"keyboard": 0, "pointer": 1}
        state["active"] = False
        assert socket.receive()["type"] == "websocket.close"


def test_unauthenticated_socket_never_opens_ssh(gateway: tuple[TestClient, dict[str, Any]]):
    client, state = gateway
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}):
            pytest.fail("anonymous socket was accepted")
    assert state["connections"] == 0
