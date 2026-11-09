"""Native desktop is a typed capability, never an arbitrary CLI/shell tunnel."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from harness.computer import native_desktop, vm_host
from harness.computer.native_desktop import advance_fence, command, uncertain
from harness.sandbox.base import SandboxCommandResult


@pytest.mark.parametrize("wrong", [None, "uid", "service", "pid", "setns"])
def test_vm_mount_join_is_fixed_to_the_desktop_service_and_fails_closed(
    tmp_path,
    monkeypatch,
    wrong,
):
    process = tmp_path / "884"
    (process / "ns").mkdir(parents=True)
    (process / "ns/mnt").write_bytes(b"namespace fixture")
    uid = 0 if wrong == "uid" else 28001
    (process / "status").write_text("Uid:\t" + "\t".join([str(uid)] * 4) + "\n")
    service = "other.service" if wrong == "service" else "kai-desktop.service"
    (process / "cgroup").write_text("0::/system.slice/" + service + "\n")
    monkeypatch.setattr(native_desktop, "PROC", tmp_path)
    lookup = Mock(return_value=b"0\n" if wrong == "pid" else b"884\n")
    monkeypatch.setattr(native_desktop.subprocess, "check_output", lookup)
    libc = SimpleNamespace(setns=Mock(return_value=-1 if wrong == "setns" else 0))
    if wrong:
        with pytest.raises(PermissionError):
            native_desktop.enter_vm_desktop_mounts(28001, libc)
    else:
        native_desktop.enter_vm_desktop_mounts(28001, libc)
    lookup.assert_called_once_with(
        ["/usr/bin/systemctl", "show", "--value", "--property=MainPID", "kai-desktop.service"],
        timeout=2,
    )
    if wrong in (None, "setns"):
        libc.setns.assert_called_once()
        assert libc.setns.call_args.args[1] == 0x00020000
    else:
        libc.setns.assert_not_called()


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


@pytest.mark.parametrize("timeout", [True, -1, 0, 499, 8001, "8000"])
def test_native_wait_budget_cannot_be_unbounded_or_coerced(timeout):
    with pytest.raises(ValueError, match="timeout"):
        command({"action": "snapshot", "timeout_ms": timeout})


def test_partial_observations_can_be_repeated_with_an_explicit_bounded_budget():
    assert "--timeout-ms=8000" in command({"action": "snapshot", "timeout_ms": 8000})
    assert command({"action": "click", "ref": "@sabc:e1", "timeout_ms": 6000}) == [
        "click",
        "--timeout-ms=6000",
        "--",
        "@sabc:e1",
    ]


def test_uncertain_native_delivery_never_claims_safe_retry():
    error = uncertain("TIMEOUT", "timed out")["error"]
    assert error["disposition"] == {"delivery": "unknown", "retry": "unsafe"}


@pytest.mark.asyncio
@pytest.mark.parametrize("desktop_id", [None, "desktop_" + "a" * 24])
async def test_native_dispatch_uses_the_same_lease_guard_and_skips_rfb(monkeypatch, desktop_id):
    calls = []
    spec = SimpleNamespace(native_desktop=True)
    service = vm_host.VmDesktopService({"remote": spec})
    computer = SimpleNamespace(volume_ref="remote", provider="vm-computer")

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
        computer, "ui", {"action": "snapshot"}, scope="lease-1", expected_epoch=3, revalidate=guard,
        desktop_id=desktop_id,
    )
    assert result["ok"] is True and checked == [True]
    assert calls[0][0] == "native_desktop" and calls[0][1]["scope"] == "lease-1"
    assert calls[0][1].get("desktop_id") == desktop_id
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


@pytest.mark.asyncio
async def test_export_dispatch_requires_vm_lease_and_uses_no_display_transport(monkeypatch):
    spec = SimpleNamespace(native_desktop=True)
    service = vm_host.VmDesktopService({"v": spec})
    calls = []

    class Gate:
        def __init__(self, target):
            assert target is spec

        async def call(self, action, payload, **kwargs):
            calls.append((action, payload))
            return SandboxCommandResult(
                exit_code=0, stdout='{"ok":true,"path":"outputs/desktop/f"}', stderr=""
            )

    monkeypatch.setattr(vm_host, "GuestGate", Gate)
    checked = []

    async def guard():
        checked.append(True)

    computer = SimpleNamespace(volume_ref="v", provider="vm-computer")
    result = await service.operate(
        computer, "export", {"path": "f"}, scope="lease-1", expected_epoch=4, revalidate=guard
    )
    assert result["path"] == "outputs/desktop/f"
    assert calls[0][0] == "desktop_export"
    assert calls[0][1]["scope"] == "lease-1" and calls[0][1]["desktop_epoch"] == 4
    assert checked == [True] and service._sessions == {}
    computer.provider = "remote-computer"
    with pytest.raises(ValueError, match="not enabled"):
        await service.operate(
            computer, "export", {"path": "f"}, scope="lease-1", expected_epoch=4, revalidate=guard
        )
    assert len(calls) == 1
