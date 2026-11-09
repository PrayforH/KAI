"""Desktop operation dispatch: only pointer actions need coordinates.

Typing and key presses act on the focused window, so a payload without x/y is
valid for them. Requiring coordinates for every non-screenshot action turned
both into an HTTP 503 on the deployed gate.
"""

import asyncio
from typing import Any, cast

import pytest

from harness.computer.desktop_client import RfbClient, TypingAbortError, press_keys
from harness.computer.models import ComputerLeaseLostError
from harness.computer.vm_host import VmDesktopService


class FakeClient:
    """Duck-typed RFB client: records the events a real one would send."""

    width = 1024
    height = 768

    def __init__(self) -> None:
        self.keys: list[tuple[int, bool]] = []
        self.pointers: list[tuple[int, int, int]] = []

    async def key(self, keysym: int, *, down: bool) -> None:
        self.keys.append((keysym, down))

    async def pointer(self, x: int, y: int, mask: int) -> None:
        self.pointers.append((x, y, mask))


def service() -> VmDesktopService:
    from harness.computer.vm_host import VmComputerSpec

    spec = VmComputerSpec.model_validate(
        {
            "volume_ref": "v",
            "container_name": "c",
            "ssh_host": "h",
            "runner_image": "sha256:" + "a" * 64,
            "guest_base_digest": "sha256:" + "b" * 64,
            "root_disk_source": "/x",
            "identity_dir": "/identities/computer_" + "c" * 32 + "-" + "d" * 32,
            "kvm_device": "/k",
            "network": "n",
        }
    )
    return VmDesktopService({"v": spec})


@pytest.mark.asyncio
async def test_type_needs_no_coordinates():
    client = FakeClient()
    result = await service()._dispatch(cast(RfbClient, client), "type", {"text": "ab"})
    assert result == {"typed": 2}
    assert [sym for sym, _ in client.keys] == [ord("a"), ord("a"), ord("b"), ord("b")]


@pytest.mark.asyncio
async def test_key_press_needs_no_coordinates():
    client = FakeClient()
    result = await service()._dispatch(
        cast(RfbClient, client), "key", {"keys": ["ctrl", "c"]}
    )
    assert result == {"pressed": 2}
    assert client.keys == [(0xFFE3, True), (ord("c"), True), (ord("c"), False), (0xFFE3, False)]


@pytest.mark.asyncio
async def test_pointer_action_still_requires_coordinates():
    client = FakeClient()
    with pytest.raises(ValueError, match="coordinates"):
        await service()._dispatch(cast(RfbClient, client), "click", {})
    result = await service()._dispatch(cast(RfbClient, client), "click", {"x": 5, "y": 6})
    assert result == {"clicked": True}
    assert client.pointers == [(5, 6, 1), (5, 6, 0)]


@pytest.mark.asyncio
async def test_unknown_operation_is_rejected_before_touching_the_desktop():
    client = FakeClient()
    with pytest.raises(ValueError):
        await service()._dispatch(cast(RfbClient, client), "nope", {})


def test_operate_rejects_unsupported_operation():
    with pytest.raises(ValueError, match="not supported"):
        asyncio.run(service().operate(cast(Any, object()), "nope", {}, revalidate=noop))


@pytest.mark.asyncio
async def test_screenshot_accepts_region_and_scale():
    service_ = service()
    calls: list[dict[str, Any]] = []

    class ShotClient(FakeClient):
        def resolve_region(self, region: Any) -> tuple[int, int, int, int]:
            return (10, 20, 40, 30) if region else (0, 0, 1024, 768)

        async def screenshot(self, *, region: Any = None, scale: float = 1.0):
            calls.append({"region": region, "scale": scale})
            return 20, 15, b"\x89PNG"

    result = await service_._dispatch(
        cast(RfbClient, ShotClient()),
        "screenshot",
        {"region": {"x": 10, "y": 20, "width": 40, "height": 30}, "scale": 0.5},
    )
    assert calls == [{"region": (10, 20, 40, 30), "scale": 0.5}]
    assert result["width"] == 20 and result["height"] == 15
    assert result["desktop_width"] == 1024 and result["desktop_height"] == 768
    assert result["region"] == {"x": 10, "y": 20, "width": 40, "height": 30}


@pytest.mark.asyncio
async def test_screenshot_rejects_a_malformed_region():
    service_ = service()
    for bad in ({"x": 1, "y": 2}, {"x": "1", "y": 2, "width": 3, "height": 4}, "nope"):
        with pytest.raises(ValueError, match="region"):
            await service_._dispatch(cast(RfbClient, FakeClient()), "screenshot", {"region": bad})


async def noop() -> None:
    return None


@pytest.mark.asyncio
async def test_dequeued_action_revalidates_before_it_executes():
    service_ = service()
    service_._sessions[("v", None)] = cast(RfbClient, FakeClient())  # already connected
    checked: list[bool] = []

    async def guard() -> None:
        checked.append(True)

    result = await service_.operate(
        cast(Any, type("C", (), {"volume_ref": "v"})()),
        "type",
        {"text": "ok"},
        revalidate=guard,
    )
    assert result == {"typed": 2} and checked == [True]


@pytest.mark.asyncio
async def test_a_queued_action_is_refused_when_its_lease_was_fenced():
    service_ = service()
    client = FakeClient()
    service_._sessions[("v", None)] = cast(RfbClient, client)  # already connected

    async def fenced() -> None:
        raise ComputerLeaseLostError("desktop control epoch is stale")

    with pytest.raises(ComputerLeaseLostError):
        await service_.operate(
            cast(Any, type("C", (), {"volume_ref": "v"})()),
            "type",
            {"text": "no"},
            revalidate=fenced,
        )
    assert client.keys == []  # a fenced action never reaches the desktop


@pytest.mark.asyncio
async def test_takeover_stops_inflight_typing_between_keystrokes():
    service_ = service()

    class SlowClient(FakeClient):
        async def key(self, keysym: int, *, down: bool) -> None:
            await asyncio.sleep(0.004)
            await super().key(keysym, down=down)

    slow = SlowClient()
    stop = asyncio.Event()
    task = asyncio.create_task(
        service_._dispatch(cast(RfbClient, slow), "type", {"text": "a" * 200}, stop=stop)
    )
    await asyncio.sleep(0.05)
    typed_before = len(slow.keys) // 2
    assert typed_before > 0
    stop.set()
    with pytest.raises(TypingAbortError, match="characters"):
        await task
    typed_after = len(slow.keys) // 2
    assert typed_after < 200
    # The stop must land within a keystroke, not after the whole text.
    assert typed_after - typed_before <= 2
    assert service_._busy_event("v").is_set()


@pytest.mark.asyncio
async def test_aborted_key_combination_releases_its_modifiers():
    service_ = service()
    client = FakeClient()
    stop = asyncio.Event()
    stop.set()

    with pytest.raises(TypingAbortError):
        await service_._dispatch(
            cast(RfbClient, client), "key", {"keys": ["ctrl", "c"]}, stop=stop
        )
    # Nothing was sent: the abort fired before the first key went down.
    assert client.keys == []


@pytest.mark.asyncio
async def test_modifier_release_survives_an_abort_mid_combination():
    class Recording(FakeClient):
        downs = 0

        async def key(self, keysym: int, *, down: bool) -> None:
            await super().key(keysym, down=down)
            if down:
                self.downs += 1

    client = Recording()
    abort_calls = {"count": 0}

    def abort() -> bool:
        abort_calls["count"] += 1
        return abort_calls["count"] > 1  # allow ctrl down, then abort

    with pytest.raises(TypingAbortError):
        await press_keys(cast(RfbClient, client), ["ctrl", "alt", "c"], abort=abort)
    # ctrl was pressed and released again; alt and c never went down.
    assert client.downs == 1
    assert client.keys == [(0xFFE3, True), (0xFFE3, False)]


def test_drain_wait_is_immediate_when_the_screen_is_idle():
    assert asyncio.run(service().wait_drained("v", timeout=0.1)) is True


def test_drain_and_stop_events_survive_repeated_calls():
    """The takeover watcher polls every tick; the events must be stable."""

    service_ = service()
    assert asyncio.run(service_.wait_drained("v", timeout=0.1)) is True
    assert asyncio.run(service_.wait_drained("v", timeout=0.1)) is True
    service_.cancel("v")
    service_.cancel("v")
    assert service_._stop_event("v").is_set()
    assert asyncio.run(service_.wait_drained("v", timeout=0.1)) is True

@pytest.mark.asyncio
async def test_bot_pixels_use_the_slot_connection_instead_of_the_machine_screen():
    screen = service()
    shared, bot = FakeClient(), FakeClient()
    slot = "desktop_" + "a" * 24
    screen._sessions[("v", None)] = cast(RfbClient, shared)
    screen._sessions[("v", slot)] = cast(RfbClient, bot)

    async def guard() -> None:
        pass

    computer = cast(Any, type("C", (), {"volume_ref": "v", "provider": "vm-computer"})())
    await screen.operate(
        computer, "click", {"x": 7, "y": 8}, revalidate=guard, desktop_id=slot
    )
    assert bot.pointers == [(7, 8, 1), (7, 8, 0)]
    assert shared.pointers == []
