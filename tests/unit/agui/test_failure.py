from datetime import UTC, datetime

import pytest

from harness.agui.activity import build_run_activity
from harness.agui.mapper import map_harness_event
from harness.core.events import RunEvent


@pytest.mark.parametrize(
    "payload",
    [
        {"error_code": "runtime_message_too_large"},
        {"error_type": "CLIJSONDecodeError"},
        {},
    ],
)
def test_live_and_historical_errors_share_actionable_copy(payload: dict[str, object]) -> None:
    event = RunEvent(
        event_id="failure",
        run_id="run",
        session_id="session",
        tenant_id="tenant",
        sequence=1,
        type="run.failed",
        timestamp=datetime.now(UTC),
        payload=payload,
    )
    live = next(item for item in map_harness_event(event) if item.type == "RUN_ERROR")
    activity = build_run_activity([event])
    assert activity is not None
    assert live.model_dump()["message"] == activity["items"][0]["summary"]
    assert "请" in live.model_dump()["message"]
