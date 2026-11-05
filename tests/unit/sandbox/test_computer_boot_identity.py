"""A previous kernel's numeric process identifiers never grant execution access."""

from types import SimpleNamespace
from typing import Any, NoReturn

import pytest

from harness.computer import supervisor

CURRENT = "11111111-1111-4111-8111-111111111111"
PREVIOUS = "22222222-2222-4222-8222-222222222222"


class IdentityProbe(supervisor.Guard):
    def inspect(self, state: dict[str, Any]) -> int | None:
        return self._namespace_fd(state)


def namespace(**extra: object) -> dict[str, Any]:
    return {"namespace": {"pid": 42, "start": "123", "pid_inode": 900, **extra}}


def guard() -> IdentityProbe:
    # Only the identity reader is exercised; no Linux setup or process runs.
    return object.__new__(IdentityProbe)


def fake_namespace(
    monkeypatch: pytest.MonkeyPatch, *, start: str | None = "123", inode: int = 900
) -> list[int]:
    def process_start(_pid: object) -> str | None:
        return start

    def opened(*_args: object) -> int:
        return 7

    def status(_fd: int) -> SimpleNamespace:
        return SimpleNamespace(st_ino=inode)

    closed: list[int] = []
    monkeypatch.setattr(supervisor, "_kernel_boot_id", lambda: CURRENT)
    monkeypatch.setattr(supervisor, "_process_start", process_start)
    monkeypatch.setattr(supervisor.os, "open", opened)
    monkeypatch.setattr(supervisor.os, "fstat", status)
    monkeypatch.setattr(supervisor.os, "close", closed.append)
    return closed


def test_previous_kernel_is_rejected_before_looking_at_reused_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(supervisor, "_kernel_boot_id", lambda: CURRENT)

    def unexpected(*_args: object) -> NoReturn:
        pytest.fail("old kernel metadata must not inspect or enter the current PID")

    monkeypatch.setattr(supervisor, "_process_start", unexpected)
    monkeypatch.setattr(supervisor.os, "open", unexpected)
    assert guard().inspect(namespace(boot_id=PREVIOUS)) is None


@pytest.mark.parametrize("value", ["", "not-a-boot-id", 42, True, []])
def test_malformed_kernel_identity_stays_fenced(value: object) -> None:
    with pytest.raises(RuntimeError, match="kernel identity"):
        guard().inspect(namespace(boot_id=value))


def test_matching_kernel_still_requires_process_lifetime_and_namespace_inode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_namespace(monkeypatch, start="different-lifetime")
    assert guard().inspect(namespace(boot_id=CURRENT)) is None
    closed = fake_namespace(monkeypatch, inode=901)
    assert guard().inspect(namespace(boot_id=CURRENT)) is None
    assert closed == [7]


def test_legacy_live_metadata_cannot_enter_a_matching_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = fake_namespace(monkeypatch)
    with pytest.raises(RuntimeError, match="physical recovery"):
        guard().inspect(namespace())
    assert closed == [7]


def test_legacy_absent_namespace_does_not_block_a_fresh_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_namespace(monkeypatch, start=None)
    assert guard().inspect(namespace()) is None
