"""Account-authenticated private desktop preview, isolated from Run execution.

This first viewer owns no lifecycle or Docker socket. Its fixed target and SSH
identity are node configuration. The API remains the authority for each login
and membership; browser input never selects a host, key, user, or command.
"""

import asyncio
import contextlib
import ipaddress
import logging
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

from harness.computer.ssh_transport import SshEndpoint
from harness.computer.vm_keys import VmSeedStore

AUTH_CHECK_SECONDS = 1
AUTH_GRACE_SECONDS = 3
RFB_FRAME_LIMIT = 65536

# Client message kinds. "negotiate" traffic (handshake, pixel format,
# encodings, refresh requests) is read-only and keeps the observation stream
# alive; the rest writes to the desktop and needs a confirmed human owner.
RFB_NEGOTIATE = "negotiate"
# A message the guest must not receive; consumed so the stream stays aligned.
RFB_DISCARD = "discard"
RFB_KEYBOARD = "keyboard"
RFB_POINTER = "pointer"
RFB_CUT = "cut"

# Fixed client message spans after the handshake, by RFB type. Besides the
# core protocol this must cover what real clients actually send: QEMU's
# extended key event (255), EnableContinuousUpdates (150) and xvp (250) that
# noVNC uses, and SetDesktopSize (251) when the client may resize. An
# unrecognized type is still refused, but a client must never be disconnected
# for speaking standard RFB.
_RFB_FIXED_SPANS = {0: 20, 3: 10, 4: 8, 5: 6}
_RFB_INPUT_LABELS = {4: RFB_KEYBOARD, 5: RFB_POINTER}
_RFB_QEMU_EXTENDED = 255
_RFB_FENCE = 248
_RFB_SET_DESKTOP_SIZE = 251
# Extension messages a client may send that this deployment's guest does not
# implement (its x11vnc reports them unsupported). They are parsed for stream
# alignment but never forwarded: handing a server an extension it does not
# support makes it drop the client, which is what a live browser hit ~1s in.
_RFB_LOCAL_EXTENSIONS = {150: "EnableContinuousUpdates", 250: "xvp"}


class RfbClientFilter:
    """Splits the browser's byte stream into complete RFB client messages.

    Splitting happens on protocol boundaries, never on WebSocket fragment
    edges: one incoming frame may carry several messages and one message may
    span frames. An input message (keyboard, pointer, clipboard) is decided
    when it completes: while input is not allowed the whole message is
    consumed and dropped, which keeps the stream aligned. The one unsafe case
    is a half-sent input message crossing a takeover boundary — completing it
    would inject bytes typed by the previous owner, and eating its tail would
    desync the stream — so the filter asks the caller to close the connection
    and let the browser reconnect with a clean handshake. Anything the
    protocol does not define fails the connection instead of forwarding
    unknown bytes to the desktop.
    """

    def __init__(self, server_banner: bytes) -> None:
        # "RFB 003.008\n": the seven version digits start at offset 4.
        self._modern = server_banner[4:11] >= b"003.007"
        self._handshake: list[int] = [12]
        self._client_init = False  # the queued final byte is the shared flag
        self._buffer = bytearray()
        self._pos = 0
        self._span: int | None = None
        self._label: str | None = None
        self._handshake_message = False
        self._input_allowed = False
        self._started = False
        self.resync_required = False

    def feed(self, chunk: bytes, *, input_allowed: bool) -> list[tuple[str, bytes]]:
        became_allowed = input_allowed and self._started and not self._input_allowed
        self._input_allowed = input_allowed
        self._started = True
        if became_allowed and self._pending_input():
            self.resync_required = True
            return []
        self._buffer.extend(chunk)
        if len(self._buffer) > RFB_FRAME_LIMIT * 2:
            raise ValueError("desktop input exceeded the message buffer")
        messages: list[tuple[str, bytes]] = []
        while True:
            item = self._next()
            if item is None:
                break
            if item[0] is not None:
                messages.append((item[0], item[1]))
        del self._buffer[: self._pos]
        self._pos = 0
        return messages

    def _pending_input(self) -> bool:
        return self._span is not None and self._label in {
            RFB_KEYBOARD,
            RFB_POINTER,
            RFB_CUT,
        }

    def _next(self) -> tuple[str | None, bytes] | None:
        if self._span is None:
            if self._handshake:
                self._span, self._label = self._handshake[0], RFB_NEGOTIATE
                self._handshake_message = True
            else:
                if len(self._buffer) - self._pos < 1:
                    return None
                self._open()
                if self._span is None:
                    return None  # the message header is still incomplete
        if len(self._buffer) - self._pos < self._span:
            return None
        message = bytes(self._buffer[self._pos : self._pos + self._span])
        self._pos += self._span
        label = self._label
        self._span = self._label = None
        if self._handshake_message:
            self._handshake_message = False
            self._handshake.pop(0)
            self._advance_handshake(message)
            return RFB_NEGOTIATE, message
        if label == RFB_DISCARD:
            return None, message  # consumed for alignment, never forwarded
        if label is not None and label != RFB_NEGOTIATE and not self._input_allowed:
            return None, message  # input without a confirmed human owner
        return label or RFB_NEGOTIATE, message

    def _advance_handshake(self, message: bytes) -> None:
        if self._handshake:
            return  # e.g. a VNC authentication response span is already queued
        if self._client_init:
            self._client_init = False  # the shared flag ends every handshake
            return
        if len(message) == 12:
            if not message.startswith(b"RFB 003."):
                raise ValueError("desktop client sent an invalid version reply")
            if self._modern:
                self._handshake.append(1)  # security type choice
            else:
                self._queue_client_init()
            return
        if len(message) == 1 and message[0] == 2:
            self._handshake.append(16)  # VNC authentication challenge response
            return
        if len(message) == 1 or len(message) == 16:
            self._queue_client_init()  # a non-VNC choice, or the VNC response
            return
        raise ValueError("desktop client handshake left the negotiated protocol")

    def _queue_client_init(self) -> None:
        self._handshake.append(1)
        self._client_init = True

    def _open(self) -> None:
        kind = self._buffer[self._pos]
        if kind in _RFB_FIXED_SPANS:
            span, label = _RFB_FIXED_SPANS[kind], _RFB_INPUT_LABELS.get(kind, RFB_NEGOTIATE)
        elif kind == 2:
            if len(self._buffer) - self._pos < 4:
                return
            span = 4 + 4 * int.from_bytes(self._buffer[self._pos + 2 : self._pos + 4], "big")
            label = RFB_NEGOTIATE
        elif kind == 6:
            if len(self._buffer) - self._pos < 8:
                return
            # ExtendedClipboard uses a *negative signed* ClientCutText length.
            # noVNC answers the server's capabilities even in view-only mode;
            # reading -8 as uint32 used to disconnect every actual browser.
            length = int.from_bytes(self._buffer[self._pos + 4 : self._pos + 8], "big", signed=True)
            span = 8 + abs(length)
            label = RFB_CUT
            if length < 0:
                if abs(length) < 4:
                    raise ValueError("desktop clipboard extension has no flags")
                if len(self._buffer) - self._pos < 12:
                    return
                flags = int.from_bytes(self._buffer[self._pos + 8 : self._pos + 12], "big")
                if flags & (1 << 24):  # Caps declares formats, it does not paste.
                    if abs(length) != 4 + 4 * (flags & 0xFFFF).bit_count():
                        raise ValueError("desktop clipboard capabilities length is invalid")
                    label = RFB_NEGOTIATE
        elif kind == _RFB_QEMU_EXTENDED:
            if len(self._buffer) - self._pos < 2:
                return
            if self._buffer[self._pos + 1] != 0:
                raise ValueError("desktop client sent an unknown extended key event")
            span, label = 16, RFB_KEYBOARD
        elif kind in _RFB_LOCAL_EXTENSIONS:
            span = 10 if kind == 150 else 4
            label = RFB_DISCARD
        elif kind == _RFB_FENCE:
            # ClientFence: type, 3-byte pad, flags, 1-byte length, payload.
            if len(self._buffer) - self._pos < 9:
                return
            span = 9 + self._buffer[self._pos + 8]
            label = RFB_DISCARD
        elif kind == _RFB_SET_DESKTOP_SIZE:
            # SetDesktopSize: type, pad, w, h, screens, pad, 16 bytes/screen.
            if len(self._buffer) - self._pos < 8:
                return
            screens = self._buffer[self._pos + 6]
            span = 8 + 16 * screens
            label = RFB_DISCARD
        else:
            raise ValueError("desktop client sent an unknown RFB message type")
        if span < 1 or span > RFB_FRAME_LIMIT:
            raise ValueError("desktop client message length is out of range")
        self._span, self._label = span, label


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
    targets: dict[str, SshEndpoint] = Field(default_factory=dict)
    max_viewers_per_target: int = Field(default=16, ge=1, le=32)
    # Targets whose guest gate understands a leading desktop_<id> line.
    desktop_header_targets: frozenset[str] = frozenset({"cloud"})

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

    async def connect(
        self, endpoint: SshEndpoint | None = None, *, slot: str | None = None
    ) -> asyncio.subprocess.Process:
        if endpoint is not None:
            return await asyncio.create_subprocess_exec(
                *endpoint.argv(),
                *([slot] if slot is not None else []),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
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
    sessions: dict[str | None, int] = {}
    input_connections: dict[str, WebSocket] = {}

    @asynccontextmanager
    async def preview_slot(target: str | None) -> AsyncGenerator[bool]:
        # Tabs may observe the same desktop concurrently. Input still needs
        # the canonical human lease and a current epoch on every message.
        # Admission has no await between checking and reserving a slot.
        active = sessions.get(target, 0)
        if active >= settings.max_viewers_per_target:
            yield False
            return
        sessions[target] = active + 1
        try:
            yield True
        finally:
            sessions[target] -= 1

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


    async def owns_desktop(token: str | None, slot: str) -> bool:
        if not token or re.fullmatch(r"desktop_[a-f0-9]{24}", slot) is None:
            return False
        try:
            response = await auth.get(
                "/v1/studio/assistant/computer/desktops",
                headers={"Authorization": "Bearer " + token},
                timeout=3,
            )
            rows = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return False
        return isinstance(rows, list) and any(
            isinstance(row, dict) and row.get("desktopId") == slot for row in rows
        )

    async def status(request: Request) -> dict[str, object]:
        token = request.cookies.get(settings.cookie_name)
        if not token and request.headers.get("authorization", "").startswith("Bearer "):
            token = request.headers["authorization"][7:]
        if not await authorized(token):
            raise HTTPException(403, "Desktop access requires its owner login")
        control = await desktop_state(token)
        return {
            "enabled": True,
            "mode": "preview",
            "busy": any(count > 0 for count in sessions.values()),
            "input_frames": dict(input_frames),
            "desktop_url": settings.public_origin + "/assistant/computer",
            "control": control,
        }

    app.add_api_route("/computer/status", status, methods=["GET"])

    async def desktop_state(
        token: str | None, *, refresh: bool = True, binding: str | None = None,
        slot: str | None = None, viewer_id: str | None = None,
    ) -> dict[str, str | int]:
        """The current desktop lease decides who may type or click."""

        observe: dict[str, str | int] = {"state": "observe", "epoch": 0}
        if not token or (slot is not None and viewer_id is None):
            return observe
        params = {"desktop_id": slot} if slot is not None else {}
        try:
            response = await auth.get(
                "/v1/studio/assistant/computer/desktop",
                params=params or None,
                headers={"Authorization": "Bearer " + token},
                timeout=3,
            )
            if response.status_code != 200:
                return observe
            value: dict[str, Any] = response.json()
            if binding is not None and value.get("binding_key") != binding:
                return observe
            state = str(value.get("state", "observe"))
            if slot is not None and (
                value.get("desktop_id") != slot or value.get("owner") != viewer_id
            ):
                return observe
            if state == "human" and refresh:
                # An open viewer is proof the owner is present; refresh the
                # takeover TTL so an active session never lapses mid-work.
                with contextlib.suppress(httpx.HTTPError, ValueError):
                    if binding is not None:
                        params["binding_key"] = binding
                    if viewer_id is not None:
                        params["viewer_id"] = viewer_id
                    await auth.post(
                        "/v1/studio/assistant/computer/desktop/heartbeat",
                        params=params or None,
                        headers={"Authorization": "Bearer " + token},
                        timeout=3,
                    )
            return {"state": state, "epoch": int(value.get("epoch") or 0)}
        except (httpx.HTTPError, ValueError, TypeError):
            return observe

    async def active_endpoint(
        token: str | None, requested: str | None = None
    ) -> tuple[str | None, SshEndpoint | None, str | None]:
        if not settings.targets:
            if requested is not None:
                raise RuntimeError("requested desktop is not served")
            return None, None, None
        response = await auth.get(
            "/v1/studio/assistant/computer/targets",
            headers={"Authorization": "Bearer " + (token or "")},
            timeout=3,
        )
        if response.status_code != 200:
            raise RuntimeError("desktop selection is unavailable")
        value = response.json()
        target = requested if requested is not None else value.get("active_target")
        allowed = next(
            (item for item in value.get("targets", []) if item.get("target_id") == target), None
        )
        if requested is not None and allowed is None:
            raise RuntimeError("requested desktop is not allowed")
        endpoint = settings.targets.get(target) if isinstance(target, str) else None
        if endpoint is None:
            raise RuntimeError("selected desktop is not served")
        binding = allowed.get("binding_key") if allowed else None
        if not isinstance(binding, str) or not re.fullmatch(r"[a-f0-9]{64}", binding):
            raise RuntimeError("requested desktop has no verified binding")
        return target, endpoint, binding

    async def stream(websocket: WebSocket) -> None:
        token = websocket.cookies.get(settings.cookie_name)
        if websocket.headers.get("origin") not in settings.origins:
            logging.getLogger(__name__).warning("desktop connection refused: untrusted origin")
            await websocket.close(code=1008)
            return
        if not await authorized(token):
            logging.getLogger(__name__).warning("desktop connection refused: owner login required")
            await websocket.close(code=1008)
            return
        requested = websocket.query_params.get("target")
        try:
            target_id, endpoint, binding = await active_endpoint(token, requested)
        except (RuntimeError, httpx.HTTPError, ValueError):
            await websocket.close(code=1008)
            return
        async with preview_slot(target_id) as acquired:
            if not acquired:
                logging.getLogger(__name__).warning(
                    "desktop connection refused: viewer capacity reached"
                )
                await websocket.close(code=1013)
                return
            control: dict[str, str | int] = {"state": "observe", "epoch": 0}
            # The input generation this connection was granted at. It is set
            # only by this connection's own poll observing ``human``, so a
            # connection never inherits input rights across a takeover,
            # return or expiry it did not observe itself.
            granted_epoch: int | None = None
            process: asyncio.subprocess.Process | None = None
            tasks: list[asyncio.Task[None]] = []
            slot = websocket.query_params.get("desktop")
            viewer_id = websocket.query_params.get("viewer_id")
            if viewer_id is not None and not re.fullmatch(
                r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", viewer_id
            ):
                await websocket.close(code=1008)
                return
            try:
                if slot is not None:
                    if (endpoint is None or target_id not in settings.desktop_header_targets
                            or not await owns_desktop(token, slot)):
                        await websocket.close(code=1008)
                        return
                    process = await desktop.connect(endpoint, slot=slot)
                else:
                    process = (
                        await desktop.connect(endpoint)
                        if endpoint is not None
                        else await desktop.connect()
                    )
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
                    policy = RfbClientFilter(header)
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            logging.getLogger(__name__).info(
                                "desktop client closed the input stream (code %s)",
                                message.get("code"),
                            )
                            return
                        data = message.get("bytes")
                        if not isinstance(data, bytes) or len(data) > RFB_FRAME_LIMIT:
                            raise ValueError("desktop input exceeds frame size")
                        # Check immediately before forwarding anything.
                        if time.monotonic() >= valid_until:
                            logging.getLogger(__name__).warning(
                                "desktop input closed: authorization was not refreshed"
                            )
                            return
                        for kind, chunk in policy.feed(
                            data,
                            input_allowed=(
                                control["state"] == "human"
                                and granted_epoch == control["epoch"]
                            ),
                        ):
                            if kind == RFB_DISCARD:
                                continue
                            if kind in {RFB_KEYBOARD, RFB_POINTER, RFB_CUT}:
                                # Observation polls are cached, but an input must
                                # prove its current epoch before reaching a PC.
                                # Returning or switching elsewhere invalidates a
                                # previously granted socket immediately.
                                latest = await desktop_state(
                                    token, refresh=False, binding=binding, slot=slot,
                                    viewer_id=viewer_id,
                                )
                                if latest["state"] != "human" or latest["epoch"] != granted_epoch:
                                    raise ValueError("desktop input ownership changed")
                            if kind == RFB_KEYBOARD:
                                input_frames["keyboard"] += 1
                            elif kind == RFB_POINTER:
                                input_frames["pointer"] += 1
                            input_stream.write(chunk)
                            # A client that vanishes mid-frame leaves the SSH
                            # window full; an unbounded drain would sit here
                            # forever and the disconnect would never be seen.
                            await asyncio.wait_for(input_stream.drain(), timeout=5)
                        if policy.resync_required:
                            # A half-sent input message crossed the takeover
                            # boundary; only a fresh connection can realign
                            # the protocol stream.
                            raise ValueError("desktop input crossed a control change")

                async def watch_authorization() -> None:
                    nonlocal valid_until, granted_epoch
                    log = logging.getLogger(__name__)
                    while True:
                        await asyncio.sleep(AUTH_CHECK_SECONDS)
                        began = time.monotonic()
                        if not await authorized(token):
                            log.info("desktop session ended: login is no longer valid")
                            return
                        selected, current_endpoint, current_binding = await active_endpoint(
                            token, requested
                        )
                        if (selected, current_endpoint, current_binding) != (
                            target_id,
                            endpoint,
                            binding,
                        ):
                            log.info("desktop session ended: the selected desktop changed")
                            return
                        if slot is not None:
                            if not await owns_desktop(token, slot):
                                log.info("desktop session ended: the viewer no longer owns it")
                                return
                        may_refresh = slot is None or input_connections.get(slot) is websocket
                        previous = (control["state"], control["epoch"])
                        control.update(await desktop_state(
                            token, binding=binding, slot=slot, viewer_id=viewer_id,
                            refresh=may_refresh,
                        ))
                        if (control["state"], control["epoch"]) != previous:
                            log.info(
                                "desktop control: %s -> %s (epoch %s)",
                                previous, control["state"], control["epoch"],
                            )
                        if control["state"] == "human":
                            if (
                                slot is not None
                                and input_connections.setdefault(slot, websocket) is not websocket
                            ):
                                granted_epoch = None
                                valid_until = time.monotonic() + AUTH_GRACE_SECONDS
                                continue
                            epoch = int(control.get("epoch") or 0)
                            if granted_epoch is None:
                                granted_epoch = epoch
                            elif granted_epoch != epoch:
                                # The generation moved under this connection
                                # (return and re-takeover elsewhere); its
                                # grant is stale, so reconnect to re-bind.
                                raise ValueError("desktop input generation changed")
                        else:
                            if slot is not None and input_connections.get(slot) is websocket:
                                input_connections.pop(slot)
                            granted_epoch = None
                        valid_until = time.monotonic() + AUTH_GRACE_SECONDS
                        # The grace window is three seconds; a poll that takes
                        # longer than that can close a session under the owner.
                        elapsed = time.monotonic() - began
                        if elapsed >= AUTH_GRACE_SECONDS:
                            log.warning(
                                "desktop authorization poll took %.1fs; input may lapse",
                                elapsed,
                            )

                tasks = [
                    asyncio.create_task(job(), name=job.__name__)
                    for job in (to_browser, to_desktop, watch_authorization)
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                # One line naming the task that ended the session and why; the
                # client never sees this, but a silent close is undebuggable.
                for task in done:
                    if task.exception() is not None:
                        logging.getLogger(__name__).warning(
                            "desktop session ended by %s: %s: %s",
                            task.get_name(),
                            type(task.exception()).__name__,
                            str(task.exception())[:200],
                        )
                    else:
                        logging.getLogger(__name__).warning(
                            "desktop session ended by %s: returned", task.get_name()
                        )
            except (
                OSError,
                RuntimeError,
                ValueError,
                TimeoutError,
                asyncio.IncompleteReadError,
                WebSocketDisconnect,
            ) as error:
                # Transport errors may include node paths or host details; keep
                # only the exception type in the operator log.
                logging.getLogger(__name__).warning(
                    "desktop connection setup failed: %s", type(error).__name__
                )
            finally:
                if slot is not None and input_connections.get(slot) is websocket:
                    input_connections.pop(slot)
                # Teardown is bounded and terminates the transport first: a
                # cancelled pipe reader must never hold a preview slot past
                # a lost client and eventually exhaust the viewer capacity.
                for task in tasks:
                    task.cancel()
                if process is not None:
                    if process.stdin is not None:
                        process.stdin.close()
                    if process.returncode is None:
                        with contextlib.suppress(ProcessLookupError):
                            process.terminate()
                if tasks:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(
                            asyncio.gather(*tasks, return_exceptions=True), timeout=3
                        )
                if process is not None and process.returncode is None:
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
    # One process owns the bounded, per-target preview admission counter.
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
