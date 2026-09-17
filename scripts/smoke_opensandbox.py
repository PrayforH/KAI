"""Exercise a configured OpenSandbox server; creates and deletes one sandbox.

The OpenSandbox provider runs inside the deferred execution mode: the model
process stays in the Worker and every tool command lands in the sandbox through
execd. This smoke therefore checks the command and file planes that mode uses,
plus the per-Run lifecycle and the orphan check after destruction.

Usage::

    HARNESS_OPENSANDBOX_API_URL=http://host:8090 \
    HARNESS_OPENSANDBOX_API_KEY=... \
    uv run python -m scripts.smoke_opensandbox
"""

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk._internal.transport import Transport

from harness.config import Settings
from harness.core.models import Run, RunStatus
from harness.sandbox.deferred import DeferredToolSandboxProvider
from harness.sandbox.opensandbox import OpenSandboxSandboxProvider, build_opensandbox_provider


async def deferred_smoke() -> None:
    """Run the same cycle through the wrapper production actually uses.

    The deferred provider keeps the Run workspace local and only acquires the
    sandbox when a tool first executes, then synchronizes generated files back.
    """

    settings = Settings()
    backend = build_opensandbox_provider(settings)
    provider = DeferredToolSandboxProvider(
        backend, provider_name=settings.sandbox_provider or "opensandbox"
    )
    now = datetime.now(UTC)
    identifier = f"opensandbox-deferred-{uuid4().hex}"
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
    try:
        assert handle.provider.endswith("-deferred")
        await provider.prepare(handle)
        result = await provider.execute(
            handle, ["bash", "-c", "printf 'deferred-ok\\n'"]
        )
        assert result.stdout == "deferred-ok", result
        await provider.execute(
            handle,
            ["python3", "-c", "open('deferred.txt','w').write('collected')"],
        )
        await provider.collect(handle)
        assert (handle.path / "deferred.txt").read_text() == "collected"
        print(
            json.dumps(
                {
                    "stage": "deferred_passed",
                    "seconds": round(time.monotonic() - started, 2),
                }
            ),
            flush=True,
        )
    finally:
        await provider.destroy(handle)


async def remote_cli_smoke() -> None:
    """Boot the pinned CLI inside the sandbox and speak the SDK protocol to it.

    This is the remote_cli bar: the CLI binary has to run inside the sandbox and
    the PTY transport has to carry the bidirectional SDK handshake. A model call
    is only attempted when the gateway coordinates are provided through
    HARNESS_OPENSANDBOX_SMOKE_BASE_URL/_TOKEN/_MODEL.
    """

    settings = Settings()
    provider = build_opensandbox_provider(settings)
    now = datetime.now(UTC)
    identifier = f"opensandbox-cli-{uuid4().hex}"
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
    try:
        await provider.prepare(handle)
        assert handle.runtime_transport_factory is not None
        print(
            json.dumps(
                {
                    "stage": "cli_installed",
                    "seconds": round(time.monotonic() - started, 2),
                }
            ),
            flush=True,
        )
        environment = dict(os.environ)
        options = ClaudeAgentOptions(
            cwd=handle.path, permission_mode="default", env=environment
        )
        transport = cast(Transport, handle.runtime_transport_factory(options))
        async with ClaudeSDKClient(options=options, transport=transport):
            print(json.dumps({"stage": "claude_protocol_initialized"}), flush=True)
            base_url = os.getenv("HARNESS_OPENSANDBOX_SMOKE_BASE_URL", "")
            token = os.getenv("HARNESS_OPENSANDBOX_SMOKE_TOKEN", "")
            model = os.getenv("HARNESS_OPENSANDBOX_SMOKE_MODEL", "")
            if not all((base_url, token, model)):
                print(
                    json.dumps({"stage": "model_call_skipped", "reason": "no gateway env"}),
                    flush=True,
                )
                return
            options.env = {
                **environment,
                "ANTHROPIC_BASE_URL": base_url,
                "ANTHROPIC_AUTH_TOKEN": token,
            }
            options.model = model
            await client_query_through_transport(options, transport)
    finally:
        await provider.destroy(handle)
        print(json.dumps({"stage": "cli_deleted"}), flush=True)


async def client_query_through_transport(
    options: ClaudeAgentOptions, transport: Transport
) -> None:
    """Run one real turn, requiring the Bash tool to execute inside the sandbox."""

    prompt = "用 Bash 工具执行 echo remote-cli-ok，然后只回复 OK。"
    client = ClaudeSDKClient(options=options, transport=transport)
    async with client:
        await client.query(prompt)
        texts: list[str] = []
        async for message in client.receive_response():
            text = getattr(message, "content", None)
            if isinstance(text, list):
                for block in text:
                    value = getattr(block, "text", None)
                    if isinstance(value, str):
                        texts.append(value)
    reply = " ".join(texts)
    assert reply.strip(), "remote CLI returned no assistant text"
    print(json.dumps({"stage": "model_turn_passed", "reply": reply[:120]}), flush=True)


async def smoke(*, timeout_seconds: int) -> None:
    settings = Settings()
    provider = build_opensandbox_provider(settings)
    assert isinstance(provider, OpenSandboxSandboxProvider)
    now = datetime.now(UTC)
    identifier = f"opensandbox-smoke-{uuid4().hex}"
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
    print(
        json.dumps(
            {
                "stage": "created",
                "sandbox_id": handle.sandbox_id,
                "seconds": round(time.monotonic() - started, 2),
            }
        ),
        flush=True,
    )
    try:
        assert handle.runtime_transport_factory is None, (
            "the OpenSandbox provider is deferred-mode only"
        )
        workspace = handle.remote_workspace
        assert workspace is not None
        (handle.path / "inputs").mkdir()
        content = "OpenSandbox 中文文件\n".encode()
        (handle.path / "inputs" / "中文.txt").write_bytes(content)
        await provider.prepare(
            handle.model_copy(update={"deferred_tool_execution": True})
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
        # execd reports one event per line without its terminator, so the
        # trailing newline of the last line is not part of the recovered text.
        assert (result.exit_code, result.stdout, result.stderr) == (
            7,
            "stdout-ok",
            "stderr-ok",
        ), result
        await provider.collect(handle)
        assert (handle.path / "outputs" / "result.txt").read_bytes() == content
        print(json.dumps({"stage": "tools_and_artifact_passed"}), flush=True)

        timed_out = await provider.execute(
            handle, ["sleep", "20"], timeout_seconds=timeout_seconds
        )
        assert timed_out.exit_code != 0, timed_out
        after = await provider.execute(handle, ["printf", "after-timeout"])
        assert after.stdout == "after-timeout"
        print(
            json.dumps(
                {
                    "stage": "timeout_passed",
                    "killed_exit_code": timed_out.exit_code,
                }
            ),
            flush=True,
        )
    finally:
        await provider.destroy(handle)
        print(json.dumps({"stage": "deleted", "sandbox_id": handle.sandbox_id}), flush=True)

    client = provider._client  # pyright: ignore[reportPrivateUsage]
    try:
        await client.sandbox_state(handle.sandbox_id)
    except RuntimeError:
        print(json.dumps({"stage": "sandbox_removed"}), flush=True)
    else:
        raise AssertionError("destroyed sandbox is still visible to the lifecycle API")
    finally:
        await client.aclose()
    print(json.dumps({"status": "passed", "seconds": round(time.monotonic() - started, 2)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=2.0,
        help="Command timeout used for the kill-and-recover stage",
    )
    parser.add_argument(
        "--remote-cli",
        action="store_true",
        help="Also install the pinned CLI in the sandbox and run a real SDK turn",
    )
    parser.add_argument(
        "--only",
        choices=("full", "deferred", "remote-cli"),
        default="full",
        help="Run a single stage instead of the whole suite",
    )
    arguments = parser.parse_args()
    if arguments.only == "full":
        asyncio.run(smoke(timeout_seconds=arguments.timeout_seconds))
    if arguments.only in ("full", "deferred"):
        asyncio.run(deferred_smoke())
    if arguments.remote_cli or arguments.only == "remote-cli":
        asyncio.run(remote_cli_smoke())
