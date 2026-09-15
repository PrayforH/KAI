"""Exercise a configured CubeSandbox; creates and deletes one temporary instance."""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk._internal.transport import Transport

from harness.config import Settings
from harness.core.models import Run, RunStatus
from harness.runtime.codex_app_server import CodexAppServerOptions, DaytonaCodexAppServerProcess
from harness.sandbox.cubesandbox import build_cubesandbox_provider


async def smoke(*, remote_cli: bool, codex: bool) -> None:
    settings = Settings()
    provider = build_cubesandbox_provider(settings)
    now = datetime.now(UTC)
    identifier = f"cube-smoke-{uuid4().hex}"
    run = Run(
        run_id=identifier,
        session_id=identifier,
        tenant_id="sandbox-integration",
        status=RunStatus.PROVISIONING,
        idempotency_key=identifier,
        created_at=now,
        updated_at=now,
    )
    started = time.monotonic()
    handle = await provider.provision(run)
    print(json.dumps({"stage": "created", "sandbox_id": handle.sandbox_id}), flush=True)
    try:
        (handle.path / "inputs").mkdir()
        content = "CubeSandbox 中文文件\n".encode()
        (handle.path / "inputs" / "中文.txt").write_bytes(content)
        await provider.prepare(
            handle if remote_cli else handle.model_copy(update={"deferred_tool_execution": True})
        )
        result = await provider.execute(
            handle,
            [
                "bash",
                "-c",
                "mkdir -p outputs; cp inputs/中文.txt outputs/result.txt; "
                "printf 'stdout-ok\\n'; printf 'stderr-ok\\n' >&2; exit 7",
            ],
        )
        assert (result.exit_code, result.stdout, result.stderr) == (7, "stdout-ok\n", "stderr-ok\n")
        await provider.collect(handle)
        assert (handle.path / "outputs" / "result.txt").read_bytes() == content
        print(json.dumps({"stage": "tools_and_artifact_passed"}), flush=True)
        try:
            await provider.execute(handle, ["sleep", "20"], timeout_seconds=0.5)
        except TimeoutError:
            print(json.dumps({"stage": "timeout_passed"}), flush=True)
        else:
            raise AssertionError("Command did not time out")
        result = await provider.execute(handle, ["printf", "after-timeout"])
        assert result.stdout == "after-timeout"
        assert handle.runtime_transport_factory is not None
        if remote_cli:
            options = ClaudeAgentOptions(cwd=handle.path, permission_mode="default")
            transport = cast(Transport, handle.runtime_transport_factory(options))
            async with ClaudeSDKClient(options=options, transport=transport):
                print(json.dumps({"stage": "claude_protocol_initialized"}), flush=True)
        if codex:
            process = cast(
                DaytonaCodexAppServerProcess,
                handle.runtime_transport_factory(
                    CodexAppServerOptions(
                        codex_path=Path("codex"),
                        working_directory=handle.path,
                    )
                ),
            )
            try:
                await process.start()
                print(json.dumps({"stage": "codex_protocol_initialized"}), flush=True)
            finally:
                await process.close()
    finally:
        await provider.destroy(handle)
        print(json.dumps({"stage": "deleted", "sandbox_id": handle.sandbox_id}), flush=True)
    print(json.dumps({"status": "passed", "seconds": round(time.monotonic() - started, 2)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-cli", action="store_true", help="Linux Worker CLI bootstrap")
    parser.add_argument("--codex", action="store_true", help="Also initialize the Codex protocol")
    args = parser.parse_args()
    asyncio.run(smoke(remote_cli=args.remote_cli, codex=args.codex))
