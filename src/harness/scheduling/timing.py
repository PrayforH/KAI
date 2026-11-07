"""The timing algebra shared by objectives, automations, and triggers.

A timing rule is a value: when the rule fires and, for wall-clock rules, which
zone anchors it. This module reads no database, session, run, or model. Each
domain converts its persisted schedule into one of the three kinds below and
asks for the next occurrence here, so minute rounding, DST behaviour, and
end-of-window rules cannot drift between surfaces.

``ONCE``   — a committed absolute instant.
``EVERY``  — a fixed elapsed interval in seconds, anchored at the call time.
``CRON``   — a five-field wall-clock expression in an explicit IANA zone.

Every occurrence is strictly after the supplied instant. ``not_after`` is an
inclusive upper bound: a candidate beyond it yields ``None``, which callers
materialize as their own "ended" or "not scheduled" state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from harness.core.errors import ConflictError
from harness.scheduling.cron import next_fire_after, parse_cron


class TimingKind(StrEnum):
    ONCE = "once"
    EVERY = "every"
    CRON = "cron"


@dataclass(frozen=True, slots=True)
class Timing:
    """One authoritative timing rule. Build it through the class methods."""

    kind: TimingKind
    at: datetime | None = None
    seconds: int | None = None
    expression: str | None = None
    timezone: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        payloads = (self.at, self.seconds, self.expression)
        if sum(value is not None for value in payloads) != 1:
            raise ConflictError("timing must carry exactly one of at, seconds, or expression")
        if self.kind is TimingKind.ONCE and self.at is None:
            raise ConflictError("once timing requires at")
        if self.kind is TimingKind.EVERY and self.seconds is None:
            raise ConflictError("every timing requires seconds")
        if self.kind is TimingKind.CRON and self.expression is None:
            raise ConflictError("cron timing requires expression")
        if self.at is not None and self.at.tzinfo is None:
            raise ValueError("timing instants must include a timezone")
        if self.seconds is not None and self.seconds < 1:
            raise ConflictError("every timing requires a positive number of seconds")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ConflictError(f"timing timezone is invalid: {self.timezone}") from error
        if self.expression is not None:
            parse_cron(self.expression)

    @classmethod
    def once(cls, at: datetime) -> Timing:
        """A single committed instant. Past instants are the caller's concern."""
        return cls(TimingKind.ONCE, at=at)

    @classmethod
    def every(cls, seconds: int) -> Timing:
        """A fixed elapsed interval; each occurrence is measured from the prior call."""
        return cls(TimingKind.EVERY, seconds=seconds)

    @classmethod
    def cron(cls, expression: str, timezone: str = "Asia/Shanghai") -> Timing:
        """A wall-clock rule; the zone is part of the value, never ambient."""
        return cls(TimingKind.CRON, expression=expression, timezone=timezone)

    def next_occurrence(
        self, after: datetime, *, not_after: datetime | None = None
    ) -> datetime | None:
        """Return the first occurrence strictly after ``after``, or ``None``.

        ``not_after`` includes the bound itself; callers keep their own
        terminal-state vocabulary (completed, expired, ended).
        """
        if after.tzinfo is None:
            raise ValueError("timing instants must include a timezone")
        if not_after is not None and not_after.tzinfo is None:
            raise ValueError("timing instants must include a timezone")
        match self.kind:
            case TimingKind.ONCE:
                assert self.at is not None
                candidate: datetime | None = self.at if self.at > after else None
            case TimingKind.EVERY:
                assert self.seconds is not None
                candidate = after + timedelta(seconds=self.seconds)
            case TimingKind.CRON:
                assert self.expression is not None
                candidate = next_fire_after(self.expression, after, self.timezone)
        if candidate is None:
            return None
        if not_after is not None and candidate > not_after:
            return None
        return candidate
