import asyncio
import base64
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from harness.core.manifest import PythonToolSnapshot
from harness.runtime.sandbox_tools import (
    canonical_tool_name,
    create_bundle_python_tool,
    create_sandbox_tool,
    proxy_tool_name,
)
from harness.sandbox.base import SandboxCommandResult


def test_proxy_tool_names_round_trip_to_builtin_names() -> None:
    assert proxy_tool_name("Read") == "mcp__harness-sandbox__read"
    assert canonical_tool_name("mcp__harness-sandbox__write") == "Write"
    assert canonical_tool_name("mcp__tavily__search") == "mcp__tavily__search"


@pytest.mark.asyncio
async def test_bash_proxy_delegates_to_isolated_executor_with_bounded_timeout() -> None:
    calls: list[tuple[Sequence[str], Mapping[str, str] | None, float]] = []

    async def execute(
        argv: Sequence[str],
        environment: Mapping[str, str] | None,
        timeout_seconds: float,
    ) -> SandboxCommandResult:
        calls.append((argv, environment, timeout_seconds))
        return SandboxCommandResult(exit_code=0, stdout="ok")

    tool = create_sandbox_tool(
        builtin="Bash",
        description="test",
        schema={},
        executor=execute,
    )

    result = await tool.handler({"command": "pwd", "timeout": 500_000})

    assert calls == [(("bash", "-lc", "pwd"), None, 120.0)]
    assert result["content"] == [{"type": "text", "text": "ok"}]
    assert "isError" not in result


@pytest.mark.asyncio
async def test_file_proxy_uses_argument_vector_and_reports_backend_failure() -> None:
    calls: list[Sequence[str]] = []

    async def execute(
        argv: Sequence[str],
        _environment: Mapping[str, str] | None,
        _timeout_seconds: float,
    ) -> SandboxCommandResult:
        calls.append(argv)
        return SandboxCommandResult(exit_code=2, stderr="path escaped workspace")

    tool = create_sandbox_tool(
        builtin="Read",
        description="test",
        schema={},
        executor=execute,
    )

    result = await tool.handler({"file_path": "../secret"})

    assert calls[0][:2] == ("python3", "-c")
    assert calls[0][-2] == "read"
    assert result["isError"] is True
    assert result["content"] == [{"type": "text", "text": "path escaped workspace"}]


@pytest.mark.asyncio
async def test_bundle_python_tool_executes_source_in_workspace_sandbox(
    tmp_path: Path,
) -> None:
    relative = Path(".harness-runtime/bundle-tools/hash/tools/double.py")
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    source = b"def run(arguments):\n    return {'result': arguments['value'] * 2}\n"
    target.write_bytes(source)

    async def execute(
        argv: Sequence[str],
        environment: Mapping[str, str] | None,
        timeout_seconds: float,
    ) -> SandboxCommandResult:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=tmp_path,
            env=dict(environment) if environment is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        return SandboxCommandResult(
            exit_code=process.returncode or 0,
            stdout=stdout.decode(),
            stderr=stderr.decode(),
        )

    tool = create_bundle_python_tool(
        snapshot=PythonToolSnapshot(
            reference="bundle:tools/double.py",
            path="tools/double.py",
            name="double_value",
            description="Double a numeric value.",
            inputSchema={"type": "object"},
            contentBase64=base64.b64encode(source).decode(),
            sha256=hashlib.sha256(source).hexdigest(),
            sizeBytes=len(source),
        ),
        materialized_path=relative,
        executor=execute,
    )

    result = await tool.handler({"value": 4})

    assert result["content"] == [{"type": "text", "text": '{"result": 8}\n'}]


@pytest.mark.asyncio
async def test_absolute_remote_file_paths_stay_inside_the_workspace(tmp_path: Path) -> None:
    import sys

    workspace = tmp_path / "run"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "link").symlink_to(outside, target_is_directory=True)

    async def execute(argv, _environment, _timeout):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *argv[1:],
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        return SandboxCommandResult(
            exit_code=process.returncode, stdout=out.decode(), stderr=err.decode()
        )

    tool = create_sandbox_tool(builtin="Write", description="test", schema={}, executor=execute)
    result = await tool.handler(
        {"file_path": str(workspace / "outputs/check.txt"), "content": "ok"}
    )
    assert not result.get("isError")
    assert (workspace / "outputs/check.txt").read_text() == "ok"
    for path in [
        outside / "secret.txt",
        workspace / "link/secret.txt",
        workspace / "../outside/secret.txt",
    ]:
        denied = await tool.handler({"file_path": str(path), "content": "outside"})
        assert denied["isError"] is True
    assert not (outside / "secret.txt").exists()
