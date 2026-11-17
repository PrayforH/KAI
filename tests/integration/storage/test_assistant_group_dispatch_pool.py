"""Group lock contenders must release shared pool connections before waiting."""

import asyncio
import os
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from harness.adapters.memory import InMemorySessionRepository, InMemoryTaskQueue
from harness.application.runs import RunService
from harness.assistant.models import AssistantGroupMessageAuthor
from harness.assistant.repository import PostgresAssistantRepository
from harness.core.models import RunStatus
from tests.integration.storage.test_assistant_group_delegate import setup_pg
from tests.unit.assistant.test_group_delegate import NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", [2, 15])
@pytest.mark.parametrize("operation", ["dispatch", "retry", "roster"])
async def test_group_lock_contenders_do_not_starve_owner(database, capacity, operation):
    # Default SQLAlchemy capacity is 5 pooled + 10 overflow. Shorten only the
    # pool timeout to make the former resource cycle fail promptly in this test.
    engine = create_async_engine(
        os.environ["HARNESS_TEST_DATABASE_URL"],
        pool_size=5 if capacity == 15 else capacity,
        max_overflow=10 if capacity == 15 else 0, pool_timeout=0.7,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_at_read, contenders_at_lock = asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        service, group, _, parent, session, events = await setup_pg((engine, factory))
        sessions = InMemorySessionRepository()
        child_session = session.model_copy(update={"session_id": "expert-session"})
        await sessions.add(child_session)
        runs = RunService(sessions, service._runs, InMemoryTaskQueue(), events,
                          clock=lambda: NOW, id_generator=lambda _: uuid4().hex)

        async def real_create(**kwargs):
            return await runs.create_with_result(
                "t", child_session.session_id, kwargs["request"].run_id,
                input={"group_context": kwargs["group_context"],
                       "prompt": kwargs["request"].messages[0].content},
                deduplicate_active_input=False)

        service._agui.create_run_with_result.side_effect = real_create
        stored = None
        if operation == "retry":
            payload = await service.delegate_assistant_group(
                parent, session, specialist_id="specialist_pm")
            stored = await service.repository.get_group_message(
                "t", "u", group.group_id, payload["messageId"])
            child = await service._runs.get("t", stored.run_id)
            assert await service._runs.compare_and_set(child.status, child.model_copy(
                update={"status": RunStatus.SUCCEEDED, "fencing_token": child.fencing_token + 1}))

        # Pause the lock owner immediately before it needs a second connection.
        collaborator = events if operation == "retry" else service._runs
        method = "list_after" if operation == "retry" else "get"
        original = getattr(collaborator, method)
        first = True

        async def gated_read(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                owner_at_read.set()
                # Old code lets every contender enter PG's lock queue. New
                # dispatch contenders wait before checking out a connection.
                # Allow that outside-pool wait while still forcing the old
                # row-lock saturation deterministically whenever it exists.
                try:
                    await asyncio.wait_for(contenders_at_lock.wait(), 0.3)
                except TimeoutError:
                    pass
            return await original(*args, **kwargs)

        setattr(collaborator, method, gated_read)
        attempted = set()
        owner_task = None

        def before_execute(conn, cursor, statement, params, context, many):
            if "FOR UPDATE" in statement and "assistant_groups" in statement:
                task = asyncio.current_task()
                if task is not owner_task:
                    attempted.add(task)
                    if len(attempted) == capacity - 1:
                        contenders_at_lock.set()

        event.listen(engine.sync_engine, "before_cursor_execute", before_execute)

        async def dispatch(worker):
            if operation == "retry":
                return await worker._retry_empty_group_reply("t", "u", group, stored)
            return await worker.delegate_assistant_group(
                parent, session, specialist_id="specialist_pm")

        started = monotonic()
        owner_task = asyncio.create_task(dispatch(service))
        tasks.append(owner_task)
        await asyncio.wait_for(owner_at_read.wait(), 5)
        for index in range(capacity - 1):
            repository = PostgresAssistantRepository(factory)
            if operation == "roster":
                # Both roster edits and dispatches used to consume connections
                # while blocked on the same row. Exercise the other entry point.
                work = repository.update_group("t", "u", group.group_id,
                                               fields={"name": f"renamed-{index}"})
            else:
                work = dispatch(service.with_repository(repository))
            tasks.append(asyncio.create_task(work))
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 12)
        assert not [r for r in results if isinstance(r, BaseException)], results
        if operation == "dispatch":
            assert all(result == results[0] for result in results)
        elif operation == "retry":
            # A pool timeout is swallowed by the conservative event-read path;
            # the admitted owner must actually perform the retry, not defer it.
            assert results[0] is not None, "lock owner silently abandoned retry"
            assert sum(result is not None for result in results) == 1
        rows = await service.repository.list_group_messages("t", "u", group.group_id)
        assert len([r for r in rows if r.author is AssistantGroupMessageAuthor.SPECIALIST]) == 1
        assert len(await events.list_after("t", parent.run_id, 0)) == 1
        assert service._agui.create_run_with_result.await_count == (2 if stored else 1)
        assert len(await service._runs.list_for_sessions(
            "t", [child_session.session_id], limit=200)) == (2 if stored else 1)
        print(f"capacity={capacity} operation={operation} completed={len(results)} "
              f"elapsed={monotonic() - started:.3f}s, one reply/delivery")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", [2, 15])
async def test_different_groups_and_factories_share_pool_admission(database, capacity):
    engine = create_async_engine(
        os.environ["HARNESS_TEST_DATABASE_URL"],
        pool_size=5 if capacity == 15 else capacity,
        max_overflow=10 if capacity == 15 else 0, pool_timeout=0.7,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tasks = []
    try:
        service, group, _, parent, _, _ = await setup_pg((engine, factory))
        groups = [group]
        for _ in range(capacity - 1):
            another = group.model_copy(update={"group_id": uuid4().hex})
            await service.repository.create_group("t", "u", another, another.members)
            groups.append(another)
        owner_at_read, all_at_lock = asyncio.Event(), asyncio.Event()
        arrivals = set()

        def before_execute(conn, cursor, statement, params, context, many):
            if "FOR UPDATE" in statement and "assistant_groups" in statement:
                arrivals.add(asyncio.current_task())
                if len(arrivals) == capacity:
                    all_at_lock.set()

        event.listen(engine.sync_engine, "before_cursor_execute", before_execute)

        async def work(index, current):
            # execution_options creates an engine facade with the *same* pool.
            alias = engine.execution_options()
            repository = PostgresAssistantRepository(
                async_sessionmaker(alias, expire_on_commit=False))
            async with repository.group_dispatch_guard("t", "u", current.group_id):
                if index == 0:
                    owner_at_read.set()
                    try:
                        await asyncio.wait_for(all_at_lock.wait(), 0.3)
                    except TimeoutError:
                        pass
                assert (await service._runs.get("t", parent.run_id)).run_id == parent.run_id

        started = monotonic()
        tasks.append(asyncio.create_task(work(0, groups[0])))
        await asyncio.wait_for(owner_at_read.wait(), 5)
        tasks.extend(asyncio.create_task(work(index, current))
                     for index, current in enumerate(groups[1:], 1))
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 12)
        assert not [r for r in results if isinstance(r, BaseException)], results
        print(f"different groups capacity={capacity}: {len(results)} admissions "
              f"completed in {monotonic() - started:.3f}s")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()
