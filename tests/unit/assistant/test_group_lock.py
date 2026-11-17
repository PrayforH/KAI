"""Admission retry releases connections and never replays admitted operations."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

import harness.assistant.repository as repository_module
from harness.assistant.repository import PostgresAssistantRepository
from harness.core.errors import ConflictError


def lock_error(sqlstate="55P03"):
    class DatabaseError(Exception):
        pass

    original = DatabaseError("lock unavailable")
    original.sqlstate = sqlstate
    return DBAPIError("SELECT FOR UPDATE NOWAIT", {}, original)


def repository_with_context(monkeypatch):
    repository = PostgresAssistantRepository(async_sessionmaker())
    session = SimpleNamespace()
    trace = []

    @asynccontextmanager
    async def context(**kwargs):
        trace.append("open")
        try:
            yield session
        finally:
            trace.append("closed")

    monkeypatch.setattr(repository, "_context", context)
    return repository, session, trace


@pytest.mark.asyncio
async def test_group_guard_releases_connection_before_retry(monkeypatch):
    repository, session, trace = repository_with_context(monkeypatch)
    repository._require_group = AsyncMock(side_effect=[lock_error(), object()])

    async def sleep(delay):
        assert trace == ["open", "closed"]
        assert 0 < delay <= 0.1
        trace.append("wait")

    monkeypatch.setattr(repository_module, "sleep", sleep)
    async with repository.group_dispatch_guard("t", "u", "g") as scoped:
        assert scoped._bound_session is session
        trace.append("body")
    assert trace == ["open", "closed", "wait", "open", "body", "closed"]


@pytest.mark.asyncio
async def test_group_guard_contention_has_bounded_deadline(monkeypatch):
    repository, _, trace = repository_with_context(monkeypatch)
    repository._require_group = AsyncMock(side_effect=lock_error())
    monkeypatch.setattr(repository_module, "monotonic", lambda: next(clock))
    clock = iter([0, 6])
    sleep = AsyncMock()
    monkeypatch.setattr(repository_module, "sleep", sleep)
    with pytest.raises(ConflictError, match="群正在处理其他操作"):
        async with repository.group_dispatch_guard("t", "u", "g"):
            pytest.fail("contended guard admitted")
    assert trace == ["open", "closed"]
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_guard_cancellation_does_not_keep_connection(monkeypatch):
    repository, _, trace = repository_with_context(monkeypatch)
    repository._require_group = AsyncMock(side_effect=lock_error())
    monkeypatch.setattr(repository_module, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        async with repository.group_dispatch_guard("t", "u", "g"):
            pytest.fail("cancelled guard admitted")
    assert trace == ["open", "closed"]
    assert repository._require_group.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [lock_error(), RuntimeError("body failed")])
async def test_group_guard_never_replays_body_failure(monkeypatch, failure):
    repository, _, trace = repository_with_context(monkeypatch)
    repository._require_group = AsyncMock(return_value=object())
    with pytest.raises(type(failure)) as caught:
        async with repository.group_dispatch_guard("t", "u", "g"):
            raise failure
    assert caught.value is failure
    assert trace == ["open", "closed"]
    assert repository._require_group.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bound,sqlstate", [(True, "55P03"), (False, "08006")])
async def test_group_guard_does_not_retry_bound_or_unrelated_database_errors(
    monkeypatch, bound, sqlstate,
):
    repository, session, trace = repository_with_context(monkeypatch)
    if bound:
        repository._bound_session = session
    error = lock_error(sqlstate)
    repository._require_group = AsyncMock(side_effect=error)
    with pytest.raises(DBAPIError) as caught:
        async with repository._group_write_context("t", "u", "g"):
            pytest.fail("failed guard admitted")
    assert caught.value is error
    assert trace == ["open", "closed"]
    assert repository._require_group.await_count == 1


@pytest.mark.asyncio
async def test_group_dispatch_rejects_caller_owned_transaction_before_waiting(monkeypatch):
    repository, session, trace = repository_with_context(monkeypatch)
    repository._bound_session = session
    with pytest.raises(ConflictError, match="独立的准入事务"):
        async with repository.group_dispatch_guard("t", "u", "g"):
            pytest.fail("nested admission")
    assert trace == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_dispatch_waiter_timeout_or_cancel_leaves_owner_and_slot_intact(monkeypatch, cancel):
    repository, _, _ = repository_with_context(monkeypatch)
    repository._require_group = AsyncMock(return_value=object())
    monkeypatch.setattr(repository_module, "GROUP_LOCK_TIMEOUT_SECONDS", 0.05)
    entered = asyncio.Event()

    async def contender():
        entered.set()
        async with repository.group_dispatch_guard("t", "u", "another-group"):
            pytest.fail("concurrent pool admission")

    async with repository.group_dispatch_guard("t", "u", "g"):
        task = asyncio.create_task(contender())
        await entered.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else ConflictError):
            await task
        assert repository._require_group.await_count == 1
    # A failed waiter must not release the owner's slot or poison future use.
    async with repository.group_dispatch_guard("t", "u", "next-group"):
        assert repository._require_group.await_count == 2
