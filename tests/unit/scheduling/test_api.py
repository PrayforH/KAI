"""Tests for the schedule catalog route's permission split."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from harness.api.dependencies import Identity
from harness.assistant.models import ObjectiveSchedule
from harness.scheduling.api import list_schedules
from harness.scheduling.catalog import ScheduleCatalog, ScheduleSource

NOW = datetime(2026, 11, 6, tzinfo=UTC)


def _objective():
    return SimpleNamespace(
        objective_id="obj_1",
        title="简报",
        status=SimpleNamespace(value="active"),
        revision=1,
        schedule=ObjectiveSchedule(intervalMinutes=60, timezone="UTC"),
        specialist_id="lead-agent",
        agent_name="lead-agent",
        next_check_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def _automation():
    return SimpleNamespace(
        task_id="task_1",
        name="周报",
        status=SimpleNamespace(value="active"),
        revision=1,
        schedule=SimpleNamespace(
            type=SimpleNamespace(value="cron"),
            at=None,
            cron="0 9 * * 1",
            timezone="Asia/Shanghai",
        ),
        next_run_at=NOW,
        last_run_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


class _Objectives:
    async def list(self, tenant_id: str, user_id: str, *, limit: int = 100):
        return [_objective()]


class _Automations:
    async def list(self, tenant_id: str, user_id: str):
        return [_automation()]


def _tasks_only() -> Identity:
    return Identity(
        tenant_id="t",
        user_id="u",
        roles=frozenset(),
        permissions=frozenset({"tasks:read"}),
    )


@pytest.mark.asyncio
async def test_tasks_only_credential_never_sees_automations() -> None:
    catalog = ScheduleCatalog(_Objectives(), _Automations())
    tasks = await list_schedules(
        identity=_tasks_only(), catalog=catalog, response=Response(),
        specialist_id=None, source=None,
    )
    assert [task.source for task in tasks] == [ScheduleSource.OBJECTIVE]


@pytest.mark.asyncio
async def test_explicit_automation_source_requires_studio_read() -> None:
    catalog = ScheduleCatalog(_Objectives(), _Automations())
    with pytest.raises(HTTPException) as raised:
        await list_schedules(
            identity=_tasks_only(),
            catalog=catalog,
            response=Response(),
            specialist_id=None,
            source=ScheduleSource.AUTOMATION,
        )
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_sees_both_stores() -> None:
    catalog = ScheduleCatalog(_Objectives(), _Automations())
    identity = Identity(tenant_id="t", user_id="u", roles=frozenset({"owner"}))
    tasks = await list_schedules(
        identity=identity, catalog=catalog, response=Response(), specialist_id=None, source=None
    )
    assert {task.source for task in tasks} == {
        ScheduleSource.OBJECTIVE,
        ScheduleSource.AUTOMATION,
    }
