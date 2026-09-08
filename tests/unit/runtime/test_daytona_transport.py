import asyncio
from dataclasses import replace
from typing import Any, cast

import pytest
from claude_agent_sdk import (
    ClaudeAgentOptions,
    SessionKey,
    SessionListSubkeysKey,
    SessionStoreEntry,
    create_sdk_mcp_server,
)

from harness.runtime.daytona_transport import (
    DaytonaClaudeTransport,
    DaytonaTransportError,
    build_remote_claude_command,
)


class FakeRemoteSession:
    def __init__(
        self,
        stdout: list[bytes | None],
        *,
        stderr: list[bytes | None] | None = None,
        exit_code: int = 0,
        hang_terminate: bool = False,
    ) -> None:
        self.stdout = asyncio.Queue[bytes | None]()
        self.stderr = asyncio.Queue[bytes | None]()
        for chunk in stdout:
            self.stdout.put_nowait(chunk)
        for chunk in stderr or [None]:
            self.stderr.put_nowait(chunk)
        self.exit_code = exit_code
        self.hang_terminate = hang_terminate
        self.started: tuple[list[str], str, dict[str, str]] | None = None
        self.writes: list[str] = []
        self.ended = False
        self.terminated = False
        self.staged_config: tuple[str, dict[str, bytes]] | None = None

    async def stage_config(
        self, remote_directory: str, files: dict[str, bytes]
    ) -> None:
        self.staged_config = (remote_directory, files)

    async def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        self.started = (argv, cwd, env)

    async def write(self, data: str) -> None:
        self.writes.append(data)

    async def end_input(self) -> None:
        self.ended = True

    async def read_stdout(self) -> bytes | None:
        return await self.stdout.get()

    async def read_stderr(self) -> bytes | None:
        return await self.stderr.get()

    async def wait(self) -> int:
        return self.exit_code

    async def terminate(self) -> None:
        self.terminated = True
        if self.hang_terminate:
            await asyncio.Event().wait()


def options() -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        tools=["Read", "Bash"],
        allowed_tools=["Read"],
        system_prompt="Act carefully",
        model="gateway-model",
        max_turns=8,
        permission_mode="dontAsk",
        include_partial_messages=True,
        strict_mcp_config=True,
        env={"ANTHROPIC_BASE_URL": "https://gateway.example"},
    )


def test_command_uses_native_cli_and_contains_required_streaming_flags() -> None:
    command = build_remote_claude_command(
        options(), cli_path="/home/daytona/.local/bin/claude"
    )

    assert command[0] == "/home/daytona/.local/bin/claude"
    assert command[-2:] == ["--input-format", "stream-json"]
    assert command[command.index("--model") + 1] == "gateway-model"
    assert command[command.index("--tools") + 1] == "Read,Bash"
    assert "--include-partial-messages" in command
    assert "--strict-mcp-config" in command


@pytest.mark.asyncio
async def test_transport_marks_remote_cli_as_an_sdk_child() -> None:
    session = FakeRemoteSession([None])
    transport = DaytonaClaudeTransport(
        session=session,
        options=options(),
        remote_workspace="/workspace/run-a",
        cli_path="/home/daytona/.local/bin/claude",
    )

    await transport.connect()

    assert session.started is not None
    environment = session.started[2]
    assert environment["CLAUDE_CODE_ENTRYPOINT"] == "sdk-py"
    assert environment["CLAUDE_AGENT_SDK_VERSION"]
    assert environment["HARNESS_DAYTONA_STDOUT_FLUSH_PADDING"] == "1"
    await transport.close()


def test_command_enables_transcript_mirror_for_external_session_store() -> None:
    configured = replace(options(), session_store=cast(Any, object()))

    command = build_remote_claude_command(
        configured, cli_path="/home/daytona/.local/bin/claude"
    )

    assert "--session-mirror" in command


@pytest.mark.asyncio
async def test_transport_moves_mcp_credentials_out_of_command_metadata() -> None:
    configured = replace(
        options(),
        mcp_servers=cast(
            Any,
            {
                "remote": {
                    "type": "http",
                    "url": "https://mcp.example",
                    "headers": {"Authorization": "private-mcp-token"},
                }
            },
        ),
    )
    session = FakeRemoteSession([None])
    transport = DaytonaClaudeTransport(
        session=session,
        options=configured,
        remote_workspace="/workspace/run-a",
        cli_path="/home/daytona/.local/bin/claude",
    )

    await transport.connect()

    assert session.started is not None
    argv, _cwd, environment = session.started
    assert "--mcp-config" not in argv
    assert "private-mcp-token" not in " ".join(argv)
    assert "private-mcp-token" in environment["HARNESS_CLAUDE_MCP_CONFIG"]
    await transport.close()


@pytest.mark.asyncio
async def test_transport_rejects_in_process_sdk_mcp_server() -> None:
    configured = replace(
        options(),
        mcp_servers={"local-python": create_sdk_mcp_server("local-python", tools=[])},
    )
    session = FakeRemoteSession([None])
    transport = DaytonaClaudeTransport(
        session=session,
        options=configured,
        remote_workspace="/workspace/run-a",
        cli_path="/home/daytona/.local/bin/claude",
    )

    with pytest.raises(DaytonaTransportError, match="authenticated HTTP MCP"):
        await transport.connect()

    assert session.started is None


@pytest.mark.asyncio
async def test_transport_materializes_external_session_store_for_remote_resume() -> None:
    session_id = "12345678-1234-4234-8234-123456789abc"

    class FakeStore:
        async def append(
            self, _key: SessionKey, _entries: list[SessionStoreEntry]
        ) -> None:
            return

        async def load(self, _key: SessionKey) -> list[SessionStoreEntry]:
            return [
                cast(
                    SessionStoreEntry,
                    {
                        "type": "user",
                        "uuid": "87654321-4321-4321-8321-cba987654321",
                        "sessionId": session_id,
                        "message": {"role": "user", "content": "first turn"},
                    },
                )
            ]

        async def list_subkeys(self, _key: SessionListSubkeysKey) -> list[str]:
            return []

    configured = replace(
        options(),
        resume=session_id,
        session_store=FakeStore(),
        env={
            **options().env,
            "CLAUDE_CONFIG_DIR": "/remote/config/session-a",
        },
    )
    session = FakeRemoteSession([None])
    transport = DaytonaClaudeTransport(
        session=session,
        options=configured,
        remote_workspace="/workspace/run-b",
        cli_path="/home/daytona/.local/bin/claude",
    )

    await transport.connect()

    assert session.staged_config is not None
    remote_directory, files = session.staged_config
    assert remote_directory == "/remote/config/session-a"
    transcript_paths = [
        path for path in files if path.endswith(f"/{session_id}.jsonl")
    ]
    assert len(transcript_paths) == 1
    assert b"first turn" in files[transcript_paths[0]]
    assert session.started is not None
    assert session.started[2]["CLAUDE_CONFIG_DIR"] == remote_directory
    await transport.close()


@pytest.mark.asyncio
async def test_transport_frames_fragmented_ndjson_and_skips_diagnostics() -> None:
    session = FakeRemoteSession(
        [
            b"install notice\n{\"type\":\"system\",\"sub",
            b"type\":\"init\"}\n{\"type\":\"result\",\"ok\":true}\n",
            None,
        ],
        stderr=[b"stderr diagnostic", None],
    )
    transport = DaytonaClaudeTransport(
        session=session,
        options=options(),
        remote_workspace="/workspace/run-a",
        cli_path="/home/daytona/.local/bin/claude",
    )

    await transport.connect()
    await transport.write('{"type":"user"}\n')
    messages = [message async for message in transport.read_messages()]
    await transport.end_input()
    await transport.close()

    assert messages == [
        {"type": "system", "subtype": "init"},
        {"type": "result", "ok": True},
    ]
    assert session.started is not None
    assert session.started[1] == "/workspace/run-a"
    assert session.writes == ['{"type":"user"}\n']
    assert session.ended is True


@pytest.mark.asyncio
async def test_transport_parses_complete_fragmented_json_without_newline() -> None:
    session = FakeRemoteSession(
        [
            b'{"type":"control_response","response":{"request_id":"req_1",',
            b'"subtype":"success"}}',
            None,
        ]
    )
    transport = DaytonaClaudeTransport(
        session=session,
        options=options(),
        remote_workspace="/workspace/run-a",
        cli_path="/home/daytona/.local/bin/claude",
    )

    await transport.connect()
    messages = [message async for message in transport.read_messages()]
    await transport.close()

    assert messages == [
        {
            "type": "control_response",
            "response": {"request_id": "req_1", "subtype": "success"},
        }
    ]


@pytest.mark.asyncio
async def test_transport_reports_remote_exit_without_exposing_stderr() -> None:
    session = FakeRemoteSession(
        [None], stderr=[b"ANTHROPIC_AUTH_TOKEN=private", None], exit_code=17
    )
    transport = DaytonaClaudeTransport(
        session=session,
        options=options(),
        remote_workspace="/workspace/run-a",
        cli_path="/home/daytona/.local/bin/claude",
    )
    await transport.connect()

    with pytest.raises(DaytonaTransportError, match="code 17") as captured:
        async for _message in transport.read_messages():
            pass

    assert "private" not in str(captured.value)


@pytest.mark.asyncio
async def test_close_is_bounded_even_if_remote_termination_hangs() -> None:
    session = FakeRemoteSession([None], hang_terminate=True)
    transport = DaytonaClaudeTransport(
        session=session,
        options=options(),
        remote_workspace="/workspace/run-a",
        cli_path="/home/daytona/.local/bin/claude",
        close_timeout=0.01,
    )
    await transport.connect()

    await asyncio.wait_for(transport.close(), timeout=0.2)

    assert session.terminated is True
    assert transport.is_ready() is False


def test_remote_command_ignores_legacy_budget() -> None:
    command = build_remote_claude_command(
        replace(options(), max_budget_usd=0.001), cli_path="/home/daytona/.local/bin/claude"
    )
    assert "--max-budget-usd" not in command
    assert "--permission-mode" in command
