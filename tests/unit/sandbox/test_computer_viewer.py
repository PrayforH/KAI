"""Browser access needs a live owner session, matching origin and sole input."""

import asyncio
import struct
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

# A complete RFB 3.8 client handshake with the None security type.
VERSION = b"RFB 003.008\n"
HANDSHAKE = VERSION + b"\x01" + b"\x01"  # version reply, security choice, ClientInit
UPDATE_REQUEST = b"\x03\x00\x00\x00\x00\x00\x00\x20\x00\x20"  # FramebufferUpdateRequest
KEY_A_DOWN = b"\x04\x01\x00\x00\x00\x00\x00\x41"  # KeyEvent, keysym 'a'
POINTER_EVENT = b"\x05\x01\x00\x0a\x00\x14"  # PointerEvent, left button held
# A complete SetPixelFormat whose first bytes happen to collide with a
# half-sent pointer event's tail: negotiation is read-only and passes.
PIXEL_FORMAT_TAIL = bytes(20)

ECHO_SCRIPT = """import os
os.write(1, b'RFB 003.008\\n')
while True:
    data = os.read(0, 4096)
    if not data:
        break
    os.write(1, data)
"""


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
        script = state.get(
            "script",
            "import os; os.write(1,b'RFB 003.008\\n'); "
            "data=os.read(0,4); os.write(1,data); os.read(0,1)",
        )
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            script,
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
        assert socket.receive_bytes() == VERSION
        wait_for_control(client, "human")
        socket.send_bytes(HANDSHAKE + UPDATE_REQUEST)
        # The child echoes the first four bytes it receives, completing the
        # round trip through protocol-valid traffic.
        assert socket.receive_bytes() == VERSION[:4]

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}):
                pytest.fail("a second input owner was accepted")
        assert state["connections"] == 1
        state["active"] = False
        assert socket.receive()["type"] == "websocket.close"
    assert client.get("/computer/status").status_code == 403


def drain(socket: Any, expected: bytes) -> bytes:
    """Collect echoed bytes until the expected stream length is reached."""

    received = bytearray()
    deadline = time.monotonic() + 5
    while len(received) < len(expected):
        assert time.monotonic() < deadline, "desktop echo stalled"
        received.extend(socket.receive_bytes())
    assert bytes(received) == expected
    return bytes(received)


def test_observation_gate_drops_input_and_forwards_after_takeover(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state["control"] = "observe"
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        # Observation must keep the read-only protocol alive: the handshake
        # crosses in complete messages even though the lease is not human.
        socket.send_bytes(HANDSHAKE[:17])
        socket.send_bytes(HANDSHAKE[17:])
        socket.send_bytes(POINTER_EVENT)  # a complete input event: dropped
        socket.send_bytes(UPDATE_REQUEST)
        drain(socket, HANDSHAKE + UPDATE_REQUEST)
        assert status_json(client)["input_frames"] == {"keyboard": 0, "pointer": 0}

        state["control"] = "human"
        wait_for_control(client, "human")
        socket.send_bytes(KEY_A_DOWN)
        socket.send_bytes(POINTER_EVENT)
        drain(socket, KEY_A_DOWN + POINTER_EVENT)
        assert status_json(client)["input_frames"] == {"keyboard": 1, "pointer": 1}
        state["active"] = False
        assert socket.receive()["type"] == "websocket.close"


def test_input_bytes_from_observation_never_complete_after_a_takeover(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state["control"] = "observe"
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE)
        drain(socket, HANDSHAKE)
        socket.send_bytes(POINTER_EVENT[:2])  # half an event while observing
        state["control"] = "human"
        wait_for_control(client, "human")
        # Completing a half-sent event would inject bytes typed by the
        # previous state and eating its tail would desync the stream, so the
        # viewer closes the connection instead; the browser reconnects with
        # a clean handshake, which every other test re-exercises.
        socket.send_bytes(UPDATE_REQUEST)
        try:
            closed = socket.receive()
            assert closed["type"] in {"websocket.close", "websocket.disconnect"}
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass  # the test client surfaces the teardown in several shapes
        assert status_json(client)["input_frames"] == {"keyboard": 0, "pointer": 0}


def test_unknown_client_messages_fail_closed(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    state["script"] = ECHO_SCRIPT
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE + b"\x51\x00\x00")  # undefined message type
        assert socket.receive()["type"] == "websocket.close"
    state["active"] = False


def test_unauthenticated_socket_never_opens_ssh(gateway: tuple[TestClient, dict[str, Any]]):
    client, state = gateway
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}):
            pytest.fail("anonymous socket was accepted")
    assert state["connections"] == 0


def test_input_grant_never_survives_an_unobserved_generation(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state["control"] = "observe"
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE)
        drain(socket, HANDSHAKE)
        # Grant: this connection observes the human generation itself.
        state["control"] = "human"
        state["epoch"] = 5
        wait_for_control(client, "human")
        time.sleep(0.3)  # the connection's own poll picks up epoch 5
        socket.send_bytes(KEY_A_DOWN)
        drain(socket, KEY_A_DOWN)
        # The lease moves under the connection (re-takeover elsewhere landed
        # on epoch 7): input stops and the connection closes instead of
        # inheriting the new generation.
        state["epoch"] = 7
        wait_for_control(client, "human")
        time.sleep(1.2)  # several of the connection's own poll ticks
        socket.send_bytes(KEY_A_DOWN)
        try:
            closed = socket.receive()
            assert closed["type"] in {"websocket.close", "websocket.disconnect"}
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass  # the test client surfaces the teardown in several shapes
        assert status_json(client)["input_frames"]["keyboard"] == 1
        state["active"] = False


def test_client_extensions_are_aligned_but_never_forwarded():
    """noVNC sends Fence, EnableContinuousUpdates, xvp and SetDesktopSize.

    Refusing them disconnected every real browser; forwarding them made the
    guest (~1s later) drop the client because its x11vnc implements none of
    them. They must be parsed for alignment and then discarded.
    """

    from harness.computer.viewer import RfbClientFilter

    fence = b"\xf8\x00\x00\x00\x00\x00\x00\x01\x05hello"  # type, pad, flags, len, data
    continuous = b"\x96\x01\x00\x00\x00\x00\x05\x00\x03\x20"  # enable + rect
    xvp = b"\xfa\x01\x00\x00"
    desktop_size = b"\xfb\x00\x01\x90\x02\x58\x01\x00" + bytes(16)  # 400x600, 1 screen
    pipeline = fence + continuous + xvp + desktop_size

    policy = RfbClientFilter(VERSION)
    messages = policy.feed(HANDSHAKE + pipeline, input_allowed=False)
    forwarded = [chunk for kind, chunk in messages if kind != "discard"]
    # Exactly the three handshake messages reach the guest; nothing else.
    assert forwarded == [VERSION, b"\x01", b"\x01"]
    assert len(messages) == 7
    assert not policy.resync_required
    # And the stream is still aligned: a following request parses cleanly.
    follow = struct.pack(">BBHHHH", 3, 0, 0, 0, 1280, 800)
    assert policy.feed(follow, input_allowed=False)[-1] == ("negotiate", follow)


def test_a_truly_undefined_client_message_still_fails_closed():
    from harness.computer.viewer import RfbClientFilter

    policy = RfbClientFilter(VERSION)
    with pytest.raises(ValueError):
        policy.feed(HANDSHAKE + b"\x51\x00\x00", input_allowed=True)
