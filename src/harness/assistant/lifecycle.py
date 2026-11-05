"""Pure lifecycle decisions, shared by Web, workers and channel adapters."""

from datetime import datetime, timedelta

from harness.assistant.models import ObjectiveAction, ObjectiveSchedule, ObjectiveStatus
from harness.core.errors import ConflictError

_TRANSITIONS = {
    (ObjectiveStatus.ACTIVE, ObjectiveAction.PAUSE): ObjectiveStatus.PAUSED,
    (ObjectiveStatus.PAUSED, ObjectiveAction.RESUME): ObjectiveStatus.ACTIVE,
    (ObjectiveStatus.ACTIVE, ObjectiveAction.COMPLETE): ObjectiveStatus.COMPLETED,
    (ObjectiveStatus.PAUSED, ObjectiveAction.COMPLETE): ObjectiveStatus.COMPLETED,
    (ObjectiveStatus.ACTIVE, ObjectiveAction.STOP): ObjectiveStatus.CANCELLED,
    (ObjectiveStatus.PAUSED, ObjectiveAction.STOP): ObjectiveStatus.CANCELLED,
}


def transition(status: ObjectiveStatus, action: ObjectiveAction) -> ObjectiveStatus:
    try:
        return _TRANSITIONS[(status, action)]
    except KeyError as error:
        raise ConflictError(f"cannot {action.value} an objective in {status.value}") from error


def next_check(
    schedule: ObjectiveSchedule | None,
    *,
    after: datetime,
    ends_at: datetime | None,
) -> datetime | None:
    """Schedule from now, avoiding a burst of catch-up runs after downtime."""
    if after.tzinfo is None or (ends_at is not None and ends_at.tzinfo is None):
        raise ValueError("schedule timestamps must include a timezone")
    if schedule is None:
        return None
    candidate = after + timedelta(minutes=schedule.interval_minutes)
    return candidate if ends_at is None or candidate <= ends_at else None
