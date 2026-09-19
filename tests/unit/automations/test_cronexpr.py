"""Tests for the automation cron evaluator."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from harness.automations.cronexpr import next_fire_after, parse_cron
from harness.core.errors import ConflictError

ZONE = ZoneInfo("Asia/Shanghai")


def test_parse_cron_rejects_wrong_field_count() -> None:
    with pytest.raises(ConflictError):
        parse_cron("* * *")


def test_parse_cron_rejects_out_of_range_values() -> None:
    with pytest.raises(ConflictError):
        parse_cron("60 * * * *")
    with pytest.raises(ConflictError):
        parse_cron("* 25 * * *")


def test_daily_expression_fires_at_requested_time() -> None:
    after = datetime(2026, 9, 19, 10, 0, tzinfo=ZONE)
    assert (
        next_fire_after("0 8 * * *", after, "Asia/Shanghai")
        == datetime(2026, 9, 20, 8, 0, tzinfo=ZONE)
    )


def test_weekday_step_expression() -> None:
    # 2026-09-19 is a Saturday; the next matching weekday slot is Monday at
    # 00:00 (even hour in the */2 set).
    after = datetime(2026, 9, 19, 9, 0, tzinfo=ZONE)
    assert (
        next_fire_after("0 */2 * * 1-5", after, "Asia/Shanghai")
        == datetime(2026, 9, 21, 0, 0, tzinfo=ZONE)
    )


def test_weekly_expression_with_weekday_name() -> None:
    after = datetime(2026, 9, 19, 10, 0, tzinfo=ZONE)  # Saturday
    assert (
        next_fire_after("0 18 * * fri", after, "Asia/Shanghai")
        == datetime(2026, 9, 25, 18, 0, tzinfo=ZONE)
    )


def test_day_of_month_or_week_union() -> None:
    # Both dom and dow restricted: a day matching either one fires (Vixie cron).
    after = datetime(2026, 9, 19, 10, 0, tzinfo=ZONE)
    # The 25th is a Friday, matching dow=5 even though dom=1 misses it.
    assert (
        next_fire_after("0 0 1 * 5", after, "Asia/Shanghai")
        == datetime(2026, 9, 25, 0, 0, tzinfo=ZONE)
    )


def test_sunday_alias_seven() -> None:
    after = datetime(2026, 9, 21, 10, 0, tzinfo=ZONE)  # Monday
    assert (
        next_fire_after("0 12 * * 7", after, "Asia/Shanghai")
        == datetime(2026, 9, 27, 12, 0, tzinfo=ZONE)
    )


def test_result_is_strictly_after_reference() -> None:
    after = datetime(2026, 9, 19, 8, 0, tzinfo=ZONE)
    fired = next_fire_after("0 8 * * *", after)
    assert fired > after
    assert (
        next_fire_after("*/30 * * * *", after, "Asia/Shanghai")
        == datetime(2026, 9, 19, 8, 30, tzinfo=ZONE)
    )


def test_utc_reference_is_supported() -> None:
    fired = next_fire_after("0 8 * * *", datetime(2026, 9, 19, 8, 0, tzinfo=UTC))
    assert fired == datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
