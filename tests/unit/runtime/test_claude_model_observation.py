"""The Claude runtime's half of a model observation.

The vocabulary moved to `observability/model_span`. What stays here is the part
only this SDK can answer -- what its `ResultMessage` says a call cost -- so this
test exists to prove the extraction did not quietly change published telemetry
while looking like a refactor. It drives the changed unit directly for that
reason; the runtime's public path is covered elsewhere.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import pytest
from claude_agent_sdk import ResultMessage

from harness.core.manifest import load_manifest
from harness.core.models import (
    AgentVersion,
    AgentVersionStatus,
    ModelCompatibility,
    ModelRoute,
)
from harness.observability.model_span import MODEL_SPAN_NAME
from harness.runtime.claude_sdk import ClaudeSdkRuntime
from tests.conftest import SpanRecorder


def _route() -> ModelRoute:
    return ModelRoute(
        route_id="new-api-default",
        provider="new-api",
        base_url="https://new-api.example/v1",
        model="gateway-model",
        compatibility=ModelCompatibility.FULL,
        capabilities=frozenset({"streaming", "tool_use"}),
    )


def _runtime(span_recorder: SpanRecorder) -> ClaudeSdkRuntime:
    snapshot = load_manifest("tests/fixtures/agents/echo-agent/agent.yaml")
    version = AgentVersion(
        tenant_id="tenant-a",
        owner_user_id="user-a",
        name="echo-agent",
        version="0.1.0",
        status=AgentVersionStatus.PUBLISHED,
        manifest_hash=snapshot.content_hash,
        snapshot=snapshot.model_dump(mode="json"),
        created_at=datetime.now(UTC),
    )
    return ClaudeSdkRuntime(
        agent_version=version,
        routes=[_route()],
        route_secrets={"new-api-default": "secret"},
        observability=span_recorder.observability,
    )


async def _messages(*messages: object) -> AsyncIterator[object]:
    for message in messages:
        yield message


@pytest.mark.asyncio
async def test_the_sdk_result_is_reported_through_the_shared_observation(
    span_recorder: SpanRecorder,
) -> None:
    result = ResultMessage(
        subtype="success",
        duration_ms=42,
        duration_api_ms=30,
        is_error=False,
        num_turns=3,
        session_id="sdk-session",
        total_cost_usd=0.25,
        usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_input_tokens": 7,
        },
        stop_reason="end_turn",
        result="完成",
    )
    runtime = _runtime(span_recorder)

    consumed = [
        message
        async for message in runtime._model_messages(  # pyright: ignore[reportPrivateUsage]
            _messages(result), run_id="run-1", route=_route(), prompt="你好"
        )
    ]

    assert consumed == [result]
    attributes = span_recorder.attributes(MODEL_SPAN_NAME)
    assert attributes["langfuse.observation.type"] == "generation"
    assert attributes["langfuse.observation.model.name"] == "gateway-model"
    assert attributes["harness.model.route"] == "new-api-default"
    assert attributes["gen_ai.usage.input_tokens"] == 100
    assert attributes["gen_ai.usage.output_tokens"] == 20
    assert attributes["harness.usage.cache_read_input_tokens"] == 7
    assert json.loads(str(attributes["langfuse.observation.usage_details"])) == {
        "input": 100,
        "output": 20,
        "cache_read_input": 7,
    }
    assert json.loads(str(attributes["langfuse.observation.cost_details"])) == {"total": 0.25}
    assert attributes["harness.model.duration_ms"] == 42
    assert attributes["harness.model.turns"] == 3
    assert attributes["harness.model.stop_reason"] == "end_turn"
    assert attributes["langfuse.observation.level"] == "DEFAULT"
    assert attributes["langfuse.observation.status_message"] == "模型处理完成"
    # The SDK permission mode is a fact only this runtime has.
    assert attributes["harness.model.permission_mode"] in {"auto", "dontAsk"}
    assert attributes["langfuse.observation.output"] == "完成"


@pytest.mark.asyncio
async def test_an_sdk_error_keeps_its_cost_and_is_marked_failed(
    span_recorder: SpanRecorder,
) -> None:
    """A failed call is still a call, and its usage is what explains the failure."""

    result = ResultMessage(
        subtype="error_max_turns",
        duration_ms=10,
        duration_api_ms=8,
        is_error=True,
        num_turns=5,
        session_id="sdk-session",
        usage={"input_tokens": 9},
        result="",
    )
    runtime = _runtime(span_recorder)

    drained: list[object] = []
    with pytest.raises(Exception):  # noqa: B017 - the runtime converts it, we only need it raised
        async for message in runtime._model_messages(  # pyright: ignore[reportPrivateUsage]
            _messages(result), run_id="run-1", route=_route(), prompt="你好"
        ):
            drained.append(message)

    attributes = span_recorder.attributes(MODEL_SPAN_NAME)
    assert attributes["langfuse.observation.level"] == "ERROR"
    assert attributes["langfuse.observation.status_message"] == "error_max_turns"
    assert attributes["gen_ai.usage.input_tokens"] == 9
    assert cast(object, drained) == [result]
