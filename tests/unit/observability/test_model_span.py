"""What a model observation reports, and what it deliberately leaves out.

These names are a contract with more than one consumer. `langfuse.observation.type`
is what decides whether the backend renders the observation as a generation at
all, and the token counters are read under three different names by three
different readers. That vocabulary used to be written out inside the Claude
runtime, which is exactly why a DeepAgents Run produced no generation: the
attribute set existed, but only one runtime knew it. These tests keep the one
home honest.
"""

from __future__ import annotations

import json
from typing import Any

from opentelemetry.trace import StatusCode

from harness.core.manifest import AgentManifestSnapshot
from harness.observability.model_span import (
    MODEL_SPAN_NAME,
    ModelRunFacts,
    model_observation,
    model_run_facts,
)
from tests.conftest import SpanRecorder


def _snapshot() -> AgentManifestSnapshot:
    return AgentManifestSnapshot.model_validate(
        {
            "manifest": {
                "apiVersion": "harness/v1alpha1",
                "kind": "Agent",
                "metadata": {"name": "score-agent", "version": "1.2.3"},
                "spec": {
                    "runtime": "deepagents",
                    "model": {"route": "default", "model": "gateway-model"},
                    "prompt": {"system": "prompts/system.md"},
                    "tools": [{"builtin": "Read"}],
                    "permissions": {"policy": "local-standard"},
                    "limits": {},
                },
            },
            "system_prompt": "You are a test.",
            "python_tool_snapshots": [],
            "content_hash": "a" * 64,
        }
    )


def _facts(**overrides: Any) -> ModelRunFacts:
    values: dict[str, Any] = {
        "run_id": "run-1",
        "route_id": "route-1",
        "model": "deepseek-flash",
        "provider": "new-api",
    }
    values.update(overrides)
    return model_run_facts(_snapshot(), **values)


def test_the_attributes_are_derived_from_the_published_agent() -> None:
    """One derivation, so two runtimes cannot describe one Agent differently."""

    attributes = _facts().attributes()

    assert attributes["langfuse.observation.type"] == "generation"
    assert attributes["langfuse.observation.model.name"] == "deepseek-flash"
    assert attributes["langfuse.observation.metadata.route_id"] == "route-1"
    assert attributes["langfuse.version"] == "1.2.3"
    assert attributes["gen_ai.request.model"] == "deepseek-flash"
    assert attributes["gen_ai.provider.name"] == "new-api"
    assert attributes["harness.model.route"] == "route-1"
    assert attributes["harness.policy.profile"] == "local-standard"
    assert attributes["agent.name"] == "score-agent"
    assert attributes["agent.content_hash"] == "a" * 64
    assert attributes["run.id"] == "run-1"


def test_a_fact_this_runtime_does_not_have_is_left_out() -> None:
    """Not reported reads as "not reported", which is not the same as a value."""

    attributes = _facts().attributes()

    assert "harness.model.permission_mode" not in attributes
    assert "agent.package_hash" not in attributes
    with_them = _facts(permission_mode="auto", package_hash="b" * 64).attributes()
    assert with_them["harness.model.permission_mode"] == "auto"
    assert with_them["agent.package_hash"] == "b" * 64


def test_a_result_names_usage_for_every_reader(span_recorder: SpanRecorder) -> None:
    with model_observation(
        span_recorder.observability, _facts(), input_value="你好"
    ) as observation:
        observation.record_result(
            usage={"input_tokens": 11, "output_tokens": 7},
            cost_usd=0.5,
            duration_ms=120,
            api_duration_ms=90,
            turns=2,
            stop_reason="end_turn",
            output="你好，我是助手",
        )

    attributes = span_recorder.attributes(MODEL_SPAN_NAME)
    assert attributes["gen_ai.usage.input_tokens"] == 11
    assert attributes["gen_ai.usage.output_tokens"] == 7
    assert json.loads(str(attributes["langfuse.observation.usage_details"])) == {
        "input": 11,
        "output": 7,
    }
    assert json.loads(str(attributes["langfuse.observation.cost_details"])) == {"total": 0.5}
    assert attributes["langfuse.observation.level"] == "DEFAULT"
    assert attributes["langfuse.observation.status_message"] == "模型处理完成"
    assert attributes["harness.model.duration_ms"] == 120
    assert attributes["harness.model.is_error"] is False
    # Content capture is on, so the answer is readable without opening the span,
    # and the trace carries it too.
    assert attributes["langfuse.observation.output"] == "你好，我是助手"
    assert attributes["langfuse.trace.output"] == "你好，我是助手"


def test_a_counter_a_runtime_never_saw_is_absent_rather_than_zero(
    span_recorder: SpanRecorder,
) -> None:
    with model_observation(span_recorder.observability, _facts()) as observation:
        observation.record_result(usage={"input_tokens": 4})

    attributes = span_recorder.attributes(MODEL_SPAN_NAME)
    assert attributes["gen_ai.usage.input_tokens"] == 4
    assert "gen_ai.usage.output_tokens" not in attributes
    assert json.loads(str(attributes["langfuse.observation.usage_details"])) == {"input": 4}
    # No cost was reported, so no cost is claimed.
    assert "langfuse.observation.cost_details" not in attributes
    assert "harness.model.cost_usd" not in attributes


def test_a_failure_is_reported_as_one(span_recorder: SpanRecorder) -> None:
    with model_observation(span_recorder.observability, _facts()) as observation:
        observation.record_result(is_error=True, status_message="error_max_turns")
        observation.mark_error("error_max_turns")

    attributes = span_recorder.attributes(MODEL_SPAN_NAME)
    assert attributes["langfuse.observation.level"] == "ERROR"
    assert attributes["langfuse.observation.status_message"] == "error_max_turns"
    assert attributes["harness.model.is_error"] is True
    assert span_recorder.span(MODEL_SPAN_NAME).status.status_code is StatusCode.ERROR


def test_an_exception_escaping_the_runtime_marks_the_model_span(
    span_recorder: SpanRecorder,
) -> None:
    """The span is opened with `set_status_on_exception=False`, so a failure that
    escapes the runtime is recorded here or not at all."""

    try:
        with model_observation(span_recorder.observability, _facts()) as observation:
            observation.record_result(usage={"output_tokens": 3})
            raise RuntimeError("sandbox died")
    except RuntimeError:
        pass

    attributes = span_recorder.attributes(MODEL_SPAN_NAME)
    assert attributes["gen_ai.usage.output_tokens"] == 3
    assert span_recorder.span(MODEL_SPAN_NAME).status.status_code is StatusCode.ERROR
