"""Shared previews require owner login; input requires the current human lease."""

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
KEY_A_UP = b"\x04\x00\x00\x00\x00\x00\x00\x41"  # KeyEvent release for the same key
POINTER_EVENT = b"\x05\x01\x00\x0a\x00\x14"  # PointerEvent, left button held
POINTER_RELEASE = b"\x05\x00\x00\x0a\x00\x14"  # same point, no buttons held
VIEWER_ID = "11111111-2222-3333-4444-555555555555"
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
        override = state.get("statuses", {}).get(request.url.path)
        if override is not None:
            return httpx.Response(override)
        if request.url.path == "/v1/studio/assistant/computer/desktop":
            state["desktop_calls"] = state.get("desktop_calls", 0) + 1
            if state.get("desktop_unavailable"):
                raise httpx.ConnectError("desktop API is unreachable")
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
    monkeypatch.setattr(viewer, "AUTH_PENDING_CHECK_SECONDS", 0.02)
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
        max_viewers_per_target=2,
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


def test_binary_transport_allows_multiple_views_and_revocation_closes_them(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    state["script"] = ECHO_SCRIPT
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        wait_for_control(client, "human")
        socket.send_bytes(HANDSHAKE + UPDATE_REQUEST)
        drain(socket, HANDSHAKE + UPDATE_REQUEST)

        with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as second:
            assert second.receive_bytes() == VERSION
            assert state["connections"] == 2
            with pytest.raises(WebSocketDisconnect) as rejected:
                with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}):
                    pytest.fail("viewer capacity was exceeded")
            assert rejected.value.code == 1013
            assert state["connections"] == 2
            state["active"] = False
            assert socket.receive()["type"] == "websocket.close"
            assert second.receive()["type"] == "websocket.close"
    assert client.get("/computer/status").status_code == 403


def test_closing_one_preview_releases_capacity_without_interrupting_another(gateway):
    client, state = gateway
    state.update(script=ECHO_SCRIPT, control="observe")
    headers = {"origin": ORIGIN}
    with client.websocket_connect("/computer/desktop", headers=headers) as first:
        assert first.receive_bytes() == VERSION
        with client.websocket_connect("/computer/desktop", headers=headers) as second:
            assert second.receive_bytes() == VERSION
            second.close()
            assert second.receive()["type"] == "websocket.close"
        # Teardown terminates a child process before returning its slot.
        deadline = time.monotonic() + 5
        while True:
            try:
                with client.websocket_connect("/computer/desktop", headers=headers) as replacement:
                    assert replacement.receive_bytes() == VERSION
                    first.send_bytes(HANDSHAKE + KEY_A_DOWN + UPDATE_REQUEST)
                    drain(first, HANDSHAKE + UPDATE_REQUEST)
                    assert status_json(client)["input_frames"] == {"keyboard": 0, "pointer": 0}
                    replacement.close()
                    assert replacement.receive()["type"] == "websocket.close"
                break
            except WebSocketDisconnect as error:
                assert error.code == 1013
                assert time.monotonic() < deadline, "closed preview did not release capacity"
                time.sleep(0.02)
        assert state["connections"] == 3
        assert status_json(client)["busy"] is True
        first.close()
        assert first.receive()["type"] == "websocket.close"


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
        # on epoch 7): input pauses instead of inheriting the new generation,
        # but the socket stays open so the picture keeps flowing.
        state["epoch"] = 7
        wait_for_control(client, "human")
        time.sleep(1.2)  # several of the connection's own poll ticks
        socket.send_bytes(KEY_A_DOWN + UPDATE_REQUEST)
        # The new key was dropped; the key held from the previous generation
        # is released so the guest does not keep it down, and the read-only
        # update request still reaches the guest, proving the session lives.
        drain(socket, KEY_A_UP + UPDATE_REQUEST)
        assert status_json(client)["input_frames"]["keyboard"] == 1
        # Once control leaves human, a later takeover is a new observed
        # transition and the same socket may be granted again.
        state["control"] = "observe"
        time.sleep(0.2)
        state.update(control="human", epoch=9)
        wait_for_control(client, "human")
        time.sleep(0.3)
        socket.send_bytes(KEY_A_DOWN)
        drain(socket, KEY_A_DOWN)
        assert status_json(client)["input_frames"]["keyboard"] == 2
        state["active"] = False


def test_input_forwarding_never_round_trips_to_the_api(
    gateway: tuple[TestClient, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drag emits many events; none of them may wait on authorization I/O."""

    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state["control"] = "human"
    # A slow watcher isolates the input path: any per-event HTTP call would
    # show up as extra desktop requests within the burst below.
    monkeypatch.setattr(viewer, "AUTH_CHECK_SECONDS", 5.0)
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        # The first poll is immediate, so the grant does not wait five seconds.
        time.sleep(0.1)
        before = state["desktop_calls"]
        burst = POINTER_EVENT * 30
        socket.send_bytes(HANDSHAKE + burst)
        drain(socket, HANDSHAKE + burst)
        assert state["desktop_calls"] - before <= 0
        assert status_json(client)["input_frames"]["pointer"] == 30
    state["active"] = False


def test_a_transient_control_api_error_pauses_input_without_closing(
    gateway: tuple[TestClient, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state["control"] = "human"
    monkeypatch.setattr(viewer, "AUTH_GRACE_SECONDS", 0.1)
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE)
        drain(socket, HANDSHAKE)
        time.sleep(0.1)
        state["desktop_unavailable"] = True
        time.sleep(0.3)  # the grace window lapses while the API is down
        socket.send_bytes(KEY_A_DOWN + UPDATE_REQUEST)
        drain(socket, UPDATE_REQUEST)  # input dropped, session still alive
        assert status_json(client)["input_frames"]["keyboard"] == 0
        state["desktop_unavailable"] = False
        time.sleep(0.3)  # the next successful poll re-arms the grant
        socket.send_bytes(KEY_A_DOWN)
        drain(socket, KEY_A_DOWN)
        assert status_json(client)["input_frames"]["keyboard"] == 1
    state["active"] = False


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_login_server_error_pauses_input_without_closing(
    gateway: tuple[TestClient, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state["control"] = "human"
    monkeypatch.setattr(viewer, "AUTH_GRACE_SECONDS", 0.1)
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE)
        drain(socket, HANDSHAKE)
        time.sleep(0.1)  # the first poll confirms the human lease
        state["statuses"] = {"/v1/auth/me": status}
        time.sleep(0.3)  # the grace window lapses while the API errors
        socket.send_bytes(KEY_A_DOWN + UPDATE_REQUEST)
        drain(socket, UPDATE_REQUEST)  # input dropped, session still alive
        state["statuses"] = {}
        time.sleep(0.3)  # the next successful poll re-arms the grant
        socket.send_bytes(KEY_A_DOWN)
        drain(socket, KEY_A_DOWN)
        assert status_json(client)["input_frames"]["keyboard"] == 1
    state["active"] = False


@pytest.mark.parametrize("status", [429, 500, 503])
def test_a_desktop_server_error_pauses_input_without_closing(
    gateway: tuple[TestClient, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state["control"] = "human"
    monkeypatch.setattr(viewer, "AUTH_GRACE_SECONDS", 0.1)
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE)
        drain(socket, HANDSHAKE)
        time.sleep(0.1)
        state["statuses"] = {"/v1/studio/assistant/computer/desktop": status}
        time.sleep(0.3)
        socket.send_bytes(KEY_A_DOWN + UPDATE_REQUEST)
        drain(socket, UPDATE_REQUEST)  # input dropped, session still alive
        state["statuses"] = {}
        time.sleep(0.3)
        socket.send_bytes(KEY_A_DOWN)
        drain(socket, KEY_A_DOWN)
        assert status_json(client)["input_frames"]["keyboard"] == 1
    state["active"] = False


@pytest.mark.parametrize("status", [401, 403])
def test_a_desktop_denial_keeps_the_session_and_pauses_input(
    gateway: tuple[TestClient, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    """An explicit 4xx is no control, not a broken session: keep the picture."""

    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state["control"] = "human"
    monkeypatch.setattr(viewer, "AUTH_GRACE_SECONDS", 0.1)
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE)
        drain(socket, HANDSHAKE)
        time.sleep(0.1)
        state["statuses"] = {"/v1/studio/assistant/computer/desktop": status}
        time.sleep(0.3)
        socket.send_bytes(KEY_A_DOWN + UPDATE_REQUEST)
        drain(socket, UPDATE_REQUEST)  # input dropped, session still alive
        state["statuses"] = {}
        time.sleep(0.3)
        socket.send_bytes(KEY_A_DOWN)
        drain(socket, KEY_A_DOWN)
        assert status_json(client)["input_frames"]["keyboard"] == 1
    state["active"] = False


def test_a_pause_releases_a_held_key_and_pointer(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    """A held press must not stay down in the guest when input pauses."""

    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state["control"] = "human"
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE)
        drain(socket, HANDSHAKE)
        time.sleep(0.1)  # the first poll grants input
        socket.send_bytes(KEY_A_DOWN + POINTER_EVENT)
        drain(socket, KEY_A_DOWN + POINTER_EVENT)
        state["control"] = "observe"
        wait_for_control(client, "observe")
        # This socket's own poll observes the change and lets go immediately:
        # no further browser event is needed to avoid a stuck key or button.
        drain(socket, KEY_A_UP + POINTER_RELEASE)
        socket.send_bytes(UPDATE_REQUEST)
        drain(socket, UPDATE_REQUEST)
        assert status_json(client)["input_frames"] == {"keyboard": 1, "pointer": 1}
    state["active"] = False


def test_a_pause_synthesizes_releases_only_for_forwarded_presses() -> None:
    policy = viewer.RfbClientFilter(VERSION)
    forwarded = policy.feed(HANDSHAKE + KEY_A_DOWN + POINTER_EVENT, input_allowed=True)
    assert [kind for kind, _ in forwarded] == ["negotiate"] * 3 + ["keyboard", "pointer"]
    assert policy.feed(UPDATE_REQUEST, input_allowed=False) == [
        (viewer.RFB_RELEASE, KEY_A_UP),
        (viewer.RFB_RELEASE, POINTER_RELEASE),
        (viewer.RFB_NEGOTIATE, UPDATE_REQUEST),
    ]
    # Releasing happens once; the pause keeps the stream aligned afterwards.
    assert policy.feed(UPDATE_REQUEST, input_allowed=False) == [
        (viewer.RFB_NEGOTIATE, UPDATE_REQUEST)
    ]


def test_a_dropped_press_is_never_released() -> None:
    """The guest never saw it, so a release would press and let go for it."""

    policy = viewer.RfbClientFilter(VERSION)
    policy.feed(HANDSHAKE, input_allowed=False)
    policy.feed(KEY_A_DOWN + POINTER_EVENT, input_allowed=False)
    assert policy.feed(UPDATE_REQUEST, input_allowed=True) == [
        (viewer.RFB_NEGOTIATE, UPDATE_REQUEST)
    ]


def test_a_pause_releases_a_qemu_extended_key() -> None:
    # type 255, subtype 0, down-flag u16, keysym, keycode, padding.
    qemu = b"\xff\x00\x00\x01" + b"\x00\x00\x00\x61" + b"\x00\x00\x00\x1e" + bytes(4)
    policy = viewer.RfbClientFilter(VERSION)
    policy.feed(HANDSHAKE, input_allowed=True)
    assert policy.feed(qemu, input_allowed=True) == [("keyboard", qemu)]
    assert policy.feed(b"", input_allowed=False) == [
        (viewer.RFB_RELEASE, b"\xff\x00\x00\x00" + qemu[4:])
    ]


@pytest.mark.parametrize(
    "status,expected", [(429, 503), (500, 503), (401, 403), (403, 403)]
)
def test_status_separates_denial_from_an_unconfirmed_login(
    gateway: tuple[TestClient, dict[str, Any]], status: int, expected: int
) -> None:
    client, state = gateway
    state["statuses"] = {"/v1/auth/me": status}
    assert client.get("/computer/status").status_code == expected


@pytest.mark.parametrize("status", [429, 503])
def test_a_startup_server_error_asks_the_client_to_retry(
    gateway: tuple[TestClient, dict[str, Any]], status: int
) -> None:
    client, state = gateway
    state["statuses"] = {"/v1/auth/me": status}
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}):
            pytest.fail("socket was accepted while login was unknown")
    assert rejected.value.code == 1013
    assert state["connections"] == 0


def probe(
    client: TestClient,
    viewer_id: str = VIEWER_ID,
    state: str | None = None,
    epoch: int | None = None,
    claim: bool = False,
) -> dict[str, Any]:
    query = f"probe=1&viewer_id={viewer_id}"
    if state is not None:
        query += f"&state={state}"
    if epoch is not None:
        query += f"&epoch={epoch}"
    if claim:
        query += "&claim=1"
    payload = cast(Any, client).get(f"/computer/status?{query}").json()
    return cast(dict[str, Any], payload)


def test_the_input_probe_still_requires_a_login(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    query = f"/computer/status?probe=1&viewer_id={VIEWER_ID}"
    assert client.get(query).status_code == 200
    client.cookies.clear()
    assert client.get(query).status_code == 403


def test_the_input_probe_rejects_a_cookie_it_never_verified(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    """A cookie that exists is not a login: the probe must ask the API."""

    client, state = gateway
    query = f"/computer/status?probe=1&viewer_id={VIEWER_ID}"
    assert client.get(query).status_code == 200
    client.cookies.set("harness_access_token", "not-a-session")
    assert client.get(query).status_code == 403


def test_the_input_probe_separates_unconfirmed_from_denied(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    query = f"/computer/status?probe=1&viewer_id={VIEWER_ID}"
    # Nothing was checked yet, so this asks the API, which cannot answer.
    state["statuses"] = {"/v1/auth/me": 503}
    assert client.get(query).status_code == 503
    state["statuses"] = {"/v1/auth/me": 401}
    assert client.get(query).status_code == 403


def test_a_probe_login_answer_expires(
    gateway: tuple[TestClient, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing a login answer is bounded; a revoked session is noticed again."""

    client, state = gateway
    monkeypatch.setattr(viewer, "AUTH_PROBE_CACHE_SECONDS", 0.05)
    query = f"/computer/status?probe=1&viewer_id={VIEWER_ID}"
    assert client.get(query).status_code == 200
    state["active"] = False
    time.sleep(0.1)
    assert client.get(query).status_code == 403


def test_the_input_probe_reports_only_this_sockets_grant(
    gateway: tuple[TestClient, dict[str, Any]],
) -> None:
    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state.update(control="human", epoch=4)
    with client.websocket_connect(
        f"/computer/desktop?viewer_id={VIEWER_ID}", headers={"origin": ORIGIN}
    ) as socket:
        assert socket.receive_bytes() == VERSION
        time.sleep(0.15)  # the immediate poll grants epoch 4
        assert probe(client)["grant"] == {
            "state": "human",
            "epoch": 4,
            "input_allowed": True,
            "blocked": False,
        }
        # Another tab is never told it may type on this socket's grant.
        assert probe(client, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")["grant"][
            "input_allowed"
        ] is False
        # The answer is the last confirmed grant, not a fresh API read: an
        # unreachable API leaves it intact.
        state["desktop_unavailable"] = True
        time.sleep(0.2)
        assert probe(client)["grant"]["input_allowed"] is True
        state["desktop_unavailable"] = False
        # A generation this socket did not observe is reported as blocked.
        state["epoch"] = 5
        time.sleep(0.2)
        grant = probe(client)["grant"]
        assert grant["input_allowed"] is False
        assert grant["blocked"] is True
    state["active"] = False


def test_a_control_probe_wakes_the_steady_poll_for_a_takeover(
    gateway: tuple[TestClient, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The browser reading control state must not wait out the 1s cadence."""

    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state.update(control="human", epoch=1)
    monkeypatch.setattr(viewer, "AUTH_CHECK_SECONDS", 5.0)
    with client.websocket_connect(
        f"/computer/desktop?viewer_id={VIEWER_ID}", headers={"origin": ORIGIN}
    ) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE)
        drain(socket, HANDSHAKE)
        time.sleep(0.1)  # the immediate poll confirms epoch 1
        assert probe(client)["grant"]["input_allowed"] is True
        time.sleep(0.05)  # let the wake rate limit pass
        state["control"] = "takeover_pending"
        # The browser asks while the handoff is in flight; the socket still
        # believes it is human, so this is the hint that starts fast polling
        # before the lease lands on a new generation.
        probe(client, state="takeover_pending")
        time.sleep(0.05)
        state.update(control="human", epoch=2)
        deadline = time.monotonic() + 2
        while not probe(client)["grant"]["input_allowed"]:
            assert time.monotonic() < deadline, "takeover waited for the steady cadence"
            time.sleep(0.02)
        socket.send_bytes(KEY_A_DOWN)
        drain(socket, KEY_A_DOWN)
    state["active"] = False


def test_a_claimed_fast_drain_is_still_granted(
    gateway: tuple[TestClient, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A drain shorter than one poll must not block the tab that asked for it."""

    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state.update(control="human", epoch=1)
    monkeypatch.setattr(viewer, "AUTH_CHECK_SECONDS", 5.0)
    with client.websocket_connect(
        f"/computer/desktop?viewer_id={VIEWER_ID}", headers={"origin": ORIGIN}
    ) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE)
        drain(socket, HANDSHAKE)
        time.sleep(0.1)  # the immediate poll grants epoch 1
        assert probe(client)["grant"]["input_allowed"] is True
        time.sleep(0.05)  # let the wake rate limit pass
        # The lease moves to epoch 2 with no observable pending phase: the tab
        # that requested the takeover records its claim, so the socket it owns
        # is granted instead of being treated as a bystander.
        state["epoch"] = 2
        probe(client, state="human", epoch=2, claim=True)
        deadline = time.monotonic() + 2
        while not probe(client)["grant"]["input_allowed"]:
            assert time.monotonic() < deadline, "claimed generation was not granted"
            assert probe(client)["grant"]["blocked"] is False
            time.sleep(0.02)
        socket.send_bytes(KEY_A_DOWN)
        drain(socket, KEY_A_DOWN)
        assert status_json(client)["input_frames"]["keyboard"] == 1
    state["active"] = False


def test_a_claim_arms_only_the_generation_it_named(
    gateway: tuple[TestClient, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim is a licence for the epoch it asked for, not "the next one"."""

    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state.update(control="human", epoch=1)
    monkeypatch.setattr(viewer, "AUTH_CHECK_SECONDS", 5.0)
    with client.websocket_connect(
        f"/computer/desktop?viewer_id={VIEWER_ID}", headers={"origin": ORIGIN}
    ) as socket:
        assert socket.receive_bytes() == VERSION
        socket.send_bytes(HANDSHAKE)
        drain(socket, HANDSHAKE)
        time.sleep(0.1)  # the immediate poll grants epoch 1
        assert probe(client)["grant"]["input_allowed"] is True
        time.sleep(0.05)
        # This tab claims the takeover it asked for at epoch 2, but the lease
        # lands on epoch 3: the stale claim must not hand it epoch 3.
        state["epoch"] = 2
        probe(client, state="human", epoch=2, claim=True)
        state["epoch"] = 3
        deadline = time.monotonic() + 2
        while True:
            grant = probe(client, state="human", epoch=3)["grant"]
            if grant["epoch"] == 3:
                break
            assert time.monotonic() < deadline, "the socket never observed epoch 3"
            time.sleep(0.02)
        assert grant["input_allowed"] is False
        assert grant["blocked"] is True
    state["active"] = False


def test_a_pending_takeover_is_confirmed_on_the_fast_cadence(
    gateway: tuple[TestClient, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, state = gateway
    state["script"] = ECHO_SCRIPT
    state.update(control="takeover_pending", epoch=1)
    # Five seconds is the ordinary cadence; only the pending state may poll
    # faster, otherwise this grant would take five seconds.
    monkeypatch.setattr(viewer, "AUTH_CHECK_SECONDS", 5.0)
    with client.websocket_connect("/computer/desktop", headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == VERSION
        time.sleep(0.1)  # the immediate poll observed pending
        state.update(control="human", epoch=2)
        time.sleep(0.3)
        socket.send_bytes(HANDSHAKE + KEY_A_DOWN)
        drain(socket, HANDSHAKE + KEY_A_DOWN)
        assert status_json(client)["input_frames"]["keyboard"] == 1
    state["active"] = False


def test_a_registry_claim_is_bound_to_its_epoch() -> None:
    registry = viewer.GrantRegistry()
    assert registry.claim("slot|tab", 3) is True
    # Another generation moves past it: the claim is dropped, not banked.
    assert registry.take_claim("slot|tab", 4) is False
    assert registry.take_claim("slot|tab", 3) is False
    # A claim without a generation would be a licence for whichever arrived.
    assert registry.claim("slot|tab", None) is False
    assert registry.take_claim("slot|tab", 0) is False


def test_a_registry_claim_expires() -> None:
    registry = viewer.GrantRegistry(claim_ttl=0.01)
    assert registry.claim("slot|tab", 3) is True
    time.sleep(0.02)
    assert registry.take_claim("slot|tab", 3) is False


def test_a_registry_prunes_abandoned_claims_and_grants() -> None:
    """A tab that vanished must not grow the map without bound."""

    registry = viewer.GrantRegistry(claim_ttl=0.01)
    assert registry.claim("slot|tab", 3) is True
    registry.observe(
        "slot|gone",
        state="human",
        epoch=1,
        input_allowed=True,
        blocked=False,
        valid_until=time.monotonic() + 0.01,
    )
    assert len(registry) == 2
    time.sleep(0.02)
    assert len(registry) == 0
    # A live socket is never pruned by idleness: its watcher writes each poll.
    registry.observe(
        "slot|live",
        state="human",
        epoch=1,
        input_allowed=True,
        blocked=False,
        valid_until=time.monotonic() + 0.01,
        connection=object(),
    )
    time.sleep(0.02)
    assert len(registry) == 1
    assert registry.peek("slot|live").epoch == 1


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
    # Exactly the three handshake messages reach the guest; nothing else,
    # and nothing is echoed back to the client either.
    assert forwarded == [VERSION, b"\x01", b"\x01"]
    assert [kind for kind, _ in messages] == ["negotiate", "negotiate", "negotiate"]
    assert not policy.resync_required
    # And the stream is still aligned: a following request parses cleanly.
    follow = struct.pack(">BBHHHH", 3, 0, 0, 0, 1280, 800)
    assert policy.feed(follow, input_allowed=False)[-1] == ("negotiate", follow)


def test_a_truly_undefined_client_message_still_fails_closed():
    from harness.computer.viewer import RfbClientFilter

    policy = RfbClientFilter(VERSION)
    with pytest.raises(ValueError):
        policy.feed(HANDSHAKE + b"\x51\x00\x00", input_allowed=True)


def test_novnc_clipboard_caps_survive_fragmentation_in_observe_mode():
    # Exact noVNC Caps response to x11vnc's ExtendedClipboard advertisement:
    # signed length -8, all supported actions, one text format with size zero.
    caps = bytes.fromhex("06000000fffffff81f00000100000000")
    policy = viewer.RfbClientFilter(VERSION)
    assert policy.feed(HANDSHAKE, input_allowed=False)
    assert policy.feed(caps[:7], input_allowed=False) == []
    assert policy.feed(caps[7:10], input_allowed=False) == []
    assert policy.feed(caps[10:] + UPDATE_REQUEST, input_allowed=False) == [
        (viewer.RFB_NEGOTIATE, caps),
        (viewer.RFB_NEGOTIATE, UPDATE_REQUEST),
    ]


@pytest.mark.parametrize("allowed", [False, True])
def test_extended_clipboard_content_still_requires_human_control(allowed: bool):
    payload = struct.pack(">I", 1 << 28 | 1) + b"compressed clipboard"
    message = struct.pack(">B3xi", 6, -len(payload)) + payload
    policy = viewer.RfbClientFilter(VERSION)
    policy.feed(HANDSHAKE, input_allowed=allowed)
    expected = [(viewer.RFB_CUT, message)] if allowed else []
    assert policy.feed(message + UPDATE_REQUEST, input_allowed=allowed) == [
        *expected,
        (viewer.RFB_NEGOTIATE, UPDATE_REQUEST),
    ]


@pytest.mark.parametrize("length", [65536, -65536, -(1 << 31), -3])
def test_clipboard_length_limits_apply_to_signed_extensions(length: int):
    policy = viewer.RfbClientFilter(VERSION)
    policy.feed(HANDSHAKE, input_allowed=False)
    with pytest.raises(ValueError):
        policy.feed(struct.pack(">B3xiI", 6, length, 0), input_allowed=False)
