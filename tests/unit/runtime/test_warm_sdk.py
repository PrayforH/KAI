"""Exercise the real SDK protocol with a model-free CLI transport."""

import asyncio
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ProcessError,
    ResultMessage,
    SdkMcpTool,
    Transport,
    create_sdk_mcp_server,
)
from mcp import types as mcp_types

from harness.runtime.claude_sdk import _client_query
from harness.runtime.ownership import ExecutionOwnership, execution_ownership
from harness.runtime.warm_sdk import WarmSdkPool, WarmSdkRequest

current_run: ContextVar[str] = ContextVar("test_current_run", default="unbound")


class Store:
    def __init__(self) -> None:
        self.value = "0"

    async def revision(self) -> str:
        return self.value

    async def load(self, key: Any) -> None:
        return None

    async def append(self, key: Any, entries: Any) -> None:
        pass

    async def list_sessions(self, key: Any) -> list[Any]:
        return []


class FakeCLI(Transport):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.closed = False
        self.turn = 0
        self.callback_id = ""

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    async def end_input(self) -> None:
        pass

    def is_ready(self) -> bool:
        return not self.closed

    async def read_messages(self):
        while True:
            item = await self.queue.get()
            if isinstance(item, BaseException):
                raise item
            yield item

    async def write(self, data: str) -> None:
        message = json.loads(data)
        if message["type"] == "control_request":
            request = message["request"]
            response: dict[str, Any] = {}
            if request["subtype"] == "initialize":
                self.callback_id = request["hooks"]["PreToolUse"][0]["hookCallbackIds"][0]
            elif request["subtype"] == "get_context_usage":
                response = {
                    "totalTokens": 12,
                    "maxTokens": 100,
                    "rawMaxTokens": 100,
                    "percentage": 12,
                    "model": "fake",
                    "isAutoCompactEnabled": False,
                    "categories": [],
                }
            else:
                raise AssertionError(request)
            await self.queue.put(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": message["request_id"],
                        "response": response,
                    },
                }
            )
        elif message["type"] == "user":
            self.turn += 1
            await self.queue.put(
                {
                    "type": "control_request",
                    "request_id": f"hook-{self.turn}",
                    "request": {
                        "subtype": "hook_callback",
                        "callback_id": self.callback_id,
                        "input": {},
                        "tool_use_id": f"tool-{self.turn}",
                    },
                }
            )
        elif message["type"] == "control_response":
            assert message["response"]["subtype"] == "success", message
            await self.queue.put(
                {
                    "type": "result",
                    "subtype": "success",
                    "duration_ms": 0,
                    "duration_api_ms": 0,
                    "is_error": False,
                    "num_turns": 1,
                    "session_id": "native-session",
                    "result": "OK",
                    "stop_reason": "end_turn",
                }
            )


class Harness:
    def __init__(self, **limits: Any) -> None:
        self.transports: list[FakeCLI] = []
        self.callbacks: list[tuple[str, str]] = []
        self.store = Store()
        self.pool = WarmSdkPool(client_factory=self.client, **limits)
        self.key = ("tenant", "user", "session")

    def client(self, *, options: ClaudeAgentOptions) -> ClaudeSDKClient:
        transport = FakeCLI()
        self.transports.append(transport)
        return ClaudeSDKClient(options, transport=transport)

    def options(self, run: str, *, resume: str | None = None) -> ClaudeAgentOptions:
        async def hook(*args: Any) -> dict[str, Any]:
            self.callbacks.append((run, current_run.get()))
            return {}

        async def tool(arguments: dict[str, Any]) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": f"{run}:{current_run.get()}"}]}

        server = create_sdk_mcp_server(
            "test",
            tools=[SdkMcpTool(name="read", description="read", input_schema={}, handler=tool)],
        )
        return ClaudeAgentOptions(
            tools=[],
            hooks={"PreToolUse": [HookMatcher(hooks=[hook])]},
            mcp_servers={"test": server},
            session_store=self.store,
            resume=resume,
        )

    @asynccontextmanager
    async def run(self, name: str):
        async def verify() -> None:
            pass

        authority = ExecutionOwnership(verify)
        token = execution_ownership.set(authority)
        run_token = current_run.set(name)
        try:
            yield authority
        finally:
            authority.active = False
            execution_ownership.reset(token)
            current_run.reset(run_token)

    async def query(self, run: str, *, resume: str | None = None, **changes: Any) -> None:
        async with self.run(run):
            options = replace(self.options(run, resume=resume), **changes)
            request = WarmSdkRequest(self.pool, self.key, "scope", lambda root: None)
            messages = [message async for message in _client_query("hello", options, warm=request)]
            assert isinstance(messages[-1], ResultMessage)


@pytest.fixture(autouse=True)
def never_start_a_real_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Unexpected cold fallback; real CLI is forbidden in this test")

    monkeypatch.setattr("harness.runtime.claude_sdk.ClaudeSDKClient", forbidden)


@pytest_asyncio.fixture
async def harness():
    instance = Harness()
    try:
        yield instance
    finally:
        await instance.pool.close()


@pytest.mark.asyncio
async def test_two_runs_use_one_connection_and_current_hook_context(harness: Harness) -> None:
    await harness.query("run-1")
    entry = harness.pool.entries[harness.key]
    assert entry.workspace.exists()
    await harness.query("run-2", resume="native-session")
    assert len(harness.transports) == 1
    assert harness.callbacks == [("run-1", "run-1"), ("run-2", "run-2")]
    assert not harness.transports[0].closed
    await harness.pool.close()
    assert harness.transports[0].closed and not entry.workspace.exists()


@pytest.mark.asyncio
async def test_mcp_proxy_uses_current_server_and_context(harness: Harness) -> None:
    for run, resume in [("one", None), ("two", "native-session")]:
        async with harness.run(run):
            async with harness.pool.acquire(
                harness.key,
                harness.options(run, resume=resume),
                scope="scope",
                prepare=lambda root: None,
            ) as entry:
                assert entry is not None
                proxy = entry.client.options.mcp_servers["test"]["instance"]
                request = mcp_types.CallToolRequest(
                    method="tools/call", params=mcp_types.CallToolRequestParams(name="read")
                )
                result = await proxy.request_handlers[mcp_types.CallToolRequest](request)
                assert result.root.content[0].text == f"{run}:{run}"
                await entry.query("hello")
                async for message in entry.receive_response():
                    if isinstance(message, ResultMessage):
                        await entry.finish(message)
    assert len(harness.transports) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["history", "rebase", "credential", "schema", "owner"])
async def test_changed_binding_discards_connection(harness: Harness, change: str) -> None:
    await harness.query("run-1")
    resume = "native-session"
    changes: dict[str, Any] = {}
    if change == "history":
        harness.store.value = "other-worker-wrote"
    elif change == "rebase":
        resume = None
    elif change == "credential":
        changes["env"] = {"ANTHROPIC_AUTH_TOKEN": "rotated"}
    elif change == "schema":
        changes["system_prompt"] = "new policy"
    else:
        harness.key = ("tenant", "another-user", "session")
    await harness.query("run-2", resume=resume, **changes)
    assert len(harness.transports) == 2
    if change != "owner":
        assert harness.transports[0].closed


@pytest.mark.asyncio
async def test_abandoned_run_and_revoked_callbacks_destroy_connection(harness: Harness) -> None:
    async with harness.run("cancel") as authority:
        async with harness.pool.acquire(
            harness.key,
            harness.options("cancel"),
            scope="scope",
            prepare=lambda root: None,
        ) as entry:
            assert entry is not None
            authority.active = False
            with pytest.raises(RuntimeError, match="revoked"):
                await entry.query("must not send")
    assert harness.transports[0].turn == 0
    assert harness.transports[0].closed and not harness.pool.entries


@pytest.mark.asyncio
async def test_late_idle_message_invalidates_cached_session(harness: Harness) -> None:
    await harness.query("run-1")
    await harness.transports[0].queue.put({"type": "system", "subtype": "late", "data": {}})
    await asyncio.sleep(0.02)
    await harness.query("run-2", resume="native-session")
    assert len(harness.transports) == 2 and harness.transports[0].closed


@pytest.mark.asyncio
async def test_idle_ttl_and_capacity_release_processes() -> None:
    harness = Harness(idle_seconds=0.03, max_sessions=1)
    try:
        await harness.query("one")
        await asyncio.sleep(0.08)
        assert harness.transports[0].closed and not harness.pool.entries
        await harness.query("two", resume="native-session")
        harness.key = ("tenant", "user", "other-session")
        await harness.query("three")
        assert harness.transports[1].closed
        assert len(harness.pool.entries) == 1
    finally:
        await harness.pool.close()


@pytest.mark.asyncio
async def test_native_file_tools_and_missing_authority_are_ineligible(harness: Harness) -> None:
    assert not harness.pool.eligible(harness.options("one"))
    async with harness.run("one"):
        assert not harness.pool.eligible(replace(harness.options("one"), tools=["Bash"]))
        assert harness.pool.eligible(harness.options("one"))


@pytest.mark.asyncio
async def test_runtime_reuses_across_run_workspace_snapshots(
    harness: Harness, tmp_path: Path
) -> None:
    from harness.core.manifest import load_manifest
    from harness.core.models import (
        AgentVersion,
        AgentVersionStatus,
        ModelCompatibility,
        ModelRoute,
        Run,
        RunStatus,
        Session,
    )
    from harness.runtime.base import RuntimeContext
    from harness.runtime.claude_sdk import ClaudeSdkRuntime

    snapshot = load_manifest("tests/fixtures/agents/echo-agent/agent.yaml")
    now = datetime.now(UTC)
    version = AgentVersion(
        tenant_id="tenant",
        owner_user_id="user",
        name="echo-agent",
        version="0.1.0",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash=snapshot.content_hash,
        snapshot=snapshot.model_dump(mode="json"),
        created_at=now,
    )
    route = ModelRoute(
        route_id="new-api-default",
        provider="new-api",
        base_url="http://unused",
        model="fake",
        compatibility=ModelCompatibility.FULL,
        capabilities=frozenset({"streaming", "tool_use"}),
    )

    class Gate:
        def hooks(self, context: Any, **kwargs: Any) -> Any:
            return harness.options(context.run.run_id).hooks

    async def executor(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("This test must not execute a real sandbox")

    runtime = ClaudeSdkRuntime(
        agent_version=version,
        routes=[route],
        route_secrets={"new-api-default": "test"},
        warm_pool=harness.pool,
        session_store=harness.store,
        tool_gate=Gate(),
    )
    for index in range(2):
        run_id = f"run-{index}"
        workspace = tmp_path / run_id
        workspace.mkdir()
        context = RuntimeContext(
            run=Run(
                run_id=run_id,
                session_id="session",
                tenant_id="tenant",
                status=RunStatus.RUNNING,
                idempotency_key=run_id,
                created_at=now,
                updated_at=now,
                input={"prompt": "hello"},
            ),
            session=Session(
                session_id="session",
                tenant_id="tenant",
                user_id="user",
                agent_name="echo-agent",
                agent_version="0.1.0",
                created_at=now,
                workspace_snapshot_id=f"snapshot-{index}",
                runtime_thread_id=None if index == 0 else "native-session",
            ),
            workspace=workspace,
            sandbox_provider="opensandbox-deferred",
            sandbox_command_executor=executor,
        )
        async with harness.run(run_id):
            events = [event async for event in runtime.execute(context)]
            assert any(event.type == "runtime.result" for event in events)
        assert not (workspace / ".harness-runtime").exists()
    assert len(harness.transports) == 1
    assert harness.callbacks == [("run-0", "run-0"), ("run-1", "run-1")]


@pytest.mark.asyncio
async def test_failed_warm_query_is_never_replayed(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = FakeCLI.write

    async def broken(self: FakeCLI, data: str) -> None:
        if json.loads(data)["type"] == "user":
            self.turn += 1
            await self.queue.put(ProcessError("connection lost after write", exit_code=1))
        else:
            await original(self, data)

    monkeypatch.setattr(FakeCLI, "write", broken)
    with pytest.raises(ProcessError):
        await harness.query("one")
    assert len(harness.transports) == 1
    assert harness.transports[0].turn == 1 and harness.transports[0].closed


@pytest.mark.asyncio
async def test_expired_lease_blocks_buffered_output_before_heartbeat(harness: Harness) -> None:
    async with harness.run("paused") as authority:
        async with harness.pool.acquire(
            harness.key,
            harness.options("paused"),
            scope="scope",
            prepare=lambda root: None,
        ) as entry:
            assert entry is not None and entry.binding is not None
            await entry.binding.messages.put("buffered-before-pause")
            authority.expires_at = 0
            messages = entry.receive_messages()
            with pytest.raises(RuntimeError, match="revoked"):
                await anext(messages)
    assert harness.transports[0].closed


@pytest.mark.asyncio
async def test_disconnect_can_flush_store_with_current_run_but_cannot_call_tools(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    flushed: list[str] = []
    original = ClaudeSDKClient.disconnect

    async def append(*args: Any) -> None:
        flushed.append(current_run.get())

    async def disconnect(client: ClaudeSDKClient) -> None:
        try:
            with pytest.raises(RuntimeError, match="no active Run"):
                await client.options.hooks["PreToolUse"][0].hooks[0]({}, None, {})
            await client.options.session_store.append({}, [])
        finally:
            await original(client)

    monkeypatch.setattr(harness.store, "append", append)
    monkeypatch.setattr(ClaudeSDKClient, "disconnect", disconnect)
    async with harness.run("cancelled-but-owned"):
        async with harness.pool.acquire(
            harness.key,
            harness.options("cancelled-but-owned"),
            scope="scope",
            prepare=lambda root: None,
        ) as entry:
            assert entry is not None
            # No terminal result: eviction/ordinary cancellation path.
    assert flushed == ["cancelled-but-owned"]
    assert harness.transports[0].closed


@pytest.mark.asyncio
async def test_fresh_run_drains_post_result_mirror_before_releasing_binding(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    flushed: list[str] = []
    original = FakeCLI.write

    async def append(key: Any, entries: Any) -> None:
        flushed.append(current_run.get())
        harness.store.value = str(len(flushed))

    async def write(cli: FakeCLI, data: str) -> None:
        message = json.loads(data)
        if (
            message["type"] == "control_request"
            and message["request"]["subtype"] == "get_context_usage"
        ):
            root = harness.pool.entries[harness.key].workspace
            await cli.queue.put(
                {
                    "type": "transcript_mirror",
                    "filePath": str(root / ".runtime-config/projects/project/native-session.jsonl"),
                    "entries": [{"type": "system", "uuid": f"tail-{cli.turn}"}],
                }
            )
        await original(cli, data)

    monkeypatch.setattr(harness.store, "append", append)
    monkeypatch.setattr(FakeCLI, "write", write)
    await harness.query("first")
    entry = harness.pool.entries[harness.key]
    assert flushed == ["first"]
    assert entry.revision == "1"
    await harness.query("second", resume="native-session")
    assert flushed == ["first", "second"]
    assert entry.revision == "2"
    assert len(harness.transports) == 1


@pytest.mark.asyncio
async def test_finish_waits_for_detached_eager_flush_before_unbinding(harness: Harness) -> None:
    """A detached flush may start after a direct empty flush has returned."""
    async with harness.run("flush-owner"):
        async with harness.pool.acquire(
            harness.key, harness.options("flush-owner"), scope="scope", prepare=lambda root: None
        ) as entry:
            assert entry is not None
            flushed: list[str] = []

            class PendingFlush:
                async def wait(self) -> None:
                    await asyncio.sleep(0)
                    await entry.dispatch(
                        lambda binding: harness.store.append({}, []), store_flush=True
                    )
                    flushed.append(current_run.get())
                    harness.store.value = "flushed"

            batcher = entry.client._query._transcript_mirror_batcher
            batcher._flush_task = PendingFlush()
            result = ResultMessage(
                subtype="success", duration_ms=0, duration_api_ms=0,
                is_error=False, num_turns=1, session_id="native-session", stop_reason="end_turn",
            )
            await entry.finish(result)
            assert flushed == ["flush-owner"]
            assert entry.revision == "flushed"
            assert entry.binding is not None and entry.binding.complete


@pytest.mark.asyncio
async def test_slow_local_context_stats_do_not_evict_a_healthy_warm_session(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = FakeCLI.write

    async def delayed_context(cli: FakeCLI, data: str) -> None:
        message = json.loads(data)
        if message.get("request", {}).get("subtype") == "get_context_usage":
            await asyncio.sleep(1.05)
        await original(cli, data)

    monkeypatch.setattr(FakeCLI, "write", delayed_context)
    await harness.query("first")
    await harness.query("second", resume="native-session")
    assert len(harness.transports) == 1
    assert harness.pool.entries[harness.key].healthy


@pytest.mark.asyncio
async def test_stalled_context_control_is_still_bounded_and_evicts_connection(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = FakeCLI.write

    async def stalled_context(cli: FakeCLI, data: str) -> None:
        message = json.loads(data)
        if message.get("request", {}).get("subtype") == "get_context_usage":
            await asyncio.Event().wait()
        await original(cli, data)

    monkeypatch.setattr(FakeCLI, "write", stalled_context)
    async with asyncio.timeout(2):
        await harness.query("stalled")
    assert harness.transports[0].closed
    assert harness.key not in harness.pool.entries
