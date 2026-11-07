"""Tests for the single schedule read model."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from harness.scheduling.catalog import (
    ScheduleCatalog,
    ScheduleSource,
    from_automation,
    from_objective,
)


def _objective(**overrides):
    values = {
        "objective_id": "obj_1",
        "title": "每日简报",
        "status": SimpleNamespace(value="active"),
        "schedule": SimpleNamespace(interval_minutes=60, timezone="Asia/Shanghai"),
        "specialist_id": "lead-agent",
        "agent_name": "lead-agent",
        "next_check_at": datetime(2026, 11, 7, 1, 0, tzinfo=UTC),
        "created_at": datetime(2026, 11, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 11, 6, tzinfo=UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _automation(**overrides):
    values = {
        "task_id": "task_1",
        "name": "周报汇总",
        "status": SimpleNamespace(value="active"),
        "schedule": SimpleNamespace(
            type=SimpleNamespace(value="cron"),
            at=None,
            cron="0 9 * * 1",
            timezone="Asia/Shanghai",
        ),
        "next_run_at": datetime(2026, 11, 9, 1, 0, tzinfo=UTC),
        "last_run_at": None,
        "created_at": datetime(2026, 11, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 11, 6, tzinfo=UTC),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_objective_interval_becomes_seconds() -> None:
    task = from_objective(_objective())
    assert task is not None
    assert task.source is ScheduleSource.OBJECTIVE
    assert task.timing.kind.value == "every"
    assert task.timing.seconds == 3600
    assert task.timing.timezone == "Asia/Shanghai"
    assert task.specialist_id == "lead-agent"


def test_manual_objective_is_not_a_schedule() -> None:
    assert from_objective(_objective(schedule=None)) is None


def test_automation_cron_keeps_expression_and_zone() -> None:
    task = from_automation(_automation())
    assert task.source is ScheduleSource.AUTOMATION
    assert task.timing.kind.value == "cron"
    assert task.timing.expression == "0 9 * * 1"
    assert task.timing.timezone == "Asia/Shanghai"


class _Objectives:
    def __init__(self, items):
        self.items = items

    async def list(self, tenant_id: str, user_id: str, *, limit: int = 100):
        return self.items


class _Automations:
    def __init__(self, items):
        self.items = items

    async def list(self, tenant_id: str, user_id: str):
        return self.items


@pytest.mark.asyncio
async def test_specialist_filter_excludes_automations() -> None:
    catalog = ScheduleCatalog(_Objectives([_objective()]), _Automations([_automation()]))
    tasks = await catalog.list("t", "u", specialist_id="lead-agent")
    assert [task.source for task in tasks] == [ScheduleSource.OBJECTIVE]


@pytest.mark.asyncio
async def test_source_filter_returns_one_store() -> None:
    catalog = ScheduleCatalog(_Objectives([_objective()]), _Automations([_automation()]))
    tasks = await catalog.list("t", "u", source=ScheduleSource.AUTOMATION)
    assert [task.schedule_id for task in tasks] == ["task_1"]


@pytest.mark.asyncio
async def test_catalog_orders_by_next_run_with_unscheduled_last() -> None:
    later = _automation(
        task_id="task_later", next_run_at=datetime(2026, 11, 20, tzinfo=UTC)
    )
    never = _objective(
        objective_id="obj_never", next_check_at=None
    )
    catalog = ScheduleCatalog(
        _Objectives([never]), _Automations([_automation(), later])
    )
    tasks = await catalog.list("t", "u")
    assert [task.schedule_id for task in tasks] == ["task_1", "task_later", "obj_never"]
