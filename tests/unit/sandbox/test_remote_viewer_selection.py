"""A viewer re-resolves the canonical target and cannot stay on an old machine."""

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from harness.computer import viewer
from harness.computer.ssh_transport import SshEndpoint

ORIGIN = "https://kai.example.test"


@pytest.fixture
def selected_viewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    selected = {"target": "cloud"}
    opened: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/targets"):
            return httpx.Response(200, json={"active_target": selected["target"]})
        if request.url.path.endswith("/desktop"):
            return httpx.Response(
                200,
                json={
                    "state": selected.get("control", "observe"),
                    "epoch": selected.get("epoch", 1),
                },
            )
        return httpx.Response(
            200, json={"user": {"user_id": "owner"}, "membership": {"tenant_id": "tenant"}}
        )

    async def connect(_self, endpoint: SshEndpoint | None = None):
        assert endpoint is not None
        opened.append(endpoint.host)
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            "import os\nos.write(1,b'RFB 003.008\\n')\n"
            "while True:\n data=os.read(0,4096)\n if not data:break\n os.write(1,data)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )

    monkeypatch.setattr(viewer.DesktopSsh, "connect", connect)
    monkeypatch.setattr(viewer, "AUTH_CHECK_SECONDS", 0.02)

    def endpoint(host):
        return SshEndpoint(
            host=host,
            user="viewer",
            identity="/private/key",
            known_hosts="/private/hosts",
            host_key_alias=host,
        )

    settings = viewer.ViewerSettings(
        api_url="http://api",
        tenant_id="tenant",
        user_id="owner",
        computer_id="computer_" + "a" * 32,
        incarnation="b" * 32,
        ssh_host="127.0.0.1",
        identities=tmp_path,
        origins=frozenset({ORIGIN}),
        public_origin=ORIGIN,
        targets={"cloud": endpoint("cloud"), "local": endpoint("local")},
    )
    auth = httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(respond))
    with TestClient(viewer.create_viewer_app(settings, client=auth)) as client:
        client.cookies.set("harness_access_token", "fixture")
        yield client, selected, opened


def test_selection_change_closes_old_stream_and_reconnects_to_new_machine(selected_viewer):
    client, selected, opened = selected_viewer
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        selected["target"] = "local"
        assert socket.receive()["type"] == "websocket.close"
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        selected["target"] = "cloud"
        assert socket.receive()["type"] == "websocket.close"
    assert opened == ["cloud", "local"]


def test_unserved_selection_never_opens_an_ssh_connection(selected_viewer):
    client, selected, opened = selected_viewer
    selected["target"] = "unserved"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}):
            pytest.fail("unserved desktop was accepted")
    assert opened == []


def test_old_human_epoch_cannot_send_input_while_observation_poll_is_waiting(
    selected_viewer, monkeypatch
):
    client, selected, _ = selected_viewer
    selected["control"] = "human"
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        time.sleep(0.05)
        monkeypatch.setattr(viewer, "AUTH_CHECK_SECONDS", 100)
        time.sleep(0.05)
        handshake = b"RFB 003.008\n" + b"\x01\x01"
        socket.send_bytes(handshake)
        assert socket.receive_bytes() == handshake
        selected["epoch"] = 2
        socket.send_bytes(b"\x04\x01\x00\x00\x00\x00\x00\x41")
        assert socket.receive()["type"] == "websocket.close"
