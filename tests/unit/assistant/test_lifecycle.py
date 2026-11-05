from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from harness.assistant.lifecycle import next_check, transition
from harness.assistant.models import (
    ObjectiveAction,
    ObjectiveBrief,
    ObjectiveSchedule,
    ObjectiveStatus,
)
from harness.core.errors import ConflictError


def test_stopping_and_completing_are_terminal() -> None:
    for status in (ObjectiveStatus.ACTIVE, ObjectiveStatus.PAUSED):
        assert transition(status, ObjectiveAction.STOP) is ObjectiveStatus.CANCELLED
        assert transition(status, ObjectiveAction.COMPLETE) is ObjectiveStatus.COMPLETED
    for status in (ObjectiveStatus.CANCELLED, ObjectiveStatus.COMPLETED):
        for action in ObjectiveAction:
            with pytest.raises(ConflictError):
                transition(status, action)


def test_resume_only_applies_to_a_paused_responsibility() -> None:
    assert transition(ObjectiveStatus.ACTIVE, ObjectiveAction.PAUSE) is ObjectiveStatus.PAUSED
    assert transition(ObjectiveStatus.PAUSED, ObjectiveAction.RESUME) is ObjectiveStatus.ACTIVE
    with pytest.raises(ConflictError):
        transition(ObjectiveStatus.ACTIVE, ObjectiveAction.RESUME)


def test_schedule_never_fires_after_end_or_catches_up_missed_slots() -> None:
    now = datetime(2026, 10, 22, 12, tzinfo=UTC)
    schedule = ObjectiveSchedule(intervalMinutes=60)
    assert next_check(schedule, after=now, ends_at=None) == now + timedelta(hours=1)
    assert next_check(schedule, after=now, ends_at=now + timedelta(minutes=30)) is None
    assert next_check(schedule, after=now, ends_at=now - timedelta(days=3)) is None
    assert next_check(None, after=now, ends_at=None) is None


def test_schedule_rejects_ambiguous_timestamps_and_invalid_timezones() -> None:
    with pytest.raises(ValueError, match="timezone"):
        next_check(ObjectiveSchedule(intervalMinutes=60), after=datetime(2026, 1, 1), ends_at=None)
    with pytest.raises(ValidationError):
        ObjectiveSchedule(intervalMinutes=60, timezone="Not/AZone")
    with pytest.raises(ValidationError):
        ObjectiveSchedule(intervalMinutes=1)


def test_user_input_cannot_inject_identity_status_or_blank_instructions() -> None:
    valid = dict(
        title="整改跟进", instructions="每周检查新材料", agentName="audit", agentVersion="1"
    )
    for extra in ({"userId": "someone-else"}, {"status": "completed"}, {"instructions": "  "}):
        with pytest.raises(ValidationError):
            ObjectiveBrief.model_validate(valid | extra)
    with pytest.raises(ValidationError):
        ObjectiveBrief.model_validate(valid | {"endsAt": "2026-11-01T00:00:00"})
