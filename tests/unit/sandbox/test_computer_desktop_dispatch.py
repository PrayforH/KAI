"""Desktop operation dispatch: only pointer actions need coordinates.

Typing and key presses act on the focused window, so a payload without x/y is
valid for them. Requiring coordinates for every non-screenshot action turned
both into an HTTP 503 on the deployed gate.
"""

import asyncio
from typing import Any, cast

import pytest

from harness.computer.desktop_client import RfbClient
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
        asyncio.run(service().operate(cast(Any, object()), "nope", {}))
