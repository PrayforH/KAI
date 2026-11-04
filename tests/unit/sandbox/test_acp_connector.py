"""The ACP capability cannot choose an identity, command, or permission bypass."""

import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from harness.computer.acp_bridge import arguments, authorized_directory, current_lease
from harness.computer.acp_tools import AcpSource, computer_acp_overlay
from harness.sandbox.base import SandboxCommandResult, SandboxHandle


@pytest.mark.parametrize("extra", [
    {"agent": "shell"}, {"prompt": ""}, {"prompt": "x" * 6001},
    {"cwd": "../private"}, {"env": {"HOME": "/root"}}, {"user": "root"},
    {"binary": "/usr/bin/sh"}, {"mode": "bypassPermissions"},
    {"timeout_seconds": True}, {"timeout_seconds": 181},
])
def test_model_cannot_expand_the_fixed_readonly_entrypoint(extra: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        arguments({"agent": "claude", "prompt": "read project", **extra})


def test_project_scope_resolves_symlinks_and_does_not_accept_sibling_prefix(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    other = tmp_path / "repo-private"
    other.mkdir()
    (project / "escape").symlink_to(other, target_is_directory=True)
    assert authorized_directory(None, [str(project)]) == str(project)
    for path in [other, project / "escape"]:
        with pytest.raises(PermissionError):
            authorized_directory(str(path), [str(project)])


def test_cancelled_expired_or_new_attempt_revokes_acp() -> None:
    request = {"epoch": 2, "lease_id": "lease-1"}
    state = {**request, "active": True, "expires_at": time.time() + 60}
    assert current_lease(state, request)
    for change in [{"active": False}, {"epoch": 3}, {"lease_id": "new"},
                   {"expires_at": time.time() - 1}]:
        assert not current_lease({**state, **change}, request)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,exit_code,failed", [
    ({"ok": True, "text": "actual agent response", "read_only": True}, 0, False),
    ({"ok": False, "code": "ACP_LOGIN_REQUIRED"}, 0, True),
    ({"ok": True, "text": "not a successful transport"}, 1, True),
])
async def test_tool_preserves_real_agent_outcome_instead_of_claiming_success(
    reply: dict[str, Any], exit_code: int, failed: bool,
) -> None:
    call = AsyncMock(return_value=SandboxCommandResult(
        exit_code=exit_code, stdout=json.dumps(reply), stderr=""))
    handle = SandboxHandle(sandbox_id="fixture", path=Path("/fixture"))
    overlay = computer_acp_overlay(cast(AcpSource, SimpleNamespace(acp_action=call)), handle)
    result = await overlay.tools[0].handler({"agent": "codex", "prompt": "read project"})
    assert result["isError"] is failed
    assert json.loads(result["content"][0]["text"]) == reply
    assert call.await_args is not None
    assert call.await_args.args[1]["agent"] == "codex"
    assert overlay.tools[0].schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_malformed_or_lost_transport_does_not_become_a_final_answer() -> None:
    call = AsyncMock(return_value=SandboxCommandResult(exit_code=0, stdout='{"text":"done"}'))
    handle = SandboxHandle(sandbox_id="fixture", path=Path("/fixture"))
    tool = computer_acp_overlay(cast(AcpSource, SimpleNamespace(acp_action=call)), handle).tools[0]
    assert (await tool.handler({"agent": "claude", "prompt": "read"}))["isError"]
    call.side_effect = RuntimeError("transport gone")
    assert (await tool.handler({"agent": "claude", "prompt": "read"}))["isError"]


@pytest.mark.asyncio
async def test_internal_acp_session_handle_is_not_conversation_content() -> None:
    reply = {"ok": True, "session_id": "private-transport-session", "text": "actual answer"}
    call = AsyncMock(return_value=SandboxCommandResult(exit_code=0, stdout=json.dumps(reply)))
    handle = SandboxHandle(sandbox_id="fixture", path=Path("/fixture"))
    tool = computer_acp_overlay(cast(AcpSource, SimpleNamespace(acp_action=call)), handle).tools[0]
    result = await tool.handler({"agent": "claude", "prompt": "read"})
    assert "private-transport-session" not in json.dumps(result)
    assert json.loads(result["content"][0]["text"])["text"] == "actual answer"


def test_demo_copy_declares_writes_and_never_readonly() -> None:
    """The demo copy must advertise writes and must not claim read-only."""
    from harness.computer import acp_tools

    source = inspect.getsource(acp_tools)
    assert "DEMO 可写" in source
    assert "allow_once" in source
    assert "只读" not in source


def test_client_keeps_writes_and_answers_permissions_once() -> None:
    """The shipped client must not force read-only modes and must answer
    permission requests with a single-shot allow (never allow_always)."""
    client = (Path(__file__).parents[2].parent / "src" / "harness" / "computer" / "acp_client.mjs").read_text()
    assert "session/set_mode" not in client
    assert '"outcome": "cancelled"' not in client
    assert "allow_once" in client
    # allow_always may only appear as a fallback pick, never as the chosen outcome.
    chosen = client.split("pick =")[1].split("return")[0]
    assert 'outcome: "allow_always"' not in chosen
