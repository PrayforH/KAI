"""Model-facing GUI tool flow: acquire, act, and friendly refusals."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from harness.computer.desktop import DesktopControlService
from harness.computer.gui_tools import computer_gui_overlay
from harness.computer.registry import ComputerRegistry
from harness.core.models import Run, RunStatus, Session
from harness.sandbox.base import SandboxHandle
from tests.integration.storage.conftest import DatabaseFixture

NOW = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
OWNER = "computer-run-" + "a" * 32


class StubProvider:
    """Stands in for ComputerSandboxProvider with a scripted desktop action."""

    def __init__(self, results: list[dict[str, Any]], *, root: Path) -> None:
        self.handle = SandboxHandle(
            sandbox_id=OWNER,
            path=root,
            provider="vm-computer",
            remote_workspace="/home/kai/workspace",
            deferred_tool_execution=True,
            persistent_workspace=True,
        )
        self.session = Session(
            session_id="session-1",
            tenant_id="tenant-a",
            user_id="user-a",
            agent_name="assistant",
            agent_version="1",
            created_at=NOW,
            environment="production",
            runtime_type="claude-agent-sdk",
        )
        self.results = results
        self.calls: list[tuple[str, dict[str, Any], int]] = []

    def owns(self, handle: SandboxHandle) -> bool:
        return handle.sandbox_id == OWNER

    async def desktop_action(
        self,
        handle: SandboxHandle,
        operation: str,
        payload: dict[str, Any],
        *,
        expected_epoch: int,
    ) -> dict[str, Any]:
        self.calls.append((operation, payload, expected_epoch))
        return self.results.pop(0)


async def prepared(database: DatabaseFixture) -> DesktopControlService:
    _, sessions = database
    await ComputerRegistry(sessions).ensure(
        "tenant-a", "user-a", provider="vm-computer", image="sha256:" + "a" * 64
    )
    return DesktopControlService(sessions)


def run() -> Run:
    return Run(
        run_id="run-1",
        session_id="session-1",
        tenant_id="tenant-a",
        status=RunStatus.RUNNING,
        idempotency_key="k",
        created_at=NOW,
        updated_at=NOW,
    )


def text_of(result: dict[str, Any]) -> str:
    return str(result["content"][0]["text"])


@pytest.mark.asyncio
async def test_run_teardown_releases_desktop_ownership(
    database: DatabaseFixture, tmp_path: Any
) -> None:
    """Run teardown returns the desktop to observation even without a release."""

    desktop = await prepared(database)
    provider = StubProvider([], root=tmp_path)
    handle = provider.handle
    from harness.computer.sandbox import ComputerSandboxProvider

    # Only the release hook is under test; destroy() would rmtree the handle
    # path, so call the hook exactly as destroy's finally block does.
    provider = StubProvider([], root=tmp_path)
    handle = provider.handle
    release_provider = cast(Any, ComputerSandboxProvider.__new__(ComputerSandboxProvider))
    release_provider.session = provider.session
    release_provider.desktop = desktop
    await desktop.acquire_agent("tenant-a", "user-a", owner=handle.sandbox_id)
    state = await desktop.view("tenant-a", "user-a")
    assert state.state.value == "agent"
    await release_provider._release_desktop(handle)
    state = await desktop.view("tenant-a", "user-a")
    assert state.state.value == "observe"


@pytest.mark.asyncio
async def test_screenshot_acquires_ownership_and_returns_image(
    database: DatabaseFixture, tmp_path: Any
) -> None:
    desktop = await prepared(database)
    provider = StubProvider(
        [{"width": 1280, "height": 800, "png_base64": "aGVsbG8="}], root=tmp_path
    )
    overlay = computer_gui_overlay(desktop, provider, provider.handle)
    tools = {tool.name: tool.handler for tool in overlay.tools}
    await tools["computer_screenshot"]({})
    assert provider.calls == [("screenshot", {}, 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,explicit", [("vm-computer", None), ("vm-computer", 500), ("remote-computer", None)]
)
async def test_native_vm_wait_default_preserves_explicit_budget_and_legacy_pc_protocol(
    database: DatabaseFixture,
    tmp_path: Path,
    target: str,
    explicit: int | None,
) -> None:
    desktop = await prepared(database)
    provider = StubProvider([{"ok": True}], root=tmp_path)
    provider.handle = provider.handle.model_copy(update={"provider": target})
    overlay = computer_gui_overlay(desktop, provider, provider.handle, native_desktop=True)
    tool = next(tool for tool in overlay.tools if tool.name == "computer_ui")
    assert ("timeout_ms" in tool.schema["properties"]) == (target == "vm-computer")
    arguments: dict[str, Any] = {"action": "snapshot", "app": "chromium"}
    if explicit is not None:
        arguments["timeout_ms"] = explicit
    original = dict(arguments)
    await tool.handler(arguments)
    forwarded = provider.calls[0][1]
    if target == "vm-computer":
        assert forwarded["timeout_ms"] == (8000 if explicit is None else explicit)
    else:
        assert forwarded == original
    assert arguments == original


@pytest.mark.asyncio
async def test_screenshot_forwards_region_and_scale(
    database: DatabaseFixture, tmp_path: Any
) -> None:
    """Dropping the arguments here turns every cropped view into a full one."""

    desktop = await prepared(database)
    provider = StubProvider(
        [{"width": 400, "height": 300, "png_base64": "aGVsbG8="}], root=tmp_path
    )
    overlay = computer_gui_overlay(desktop, provider, provider.handle)
    tools = {tool.name: tool.handler for tool in overlay.tools}
    payload = {"region": {"x": 0, "y": 0, "width": 400, "height": 300}, "scale": 0.5}
    result = await tools["computer_screenshot"](payload)
    assert provider.calls == [("screenshot", payload, 1)]
    text = result["content"][1]["text"]
    assert "400x300" in text and "tokens" in text
    image = result["content"][0]
    assert image == {"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"}
    state = await desktop.view("tenant-a", "user-a")
    assert state.state.value == "agent" and state.owner == OWNER


@pytest.mark.asyncio
async def test_occupied_desktop_refuses_with_a_clear_message(
    database: DatabaseFixture, tmp_path: Any
) -> None:
    desktop = await prepared(database)
    provider = StubProvider([], root=tmp_path)
    overlay = computer_gui_overlay(desktop, provider, provider.handle)
    tools = {tool.name: tool.handler for tool in overlay.tools}
    # The user took over: no in-flight ownership by this run any more.
    await desktop.request_human("tenant-a", "user-a")
    result = await tools["computer_click"]({"x": 3, "y": 4})
    assert "控制" in text_of(result) or "等待" in text_of(result)
    assert provider.calls == []


@pytest.mark.asyncio
async def test_actions_renew_ttl_and_bind_the_acquired_epoch(
    database: DatabaseFixture, tmp_path: Any
) -> None:
    desktop = await prepared(database)
    provider = StubProvider([{"clicked": True}, {"typed": 2}], root=tmp_path)
    overlay = computer_gui_overlay(desktop, provider, provider.handle)
    tools = {tool.name: tool.handler for tool in overlay.tools}
    first = await tools["computer_click"]({"x": 3, "y": 4})
    assert first == {"clicked": True}
    second = await tools["computer_type"]({"text": "你好"})
    assert second == {"typed": 2}
    # Both actions carried the same acquired epoch; ownership was renewed.
    assert [call[2] for call in provider.calls] == [1, 1]
    state = await desktop.view("tenant-a", "user-a")
    assert state.revision >= 3


@pytest.mark.asyncio
async def test_desktop_export_uses_the_same_ownership_guard_and_structured_file_reference(
    database: DatabaseFixture,
    tmp_path: Path,
):
    desktop = await prepared(database)
    provider = StubProvider(
        [{"ok": True, "path": "outputs/desktop/report.txt", "size_bytes": 4}], root=tmp_path
    )
    overlay = computer_gui_overlay(desktop, provider, provider.handle, native_desktop=True)
    tool = next(tool for tool in overlay.tools if tool.name == "computer_export_file")
    result = await tool.handler({"path": "report.txt"})
    assert "outputs/desktop/report.txt" in text_of(result)
    assert provider.calls == [("export", {"path": "report.txt"}, 1)]
    await desktop.request_human("tenant-a", "user-a")
    refused = await tool.handler({"path": "report.txt"})
    assert "控制" in text_of(refused)
    assert len(provider.calls) == 1
