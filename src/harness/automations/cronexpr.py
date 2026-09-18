"""Minimal five-field cron expression evaluation for automation schedules.

Supports the subset the automation UI emits and power users may paste:
``*``, lists (``a,b``), ranges (``a-b``), and step values (``*/n``, ``a-b/n``)
over minute, hour, day-of-month, month, and day-of-week (0=Sunday, 6=Saturday,
7 is accepted as Sunday). When both day-of-month and day-of-week are
restricted, a day matches either - the standard Vixie cron behaviour.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from harness.core.errors import ConflictError

_FIELD_RANGES = {
    0: (0, 59),  # minute
    1: (0, 23),  # hour
    2: (1, 31),  # day of month
    3: (1, 12),  # month
    4: (0, 7),  # day of week (0 and 7 are Sunday)
}

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_WEEKDAY_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}


def _parse_atom(atom: str, field: int) -> set[int]:
    low, high = _FIELD_RANGES[field]
    step = 1
    if "/" in atom:
        atom, step_text = atom.split("/", 1)
        if not step_text.isdigit() or int(step_text) < 1:
            raise ConflictError(f"automation cron step must be a positive integer: {atom}")
        step = int(step_text)
    if atom == "*":
        start, end = low, high
    elif "-" in atom:
        start_text, end_text = atom.split("-", 1)
        start, end = _parse_value(start_text, field), _parse_value(end_text, field)
        if start > end:
            raise ConflictError(f"automation cron range is inverted: {atom}")
    else:
        start = end = _parse_value(atom, field)
    values = {value for value in range(start, end + 1, step) if low <= value <= high}
    if field == 4:
        # 7 is an alias for Sunday; map it back onto 0.
        if high == 7 and (7 in values or end == 7):
            values.add(0)
        values.discard(7)
    if not values:
        raise ConflictError(f"automation cron field has no valid values: {atom}")
    return values


def _parse_value(text: str, field: int) -> int:
    normalized = text.strip().lower()
    names = _WEEKDAY_NAMES if field == 4 else _MONTH_NAMES
    if normalized in names:
        return names[normalized]
    if not normalized.isdigit():
        raise ConflictError(f"automation cron value is not a number: {text}")
    return int(normalized)


def parse_cron(expression: str) -> tuple[set[int], ...]:
    fields = expression.split()
    if len(fields) != 5:
        raise ConflictError("automation cron expression must have exactly 5 fields")
    return tuple(_parse_atom(atom, index) for index, atom in enumerate(fields))


def _day_matches(days: set[int], weekdays: set[int], day: int, weekday: int) -> bool:
    dom_restricted = days != set(range(1, 32))
    dow_restricted = weekdays != set(range(8))
    if dom_restricted and dow_restricted:
        return day in days or weekday in weekdays
    if dom_restricted:
        return day in days
    if dow_restricted:
        return weekday in weekdays
    return True


def next_fire_after(
    expression: str, after: datetime, timezone: str = "UTC"
) -> datetime:
    """Return the next fire time strictly after ``after`` in ``timezone``.

    ``timezone`` is an IANA name (e.g. ``Asia/Shanghai``); the schedule always
    carries one so host-local clock ambiguity never leaks into the schedule.
    """

    try:
        zone = ZoneInfo(timezone)
    except Exception as error:
        raise ConflictError(f"automation schedule timezone is invalid: {timezone}") from error
    minute_values, hour_values, day_values, month_values, weekday_values = parse_cron(expression)
    local = after.astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)

    def days_in(year: int, month: int) -> int:
        return calendar.monthrange(year, month)[1]

    def cron_weekday(day: datetime) -> int:
        # cron numbers Sunday=0..Saturday=6; Python numbers Monday=0..Sunday=6.
        return (day.weekday() + 1) % 7

    for _month_scan in range(4 * 12):  # search at most four years ahead
        if local.month not in month_values:
            # Jump to the first day of the next candidate month.
            year, month = local.year, local.month + 1
            if month > 12:
                year, month = year + 1, 1
            local = local.replace(year=year, month=month, day=1, hour=0, minute=0)
            continue
        if local.day > days_in(local.year, local.month) or not _day_matches(
            day_values, weekday_values, local.day, cron_weekday(local)
        ):
            local = (local + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if local.hour not in hour_values:
            hour = next((value for value in sorted(hour_values) if value > local.hour), None)
            if hour is None:
                local = (local + timedelta(days=1)).replace(hour=0, minute=0)
            else:
                local = local.replace(hour=hour, minute=0)
            continue
        if local.minute not in minute_values:
            minute = next(
                (value for value in sorted(minute_values) if value > local.minute), None
            )
            if minute is None:
                local = (local + timedelta(hours=1)).replace(minute=0)
            else:
                local = local.replace(minute=minute)
            continue
        return local
    raise ConflictError("automation cron expression never fires: " + expression)
