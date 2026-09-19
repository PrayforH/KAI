"""How the DeepAgents backend maps model paths onto the Sandbox workspace.

The backend is the only component in the DeepAgents path that *rewrites* a path
before the Sandbox sees it, and the platform's `Bash` tool hands the model the
Sandbox's real absolute path for free (``pwd``). A model that asks where it is
will therefore spell every later path absolutely, and that spelling has to reach
the same file as the workspace-relative one: otherwise the tool gate authorizes
``outputs/report.md``, the bytes land in
``<root>/home/user/harness/<run_id>/outputs/report.md``, and the Worker publishes
nothing while the Run reports success.

These tests pin both halves: the pure rewrite, and the backend wiring that keeps
the rewrite free of an extra Sandbox round trip when the provider declares its
remote workspace.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

pytest.importorskip("deepagents", reason="the DeepAgents kernel is an optional extra")

from harness.runtime.deepagents_backend import (  # noqa: E402
    HarnessSandboxBackend,
    workspace_relative,
)
from harness.sandbox.base import SandboxCommandResult  # noqa: E402

REMOTE_ROOT = "/home/user/harness/run-1"


class _Recorder:
    """A Sandbox command executor that answers `pwd` and records every argv."""

    def __init__(self, *, root: str = REMOTE_ROOT, pwd_exit_code: int = 0) -> None:
        self.commands: list[tuple[str, ...]] = []
        self._root = root
        self._pwd_exit_code = pwd_exit_code

    async def __call__(
        self,
        argv: Any,
        environment: Any,
        timeout_seconds: float,
    ) -> SandboxCommandResult:
        command = tuple(argv)
        self.commands.append(command)
        if command == ("pwd",):
            return SandboxCommandResult(exit_code=self._pwd_exit_code, stdout=f"{self._root}\n")
        return SandboxCommandResult(exit_code=0, stdout="{}")

    @property
    def pwd_calls(self) -> int:
        return sum(1 for command in self.commands if command == ("pwd",))

    def asked_for(self, path: str) -> bool:
        """Whether any command carried this exact path.

        DeepAgents base64-encodes every path into the remote command, and this
        backend does the same for its own read/write scripts, so one check
        covers both transports.
        """

        encoded = base64.b64encode(path.encode("utf-8")).decode("ascii")
        return any(encoded in part for command in self.commands for part in command)

    def operations(self) -> list[str]:
        """The operation name of every platform read/write script invoked."""

        return [
            command[3]
            for command in self.commands
            if command[0] == "python3" and len(command) > 4
        ]


# -- the pure rewrite --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Already workspace-relative: passed through untouched.
        ("outputs/report.md", "outputs/report.md"),
        ("./outputs/report.md", "outputs/report.md"),
        ("notes.md", "notes.md"),
        # The Sandbox's own absolute path: the root is stripped, not re-rooted.
        (f"{REMOTE_ROOT}/outputs/report.md", "outputs/report.md"),
        (REMOTE_ROOT, "."),
        (f"{REMOTE_ROOT}/", "."),
        # Virtual paths, where `/` is the workspace. The FilesystemMiddleware
        # roots its own paths this way.
        ("/outputs/report.md", "outputs/report.md"),
        ("/", "."),
        ("", "."),
        (None, "."),
        # `/workspace` is the platform's alias for the remote workspace, read the
        # same way by the Bash safety review and `RunFileCapabilities`.
        ("/workspace/notes.md", "notes.md"),
        # A directory that merely shares the name is unaffected when relative.
        ("workspace/notes.md", "workspace/notes.md"),
    ],
)
def test_a_path_inside_the_workspace_resolves_to_one_place(
    value: str | None, expected: str
) -> None:
    assert workspace_relative(value, root=REMOTE_ROOT) == expected


def test_a_root_that_is_the_workspace_alias_is_stripped_once() -> None:
    """A provider whose remote root is `/workspace` must not double-strip."""

    assert workspace_relative("/workspace/notes.md", root="/workspace") == "notes.md"


def test_an_absolute_path_without_a_known_root_stays_a_virtual_path() -> None:
    """Containment is preserved when the Sandbox root is unknown.

    A virtual path cannot escape the workspace, because its leading `/` is
    dropped rather than resolved.
    """

    assert workspace_relative("/etc/passwd") == "etc/passwd"
    assert workspace_relative("/etc/passwd", root=REMOTE_ROOT) == "etc/passwd"


@pytest.mark.parametrize("value", ["../escape.md", "outputs/../../escape.md", "/../escape.md"])
def test_traversal_is_rejected_rather_than_resolved(value: str) -> None:
    with pytest.raises(ValueError, match="traverse"):
        workspace_relative(value, root=REMOTE_ROOT)


def test_control_characters_and_home_paths_are_rejected() -> None:
    with pytest.raises(ValueError, match="control characters"):
        workspace_relative("outputs/re\nport.md", root=REMOTE_ROOT)
    with pytest.raises(ValueError, match="home-relative"):
        workspace_relative("~/notes.md", root=REMOTE_ROOT)


# -- the backend wiring ------------------------------------------------------


def _backend(executor: _Recorder, *, remote_workspace: str | None) -> HarnessSandboxBackend:
    return HarnessSandboxBackend(
        executor,  # type: ignore[arg-type]
        sandbox_id="run-1",
        remote_workspace=remote_workspace,
    )


@pytest.mark.asyncio
async def test_a_declared_remote_workspace_costs_no_pwd_round_trip() -> None:
    """The declared root is the same value the tool gate resolves against."""

    executor = _Recorder()
    backend = _backend(executor, remote_workspace=REMOTE_ROOT)

    await backend.aread(f"{REMOTE_ROOT}/outputs/report.md")

    assert executor.asked_for("outputs/report.md")
    assert not executor.asked_for(f"{REMOTE_ROOT}/outputs/report.md")
    assert executor.pwd_calls == 0


@pytest.mark.asyncio
async def test_the_absolute_path_a_model_learned_from_pwd_is_honoured() -> None:
    """The regression: `pwd` output must not be re-rooted into a nested copy."""

    executor = _Recorder()
    backend = _backend(executor, remote_workspace=REMOTE_ROOT)

    await backend.awrite(f"{REMOTE_ROOT}/outputs/report.md", "hello")

    assert executor.asked_for("outputs/report.md")
    assert not executor.asked_for(f"{REMOTE_ROOT}/outputs/report.md")
    # The operation name is what the deferred Sandbox provider classifies, so a
    # write must stay a write or the workspace is never synchronized back.
    assert "write" in executor.operations()


@pytest.mark.asyncio
async def test_the_workspace_root_is_discovered_once_when_not_declared() -> None:
    """A provider that declares no remote workspace falls back to `pwd`, cached."""

    executor = _Recorder()
    backend = _backend(executor, remote_workspace=None)

    await backend.aread(f"{REMOTE_ROOT}/outputs/a.md")
    await backend.aread(f"{REMOTE_ROOT}/outputs/b.md")

    assert executor.asked_for("outputs/a.md")
    assert executor.asked_for("outputs/b.md")
    assert executor.pwd_calls == 1


@pytest.mark.asyncio
async def test_a_relative_path_never_asks_the_sandbox_where_it_is() -> None:
    executor = _Recorder()
    backend = _backend(executor, remote_workspace=None)

    await backend.aread("outputs/report.md")

    assert executor.asked_for("outputs/report.md")
    assert executor.pwd_calls == 0


@pytest.mark.asyncio
async def test_an_undeterminable_workspace_root_fails_loudly() -> None:
    executor = _Recorder(root="", pwd_exit_code=1)
    backend = _backend(executor, remote_workspace=None)

    with pytest.raises(RuntimeError, match="workspace root"):
        await backend.aread(f"{REMOTE_ROOT}/outputs/report.md")


@pytest.mark.asyncio
async def test_glob_searches_from_the_real_root_and_reports_workspace_paths() -> None:
    """`aglob` absolutizes its search root, so it must be given the real one."""

    executor = _Recorder()
    backend = _backend(executor, remote_workspace=REMOTE_ROOT)

    await backend.aglob("*.md", f"{REMOTE_ROOT}/outputs")

    # A relative search root would turn a glob of the workspace into a walk of
    # the whole Sandbox filesystem.
    assert executor.asked_for(f"{REMOTE_ROOT}/outputs")
    assert executor.pwd_calls == 0
