"""Real bundled CLI + local synthetic model: no external model API or credentials.

Measures process/protocol overhead and checks real MCP/hook attribution across
Runs. This is not a production TTFT benchmark. Run with PYTHONPATH=src.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import tempfile
import threading
import time
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SdkMcpTool,
    StreamEvent,
    create_sdk_mcp_server,
)

from harness.runtime import warm_sdk
from harness.runtime.ownership import ExecutionOwnership, execution_ownership
from harness.runtime.warm_sdk import WarmSdkPool

binding_run: ContextVar[str] = ContextVar("probe_binding_run", default="unbound")


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "count_tokens" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"input_tokens":128}')
            return
        last = request["messages"][-1]["content"]
        tool_result = isinstance(last, list) and any(
            item.get("type") == "tool_result" for item in last
        )
        content = (
            {"type": "text", "text": "OK"}
            if tool_result
            else {
                "type": "tool_use",
                "id": "tool-" + uuid4().hex,
                "name": "mcp__probe__publish",
                "input": {"text": "OK"},
            }
        )
        stop = "end_turn" if tool_result else "tool_use"
        message = {
            "id": "msg-" + uuid4().hex,
            "type": "message",
            "role": "assistant",
            "model": request["model"],
            "content": [content],
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": 128, "output_tokens": 8},
        }
        self.send_response(200)
        if not request.get("stream"):
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(message).encode())
            return
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        start = {
            **message,
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": 128, "output_tokens": 0},
        }
        block = {**content, "text": ""} if tool_result else {**content, "input": {}}
        delta = (
            {"type": "text_delta", "text": "OK"}
            if tool_result
            else {"type": "input_json_delta", "partial_json": '{"text":"OK"}'}
        )
        events = [
            {"type": "message_start", "message": start},
            {"type": "content_block_start", "index": 0, "content_block": block},
            {"type": "content_block_delta", "index": 0, "delta": delta},
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop, "stop_sequence": None},
                "usage": {"output_tokens": 8},
            },
            {"type": "message_stop"},
        ]
        for event in events:
            self.wfile.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())
        self.wfile.flush()


class Store:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], list[Any]] = defaultdict(list)
        self.serial = 0

    async def revision(self) -> str:
        return str(self.serial)

    async def append(self, key: Any, entries: list[Any]) -> None:
        stored = self.data[(key["session_id"], key.get("subpath", ""))]
        known = {entry.get("uuid") for entry in stored if entry.get("uuid")}
        additions = [
            entry for entry in entries if not entry.get("uuid") or entry["uuid"] not in known
        ]
        if additions:
            stored.extend(additions)
            self.serial += 1

    async def load(self, key: Any) -> list[Any] | None:
        return self.data.get((key["session_id"], key.get("subpath", "")))

    async def list_sessions(self, project_key: str) -> list[Any]:
        return [
            {"session_id": sid, "mtime": time.time() * 1000}
            for sid, subpath in self.data
            if not subpath
        ]

    async def list_subkeys(self, key: Any) -> list[str]:
        return [path for sid, path in self.data if sid == key["session_id"] and path]

    async def delete(self, key: Any) -> None:
        for item in list(self.data):
            if item[0] == key["session_id"]:
                del self.data[item]
        self.serial += 1


async def measure(base_url: str, samples: int, warm: bool) -> dict[str, Any]:
    pool = WarmSdkPool()
    store = Store()
    native_id = None
    callbacks: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    with tempfile.TemporaryDirectory(prefix="sdk-probe-runs-") as directory:
        try:
            for index in range(samples):
                run_id = f"{'warm' if warm else 'cold'}-{index}"
                workspace = Path(directory) / run_id
                workspace.mkdir()

                async def verify() -> None:
                    return None

                authority = ExecutionOwnership(verify)
                authority_token = execution_ownership.set(authority)
                run_token = binding_run.set(run_id)

                def make_tool(owner: str, root: Path):
                    async def publish(arguments: dict[str, Any]) -> dict[str, Any]:
                        actual = binding_run.get()
                        assert owner == actual, (owner, actual)
                        (root / "artifact.txt").write_text(actual)
                        callbacks.append({"kind": "mcp", "owner": owner, "context": actual})
                        return {"content": [{"type": "text", "text": "published"}]}

                    return publish

                def make_hook(owner: str):
                    async def hook(*args: Any) -> dict[str, Any]:
                        actual = binding_run.get()
                        assert owner == actual, (owner, actual)
                        callbacks.append({"kind": "hook", "owner": owner, "context": actual})
                        return {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "allow",
                            }
                        }

                    return hook

                server = create_sdk_mcp_server(
                    "probe",
                    tools=[
                        SdkMcpTool(
                            "publish",
                            "Publish the probe artifact.",
                            {"text": str},
                            make_tool(run_id, workspace),
                        )
                    ],
                )
                options = ClaudeAgentOptions(
                    tools=[],
                    model="claude-sonnet-4-6",
                    max_turns=4,
                    permission_mode="dontAsk",
                    cwd=workspace,
                    system_prompt="Call the publish tool once, then reply OK.",
                    mcp_servers={"probe": server},
                    strict_mcp_config=True,
                    hooks={"PreToolUse": [HookMatcher(hooks=[make_hook(run_id)])]},
                    include_partial_messages=True,
                    resume=native_id,
                    session_store=cast(Any, store),
                    session_store_flush="eager",
                    env={
                        "ANTHROPIC_BASE_URL": base_url,
                        "ANTHROPIC_AUTH_TOKEN": "local-probe-only",
                        "CLAUDE_CONFIG_DIR": str(workspace / ".config"),
                        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                    },
                    stderr=lambda line: diagnostics.append(line),
                )
                started = time.perf_counter()

                async def consume(
                    client: Any,
                    reused: bool,
                    *,
                    run_id: str = run_id,
                    workspace: Path = workspace,
                    started: float = started,
                ) -> None:
                    nonlocal native_id
                    ready = time.perf_counter()
                    await client.query(f"Probe {run_id}: publish once and reply OK.")
                    first_text = None
                    result = None
                    async for message in client.receive_response():
                        if isinstance(message, StreamEvent):
                            delta = message.event.get("delta", {})
                            if delta.get("type") == "text_delta" and first_text is None:
                                first_text = time.perf_counter()
                        if isinstance(message, ResultMessage):
                            result = message
                    assert result is not None and not result.is_error, diagnostics[-5:]
                    if native_id is not None:
                        await client.get_context_usage()
                    native_id = result.session_id
                    if warm:
                        await client.finish(result)
                    assert (workspace / "artifact.txt").read_text() == run_id
                    rows.append(
                        {
                            "run": run_id,
                            "reused": reused,
                            "connect_or_acquire_ms": (ready - started) * 1000,
                            "first_text_ms": ((first_text or time.perf_counter()) - started) * 1000,
                            "total_ms": (time.perf_counter() - started) * 1000,
                        }
                    )

                try:
                    async with asyncio.timeout(45):
                        if warm:
                            async with pool.acquire(
                                ("probe", "probe", "session"),
                                options,
                                scope="probe",
                                prepare=lambda root: None,
                            ) as entry:
                                assert entry is not None
                                await consume(entry, entry.reused)
                        else:
                            async with ClaudeSDKClient(options=replace(options)) as client:
                                await consume(client, False)
                finally:
                    authority.active = False
                    execution_ownership.reset(authority_token)
                    binding_run.reset(run_token)
        finally:
            await pool.close()
    assert len([item for item in callbacks if item["kind"] == "mcp"]) == samples, callbacks
    return {
        "samples": rows,
        "callbacks": callbacks,
        "median_connect_or_acquire_ms": statistics.median(
            row["connect_or_acquire_ms"] for row in rows[1:] or rows
        ),
    }


async def main(samples: int) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        cold = await measure(url, samples, False)
        warm = await measure(url, samples, True)
        print(
            json.dumps(
                {
                    "kind": "real CLI; synthetic localhost model; not production TTFT",
                    "sdk_version": version("claude-agent-sdk"),
                    "platform": platform.platform(),
                    "warm_source_sha256": hashlib.sha256(
                        Path(warm_sdk.__file__).read_bytes()
                    ).hexdigest(),
                    "cold": cold,
                    "warm": warm,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3)
    asyncio.run(main(parser.parse_args().samples))
