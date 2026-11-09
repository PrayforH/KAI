"""Pure lifecycle decisions, shared by Web, workers and channel adapters."""

from datetime import datetime

from harness.assistant.models import ObjectiveAction, ObjectiveSchedule, ObjectiveStatus
from harness.core.errors import ConflictError
from harness.scheduling import TimingKind

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
    resume: bool = False,
) -> datetime | None:
    """Schedule from now, avoiding a burst of catch-up runs after downtime."""
    if after.tzinfo is None or (ends_at is not None and ends_at.tzinfo is None):
        raise ValueError("schedule timestamps must include a timezone")
    if schedule is None:
        return None
    if resume and schedule.kind is TimingKind.ONCE and schedule.at is not None:
        if ends_at is None or max(after, schedule.at) <= ends_at:
            return schedule.at
    return schedule.timing().next_occurrence(after, not_after=ends_at)


def validate_new_schedule(
    schedule: ObjectiveSchedule | None, *, now: datetime, ends_at: datetime | None
) -> None:
    if schedule is not None and schedule.kind is TimingKind.ONCE:
        if next_check(schedule, after=now, ends_at=ends_at) is None:
            raise ConflictError("一次性职责的执行时间须在未来，且不能晚于结束时间。")
