"""Audit timeout recovery against real PostgreSQL event/outbox repositories."""

import pytest

from tests.integration.storage.test_assistant_group_delegate import setup_pg
from tests.unit.assistant.test_group_audit_bounds import (
    check_cancelled_drain,
    check_hanging_failure,
    check_hanging_success,
    check_slow_head,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["persist", "publish", "all_publish"])
async def test_group_delegate_pg_hanging_audit_returns_and_recovers(database, failure):
    await check_hanging_success(await setup_pg(database), failure)


@pytest.mark.asyncio
async def test_group_delegate_pg_hanging_failure_audit_returns_failed_and_recovers(database):
    await check_hanging_failure(await setup_pg(database))


@pytest.mark.asyncio
async def test_group_notice_pg_slow_head_does_not_starve_batch(database):
    await check_slow_head(await setup_pg(database))


@pytest.mark.asyncio
async def test_group_notice_pg_cancelled_batch_joins_publishers_and_recovers(database):
    await check_cancelled_drain(await setup_pg(database))
