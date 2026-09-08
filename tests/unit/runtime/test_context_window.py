# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import AsyncIterator
from typing import cast

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ContextUsageResponse, ProcessError, Transport

import harness.runtime.claude_sdk as claude_runtime
from harness.runtime.claude_sdk import (
    ContextWindowObservation,
    ContextWindowUnavailable,
    SessionResumeRecovery,
)


def _usage(total: int) -> ContextUsageResponse:
    return cast(
        ContextUsageResponse,
        {
            "categories": [
                {"name": "System prompt", "tokens": 40, "color": "blue"},
                {"name": "Messages", "tokens": total - 40, "color": "green"},
            ],
            "totalTokens": total,
            "maxTokens": 180_000,
            "rawMaxTokens": 200_000,
            "percentage": total / 1_800,
            "model": "claude-sonnet",
            "isAutoCompactEnabled": True,
            "autoCompactThreshold": 175_000,
            "memoryFiles": [{"path": "/private/CLAUDE.md"}],
            "mcpTools": [{"name": "private-tool"}],
            "agents": [{"name": "private-agent"}],
            "gridRows": [],
        },
    )


@pytest.mark.asyncio
async def test_default_query_observes_resumed_context_after_result_without_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        queried = False

        def __init__(self, *, options: ClaudeAgentOptions) -> None:
            self.options = options

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_context_usage(self) -> ContextUsageResponse:
            return _usage(120 if self.queried else 100)

        async def query(self, prompt: str) -> None:
            assert prompt == "hello"
            self.queried = True

        async def receive_response(self) -> AsyncIterator[object]:
            yield "provider-message"
            yield claude_runtime.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="sdk-session",
                stop_reason="end_turn",
            )

    monkeypatch.setattr(claude_runtime, "ClaudeSDKClient", FakeClient)

    observed = [
        item
        async for item in claude_runtime._default_query(
            "hello",
            ClaudeAgentOptions(resume="sdk-session"),
        )
    ]

    assert observed[0] == "provider-message"
    assert [item.phase for item in observed if isinstance(item, ContextWindowObservation)] == [
        "after"
    ]
    event = cast(ContextWindowObservation, observed[1]).event()
    assert event.type == "context.window.observed"
    assert event.payload["total_tokens"] == 120
    assert event.payload["auto_compact_threshold"] == 175_000
    assert event.payload["categories"] == [
        {"name": "System prompt", "tokens": 40},
        {"name": "Messages", "tokens": 80},
    ]
    assert "private" not in repr(event)
    assert isinstance(observed[2], claude_runtime.ResultMessage)


@pytest.mark.asyncio
async def test_client_query_uses_remote_transport_for_context_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_transport = cast(Transport, object())
    captured: list[Transport | None] = []

    class RemoteClient:
        def __init__(
            self,
            *,
            options: ClaudeAgentOptions,
            transport: Transport | None = None,
        ) -> None:
            self.options = options
            captured.append(transport)

        async def __aenter__(self) -> "RemoteClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_context_usage(self) -> ContextUsageResponse:
            return _usage(160)

        async def query(self, prompt: str) -> None:
            assert prompt == "hello remote"

        async def receive_response(self) -> AsyncIterator[object]:
            yield "remote-provider-message"
            yield claude_runtime.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="sdk-session",
                stop_reason="end_turn",
            )

    monkeypatch.setattr(claude_runtime, "ClaudeSDKClient", RemoteClient)

    observed = [
        item
        async for item in claude_runtime._client_query(
            "hello remote",
            ClaudeAgentOptions(resume="sdk-session"),
            transport=remote_transport,
        )
    ]

    assert captured == [remote_transport]
    assert observed[0] == "remote-provider-message"
    assert isinstance(observed[1], ContextWindowObservation)
    assert observed[1].total_tokens == 160
    assert isinstance(observed[2], claude_runtime.ResultMessage)


@pytest.mark.asyncio
async def test_context_control_api_failure_does_not_fail_model_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnsupportedClient:
        def __init__(self, *, options: ClaudeAgentOptions) -> None:
            self.options = options

        async def __aenter__(self) -> "UnsupportedClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_context_usage(self) -> ContextUsageResponse:
            raise RuntimeError("control method unsupported")

        async def query(self, _prompt: str) -> None:
            return None

        async def receive_response(self) -> AsyncIterator[object]:
            yield "provider-message"

    monkeypatch.setattr(claude_runtime, "ClaudeSDKClient", UnsupportedClient)

    observed = [
        item
        async for item in claude_runtime._default_query(
            "hello", ClaudeAgentOptions(resume="sdk-session")
        )
    ]

    assert observed[0] == "provider-message"
    unavailable = cast(ContextWindowUnavailable, observed[1])
    assert unavailable.reason == "control_unavailable"
    assert unavailable.event().type == "context.window.unavailable"


@pytest.mark.asyncio
async def test_context_control_api_timeout_does_not_block_resumed_model_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HangingClient:
        def __init__(self, *, options: ClaudeAgentOptions) -> None:
            self.options = options

        async def __aenter__(self) -> "HangingClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_context_usage(self) -> ContextUsageResponse:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def query(self, _prompt: str) -> None:
            return None

        async def receive_response(self) -> AsyncIterator[object]:
            yield "provider-message"

    monkeypatch.setattr(claude_runtime, "ClaudeSDKClient", HangingClient)
    monkeypatch.setattr(claude_runtime, "CONTEXT_USAGE_CONTROL_TIMEOUT_SECONDS", 0.001)

    observed = [
        item
        async for item in claude_runtime._default_query(
            "hello", ClaudeAgentOptions(resume="sdk-session")
        )
    ]

    assert observed[0] == "provider-message"
    unavailable = cast(ContextWindowUnavailable, observed[1])
    assert unavailable.reason == "control_timeout"


@pytest.mark.asyncio
async def test_fresh_session_skips_optional_context_control_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FreshClient:
        def __init__(self, *, options: ClaudeAgentOptions) -> None:
            self.options = options

        async def __aenter__(self) -> "FreshClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get_context_usage(self) -> ContextUsageResponse:
            raise AssertionError("fresh sessions must not call context control API")

        async def query(self, _prompt: str) -> None:
            return None

        async def receive_response(self) -> AsyncIterator[object]:
            yield "provider-message"

    monkeypatch.setattr(claude_runtime, "ClaudeSDKClient", FreshClient)

    observed = [item async for item in claude_runtime._default_query("hello", ClaudeAgentOptions())]

    assert observed == ["provider-message"]


@pytest.mark.asyncio
async def test_client_query_recovers_stale_resume_before_first_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resumes: list[str | None] = []

    class RecoveringClient:
        def __init__(self, *, options: ClaudeAgentOptions) -> None:
            self.options = options
            resumes.append(options.resume)

        async def __aenter__(self) -> "RecoveringClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def query(self, _prompt: str) -> None:
            return None

        async def receive_response(self) -> AsyncIterator[object]:
            if self.options.resume is not None:
                raise ProcessError("stale resume", exit_code=1)
            yield "recovered-provider-message"

    monkeypatch.setattr(claude_runtime, "ClaudeSDKClient", RecoveringClient)
    monkeypatch.setattr(claude_runtime, "SDK_STARTUP_RETRY_DELAYS_SECONDS", (0.0, 0.0))

    observed = [
        item
        async for item in claude_runtime._default_query(
            "hello", ClaudeAgentOptions(resume="stale-sdk-session")
        )
    ]

    assert resumes == ["stale-sdk-session", None]
    assert isinstance(observed[0], SessionResumeRecovery)
    assert observed[0].previous_session_id == "stale-sdk-session"
    assert observed[1:] == ["recovered-provider-message"]


@pytest.mark.asyncio
async def test_client_query_never_retries_after_sdk_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = 0

    class PartialClient:
        def __init__(self, *, options: ClaudeAgentOptions) -> None:
            nonlocal clients
            self.options = options
            clients += 1

        async def __aenter__(self) -> "PartialClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def query(self, _prompt: str) -> None:
            return None

        async def receive_response(self) -> AsyncIterator[object]:
            yield "visible-provider-message"
            raise ProcessError("late failure", exit_code=1)

    monkeypatch.setattr(claude_runtime, "ClaudeSDKClient", PartialClient)
    monkeypatch.setattr(claude_runtime, "SDK_STARTUP_RETRY_DELAYS_SECONDS", (0.0, 0.0))

    with pytest.raises(ProcessError, match="late failure"):
        _ = [item async for item in claude_runtime._default_query("hello", ClaudeAgentOptions())]

    assert clients == 1
