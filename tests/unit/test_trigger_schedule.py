from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from harness.triggers.models import TriggerSchedule


def test_cron_uses_agent_schedule_timezone_and_advances_strictly():
    schedule = TriggerSchedule(cron="0 9 * * 1-5", timezone="Asia/Shanghai", prompt="日报")
    friday = datetime(2026, 9, 18, 1, tzinfo=UTC)
    assert schedule.next_after(friday) == datetime(2026, 9, 21, 1, tzinfo=UTC)
    assert schedule.next_after(friday - timedelta(minutes=1)) == friday


def test_legacy_interval_schedule_still_round_trips():
    schedule = TriggerSchedule(intervalSeconds=3600, prompt="巡检")
    now = datetime(2026, 9, 21, tzinfo=UTC)
    assert schedule.next_after(now) == now + timedelta(hours=1)
    assert TriggerSchedule.model_validate_json(schedule.model_dump_json()) == schedule


@pytest.mark.parametrize("fields", [
    {}, {"cron": "bad"}, {"cron": "0 9 * * *", "intervalSeconds": 60},
    {"cron": "0 9 * * *", "timezone": "Invalid/Zone"}, {"intervalSeconds": 0},
])
def test_invalid_schedules_are_rejected_at_request_boundary(fields):
    with pytest.raises(ValidationError):
        TriggerSchedule(prompt="巡检", **fields)
