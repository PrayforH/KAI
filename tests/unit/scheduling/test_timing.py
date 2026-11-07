"""Tests for the timing value shared by every scheduled surface."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from harness.core.errors import ConflictError
from harness.scheduling import Timing, TimingKind

ZONE = ZoneInfo("Asia/Shanghai")


def test_once_returns_the_committed_instant() -> None:
    at = datetime(2026, 11, 7, 9, 30, tzinfo=ZONE)
    timing = Timing.once(at)
    assert timing.kind is TimingKind.ONCE
    assert timing.next_occurrence(at - timedelta(minutes=1)) == at


def test_once_in_the_past_has_no_occurrence() -> None:
    at = datetime(2026, 11, 7, 9, 30, tzinfo=ZONE)
    assert Timing.once(at).next_occurrence(at) is None


def test_every_measures_from_the_supplied_instant() -> None:
    after = datetime(2026, 11, 7, 8, 0, tzinfo=UTC)
    assert Timing.every(90 * 60).next_occurrence(after) == after + timedelta(minutes=90)


def test_cron_anchors_the_expression_in_its_zone() -> None:
    after = datetime(2026, 11, 7, 0, 0, tzinfo=UTC)
    timing = Timing.cron("0 8 * * *", "Asia/Shanghai")
    assert timing.next_occurrence(after) == datetime(2026, 11, 8, 8, 0, tzinfo=ZONE)


def test_not_after_rejects_candidates_beyond_the_bound() -> None:
    after = datetime(2026, 11, 7, 8, 0, tzinfo=UTC)
    assert (
        Timing.every(30 * 60).next_occurrence(
            after, not_after=after + timedelta(minutes=29)
        )
        is None
    )
    assert Timing.every(30 * 60).next_occurrence(
        after, not_after=after + timedelta(minutes=30)
    ) == after + timedelta(minutes=30)


def test_naive_instants_are_rejected() -> None:
    with pytest.raises(ValueError):
        Timing.once(datetime(2026, 11, 7, 9, 30))
    with pytest.raises(ValueError):
        Timing.every(60).next_occurrence(datetime(2026, 11, 7, 9, 30))


def test_exactly_one_payload_is_required() -> None:
    with pytest.raises(ConflictError):
        Timing(TimingKind.ONCE)
    with pytest.raises(ConflictError):
        Timing(TimingKind.EVERY, seconds=60, expression="* * * * *")


def test_every_needs_a_positive_interval() -> None:
    with pytest.raises(ConflictError):
        Timing.every(0)


def test_cron_validates_expression_and_timezone() -> None:
    with pytest.raises(ConflictError):
        Timing.cron("* * *")
    with pytest.raises(ConflictError):
        Timing.cron("0 8 * * *", "Not/AZone")
    with pytest.raises(ConflictError):
        Timing(TimingKind.EVERY, seconds=60, timezone="Not/AZone")
