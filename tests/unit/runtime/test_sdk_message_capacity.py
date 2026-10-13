"""Regression for image-heavy transcript mirrors emitted during compaction."""

# pyright: reportPrivateUsage=false

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, CLIJSONDecodeError
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

import harness.runtime.claude_sdk as sdk_runtime
from harness.runtime.base import RuntimeResultError


class ExitedProcess:
    async def wait(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_compaction_mirror_above_old_limit_keeps_following_result() -> None:
    # The mirror combines many individually valid image results in ONE frame.
    # Exercise the installed SDK framer with split chunks, including a result
    # sharing the final chunk, rather than merely asserting an option constant.
    frame = (
        json.dumps(
            {
                "type": "transcript_mirror",
                "filePath": "session.jsonl",
                "entries": [{"image_data": "a" * (36 * 1024 * 1024)}],
            }
        )
        + '\n{"type":"result","is_error":false}\n'
    )

    async def stdout() -> AsyncIterator[str]:
        for offset in range(0, len(frame), 65536):
            yield frame[offset : offset + 65536]

    transport = SubprocessCLITransport(
        prompt="continue",
        options=ClaudeAgentOptions(max_buffer_size=sdk_runtime.SDK_JSON_MAX_BUFFER_SIZE),
    )
    transport._process = ExitedProcess()  # type: ignore[assignment]
    transport._stdout_stream = stdout()  # type: ignore[assignment]
    types = [message["type"] async for message in transport.read_messages()]
    assert types == ["transcript_mirror", "result"]


@pytest.mark.asyncio
async def test_sdk_message_limit_remains_enforced() -> None:
    async def stdout() -> AsyncIterator[str]:
        yield '{"type":"user","content":"' + "a" * 2048

    transport = SubprocessCLITransport(
        prompt="continue",
        options=ClaudeAgentOptions(max_buffer_size=1024),
    )
    transport._process = ExitedProcess()  # type: ignore[assignment]
    transport._stdout_stream = stdout()  # type: ignore[assignment]
    with pytest.raises(CLIJSONDecodeError, match="exceeded maximum buffer size"):
        _messages = [message async for message in transport.read_messages()]


@pytest.mark.asyncio
@pytest.mark.parametrize("oversized", [True, False])
async def test_decode_failure_has_safe_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
    oversized: bool,
) -> None:
    class FailingClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FailingClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def query(self, prompt: str) -> None:
            raise CLIJSONDecodeError(
                "JSON message exceeded maximum buffer size"
                if oversized
                else "private malformed data",
                ValueError("private image payload"),
            )

    monkeypatch.setattr(sdk_runtime, "ClaudeSDKClient", FailingClient)
    with pytest.raises(RuntimeResultError) as failure:
        _messages = [
            message async for message in sdk_runtime._client_query("continue", ClaudeAgentOptions())
        ]
    expected = "runtime_message_too_large" if oversized else "runtime_invalid_json"
    assert failure.value.error_code == expected
    assert failure.value.user_message
    assert "private" not in failure.value.user_message
    assert isinstance(failure.value.__cause__, CLIJSONDecodeError)
