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
DESKTOP_ID = "desktop_" + "a" * 24
VIEWER_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def selected_viewer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    selected: dict = {"target": "cloud"}
    opened: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        overrides = selected.get("statuses", {})
        if request.url.path in overrides:
            return httpx.Response(overrides[request.url.path])
        if request.url.path.endswith("/desktops"):
            return httpx.Response(200, json=[{"desktopId": selected.get("slot", DESKTOP_ID)}])
        if request.url.path.endswith("/targets"):
            return httpx.Response(
                200,
                json={
                    "active_target": selected["target"],
                    "targets": [
                        {"target_id": name, "binding_key": key * 64}
                        for name, key in [("cloud", "c"), ("local", "a")]
                    ]
                    if selected.get("allowed", True)
                    else [],
                },
            )
        if request.url.path.endswith("/desktop"):
            return httpx.Response(
                200,
                json={
                    "state": selected.get("control", "observe"),
                    "epoch": selected.get("epoch", 1),
                    "binding_key": ("c" if selected["target"] == "cloud" else "a") * 64,
                },
            )
        return httpx.Response(
            200, json={"user": {"user_id": "owner"}, "membership": {"tenant_id": "tenant"}}
        )

    async def connect(_self, endpoint: SshEndpoint | None = None, *, slot: str | None = None):
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
        max_viewers_per_target=1,
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


def test_explicit_view_survives_execution_switch_without_inheriting_human_input(selected_viewer):
    client, selected, opened = selected_viewer
    with client.websocket_connect(
        "/computer/desktop?target=local", headers={"origin": ORIGIN}
    ) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        selected["control"] = "human"  # on cloud, never on the observed local PC
        time.sleep(0.05)
        handshake = b"RFB 003.008\n" + b"\x01\x01"
        socket.send_bytes(handshake)
        assert socket.receive_bytes() == handshake
        # An input followed by a read-only refresh must echo only the refresh.
        socket.send_bytes(b"\x04\x01\x00\x00\x00\x00\x00\x41" + b"\x03" + b"\x00" * 9)
        assert socket.receive_bytes() == b"\x03" + b"\x00" * 9
        selected["target"] = "local"
        selected["control"] = "observe"
        time.sleep(0.05)
        socket.send_bytes(b"\x03" + b"\x00" * 9)
        assert socket.receive_bytes() == b"\x03" + b"\x00" * 9
        selected["allowed"] = False
        assert socket.receive()["type"] == "websocket.close"
    assert opened == ["local"]


def test_explicit_unknown_target_never_opens_transport(selected_viewer):
    client, _, opened = selected_viewer
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/computer/desktop?target=other", headers={"origin": ORIGIN}):
            pytest.fail("unauthorized view was accepted")
    assert opened == []


def test_capacity_is_independent_for_each_authorized_target(selected_viewer):
    client, _, opened = selected_viewer
    headers = {"origin": ORIGIN}
    with client.websocket_connect("/computer/desktop?target=cloud", headers=headers) as cloud:
        assert cloud.receive_bytes() == b"RFB 003.008\n"
        with client.websocket_connect("/computer/desktop?target=local", headers=headers) as local:
            assert local.receive_bytes() == b"RFB 003.008\n"
            with pytest.raises(WebSocketDisconnect) as rejected:
                with client.websocket_connect("/computer/desktop?target=cloud", headers=headers):
                    pytest.fail("cloud exceeded its own capacity")
            assert rejected.value.code == 1013
            handshake = b"RFB 003.008\n" + b"\x01\x01"
            local.send_bytes(handshake)
            assert local.receive_bytes() == handshake
            local.close()
            assert local.receive()["type"] == "websocket.close"
        cloud.close()
        assert cloud.receive()["type"] == "websocket.close"
    assert opened == ["cloud", "local"]


def test_a_stale_human_epoch_loses_input_at_the_next_poll(selected_viewer):
    """Input is gated by the cached lease, not by a per-event API call.

    Revocation therefore lands on the next poll (one second in production,
    faster in this fixture) instead of mid-keystroke. The stale generation
    must not send anything once that poll has seen it, and the session keeps
    running in observation rather than dropping the picture.
    """

    client, selected, _ = selected_viewer
    selected["control"] = "human"
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        time.sleep(0.05)
        handshake = b"RFB 003.008\n" + b"\x01\x01"
        socket.send_bytes(handshake)
        assert socket.receive_bytes() == handshake
        # The generation moves while the socket stays on the old grant.
        selected["epoch"] = 2
        time.sleep(0.2)  # several of the connection's own poll ticks
        key = b"\x04\x01\x00\x00\x00\x00\x00\x41"
        update = b"\x03\x00\x00\x00\x00\x00\x00\x20\x00\x20"
        # The key is dropped; the read-only request still reaches the guest,
        # proving the viewer did not close the session.
        socket.send_bytes(key + update)
        assert socket.receive_bytes() == update
        assert client.get("/computer/status").json()["input_frames"]["keyboard"] == 0


@pytest.mark.parametrize("status", [429, 500])
def test_target_server_errors_keep_the_session(selected_viewer, monkeypatch, status):
    """A failed target list is unknown, not a reason to drop the viewer."""

    client, selected, opened = selected_viewer
    monkeypatch.setattr(viewer, "AUTH_GRACE_SECONDS", 0.1)
    with client.websocket_connect(
        "/computer/desktop?target=cloud", headers={"origin": ORIGIN}
    ) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        handshake = b"RFB 003.008\n" + b"\x01\x01"
        socket.send_bytes(handshake)
        assert socket.receive_bytes() == handshake
        time.sleep(0.1)  # the first poll confirms the selection
        selected["statuses"] = {"/v1/studio/assistant/computer/targets": status}
        time.sleep(0.3)  # several polls fail
        socket.send_bytes(b"\x03" + b"\x00" * 9)
        assert socket.receive_bytes() == b"\x03" + b"\x00" * 9
    assert opened == ["cloud"]


@pytest.mark.parametrize("status", [401, 403])
def test_a_target_denial_ends_the_session(selected_viewer, status):
    """An explicit denial of the target list is a logout, not a retry."""

    client, selected, opened = selected_viewer
    with client.websocket_connect(
        "/computer/desktop?target=cloud", headers={"origin": ORIGIN}
    ) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        handshake = b"RFB 003.008\n" + b"\x01\x01"
        socket.send_bytes(handshake)
        assert socket.receive_bytes() == handshake
        time.sleep(0.1)  # the first poll confirms the selection
        selected["statuses"] = {"/v1/studio/assistant/computer/targets": status}
        assert socket.receive()["type"] == "websocket.close"
    assert opened == ["cloud"]


@pytest.mark.parametrize("status", [429, 500])
def test_ownership_server_errors_keep_the_slot_session(selected_viewer, monkeypatch, status):
    """Losing the ownership answer for a while must not end the picture."""

    client, selected, opened = selected_viewer
    monkeypatch.setattr(viewer, "AUTH_GRACE_SECONDS", 0.1)
    url = f"/computer/desktop?target=cloud&desktop={DESKTOP_ID}&viewer_id={VIEWER_ID}"
    with client.websocket_connect(url, headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        handshake = b"RFB 003.008\n" + b"\x01\x01"
        socket.send_bytes(handshake)
        assert socket.receive_bytes() == handshake
        time.sleep(0.1)
        selected["statuses"] = {"/v1/studio/assistant/computer/desktops": status}
        time.sleep(0.3)
        socket.send_bytes(b"\x03" + b"\x00" * 9)
        assert socket.receive_bytes() == b"\x03" + b"\x00" * 9
    assert opened == ["cloud"]


@pytest.mark.parametrize("status", [401, 403])
def test_losing_ownership_still_ends_the_slot_session(selected_viewer, status):
    """An explicit denial is different from a server-side failure."""

    client, selected, opened = selected_viewer
    url = f"/computer/desktop?target=cloud&desktop={DESKTOP_ID}&viewer_id={VIEWER_ID}"
    with client.websocket_connect(url, headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == b"RFB 003.008\n"
        time.sleep(0.1)
        selected["statuses"] = {"/v1/studio/assistant/computer/desktops": status}
        assert socket.receive()["type"] == "websocket.close"
