"""Native desktop is a typed capability, never an arbitrary CLI/shell tunnel."""

from types import SimpleNamespace

import pytest

from harness.computer import vm_host
from harness.computer.native_desktop import advance_fence, command, uncertain
from harness.sandbox.base import SandboxCommandResult


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "shell", "text": "id"},
        {"action": []},
        {"action": "snapshot", "headed": True},
        {"action": "snapshot", "trace": "/tmp/output"},
        {"action": "snapshot", "max_depth": True},
        {"action": "snapshot", "root": "../../private"},
        {"action": "snapshot", "surface": "alert", "skeleton": True},
        {"action": "snapshot", "app": "name\n--headed"},
        {"action": "click", "ref": "@e1"},
        {"action": "type", "ref": "@sabc:e1", "text": "x" * 2001},
        {"action": "status", "app": "other"},
    ],
)
def test_untrusted_requests_cannot_expand_native_capabilities(payload):
    with pytest.raises(ValueError):
        command(payload)


def test_user_text_is_a_literal_operand_not_an_option_or_shell_command():
    text = "--headed $(touch /tmp/should-not-exist)\n中文"
    argv = command({"action": "type", "ref": "@sabc:e1", "text": text})
    assert argv == ["type", "--timeout-ms=2000", "--", "@sabc:e1", text]


def test_uncertain_native_delivery_never_claims_safe_retry():
    error = uncertain("TIMEOUT", "timed out")["error"]
    assert error["disposition"] == {"delivery": "unknown", "retry": "unsafe"}


@pytest.mark.asyncio
async def test_native_dispatch_uses_the_same_lease_guard_and_skips_rfb(monkeypatch):
    calls = []
    spec = SimpleNamespace(native_desktop=True)
    service = vm_host.VmDesktopService({"remote": spec})
    computer = SimpleNamespace(volume_ref="remote")

    class Gate:
        def __init__(self, target):
            assert target is spec

        async def call(self, action, payload, **_kwargs):
            calls.append((action, payload))
            return SandboxCommandResult(
                exit_code=0, stdout='{"ok":true,"data":{"tree":{}}}', stderr=""
            )

    checked = []

    async def guard():
        checked.append(True)

    monkeypatch.setattr(vm_host, "GuestGate", Gate)
    result = await service.operate(
        computer, "ui", {"action": "snapshot"}, scope="lease-1", expected_epoch=3, revalidate=guard
    )
    assert result["ok"] is True and checked == [True]
    assert calls[0][0] == "native_desktop" and calls[0][1]["scope"] == "lease-1"
    assert service._sessions == {}
    assert await service.wait_drained("remote", timeout=0.1)


def test_takeover_fences_delayed_native_calls_and_return_requires_a_new_generation():
    active = advance_fence({}, 3, blocked=False)
    pending = advance_fence(active, 4, blocked=True)
    for epoch in (3, 4):
        with pytest.raises(PermissionError):
            advance_fence(pending, epoch, blocked=False)
    assert advance_fence(pending, 4, blocked=True) == pending
    assert advance_fence(pending, 6, blocked=False) == {"epoch": 6, "blocked": False}


@pytest.mark.asyncio
async def test_unconfigured_native_target_is_refused_before_transport():
    service = vm_host.VmDesktopService({"remote": SimpleNamespace(native_desktop=False)})

    async def guard():
        pytest.fail("an unsupported target must not reach execution")

    with pytest.raises(ValueError, match="not enabled"):
        await service.operate(
            SimpleNamespace(volume_ref="remote"),
            "ui",
            {"action": "status"},
            scope="lease",
            revalidate=guard,
        )
