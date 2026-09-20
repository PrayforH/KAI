import pytest

from harness.evals.diagnostics import usage_from_events
from harness.evals.runner import RecordedRun, evaluate_recorded_run
from harness.evals.suite import EvalCase, EvalExpectation


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf")])
def test_invalid_cost_is_missing_evidence(value: object) -> None:
    usage = usage_from_events(({"type": "runtime.result", "payload": {"total_cost_usd": value}},))
    assert usage.cost_usd is None


def test_output_json_fragments_and_ordered_tools_and_usage() -> None:
    case = EvalCase(
        id="structured",
        tags=("happy",),
        prompt="return JSON",
        expect=EvalExpectation(
            outputJsonEquals={"ok": True},
            outputContains=('{"ok":true}',),
            toolCallSequence=("Read", "Write"),
            maxToolCalls=2,
            maxCostUsd=0.01,
            maxModelTokens=50,
        ),
    )
    events: tuple[dict[str, object], ...] = (
        {"type": "tool.request", "payload": {"name": "Read"}},
        {"type": "tool.request", "payload": {"name": "Write"}},
        {"type": "message.delta", "payload": {"text": '{"ok":'}},
        {"type": "message.delta", "payload": {"text": "true}"}},
        {"type": "message.completed", "payload": {"role": "assistant"}},
        {
            "type": "runtime.result",
            "payload": {
                "total_cost_usd": 0.005,
                "usage": {"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 20},
            },
        },
    )
    scored = evaluate_recorded_run(case, RecordedRun("run", "succeeded", 1, events))
    assert scored.passed and scored.usage.model_tokens == 33
    wrong = evaluate_recorded_run(
        case, RecordedRun("run", "succeeded", 1, (*events[1:2], events[0], *events[2:-1]))
    )
    assert not wrong.passed
    assert {f.code for f in wrong.failure_details} == {"tool_contract", "missing_evidence"}
