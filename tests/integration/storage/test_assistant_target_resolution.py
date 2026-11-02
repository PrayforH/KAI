"""Contact identity and original-thread lookup share navigation's durable address."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from harness.adapters.memory import InMemoryAguiThreadBindingRepository
from harness.api.dependencies import Identity
from harness.assistant.api import get_specialist
from harness.assistant.models import SpecialistStatus
from harness.core.models import AguiThreadBinding
from harness.storage.platform_repositories import PostgresAguiThreadBindingRepository
from tests.integration.storage.test_delegation_real_gateway import (
    brief,
    make_service,
    overlay_for,
    text_of,
)


async def context(database, *, memory=False):
    service = make_service(database)
    records = (InMemoryAguiThreadBindingRepository() if memory
               else PostgresAguiThreadBindingRepository(database[1]))

    async def record(*, tenant_id, user_id, thread_id):
        return await records.get_by_thread(tenant_id, user_id, thread_id)

    async def binding(**coordinates):
        item = await record(**coordinates)
        return SimpleNamespace(session_id=item.session_id, project_id=item.project_id,
                               agent_name="audit-agent", agent_version="1.0.0")

    async def listing(*, tenant_id, user_id, **kwargs):
        return await records.list_for_user(tenant_id, user_id, **kwargs)

    service._agui = cast(Any, SimpleNamespace(
        get_thread_record=record, get_binding=binding, list_bindings=listing,
    ))
    return service, records


async def thread(records, identifier, *, title="旧标题", user="user-a", archived=False, days=0):
    at = datetime.now(UTC) - timedelta(days=days)
    await records.add(AguiThreadBinding(
        tenant_id="tenant-a", user_id=user, thread_id=identifier, session_id=f"s-{identifier}",
        title=title, archived_at=at if archived else None, created_at=at, updated_at=at,
    ))


async def bot(service, name="费用监控", user="user-a"):
    result, _ = await service.create_specialist(
        "tenant-a", user, name=name, description="按需",
        agent_name="audit-agent", agent_version="1.0.0",
    )
    return result


@pytest.mark.asyncio
async def test_contact_name_and_url_resolve_the_existing_thread_despite_unrelated_title(database):
    service, records = await context(database)
    contact = await bot(service)
    original = f"assistant_{contact.specialist_id}"
    await thread(records, original, title="模型生成的旧标题", days=90)
    for n in range(55):
        await thread(records, f"new-{n}", title="近期任务")
    for query in ["「费用监控」", contact.specialist_id, original,
                  f"https://example.test/assistant?bot={contact.specialist_id}",
                  f"/?thread={original}&run=old-run"]:
        candidates = await service.resolve_targets("tenant-a", "user-a", query)
        assert len(candidates) == 1
        assert candidates[0]["thread_id"] == original
        assert candidates[0]["title"] == "费用监控"
        assert not candidates[0].get("unavailable_reason")
    assert not await service.repository.list("tenant-a", "user-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("memory", [False, True])
async def test_task_search_filters_before_limit_and_treats_wildcards_literally(database, memory):
    service, records = await context(database, memory=memory)
    await thread(records, "original", title="费用_100% 回归", days=90)
    for n in range(55):
        await thread(records, f"new-{n}", title="其他近期任务")
    await thread(records, "foreign", title="费用_100% 回归", user="other")
    await thread(records, "archived", title="费用_100% 回归", archived=True)
    for query in ["费用_100%", "费用", "_100%"]:
        result = await service.resolve_targets("tenant-a", "user-a", query)
        assert [item["thread_id"] for item in result] == ["original"]
    assert not await service.resolve_targets("tenant-a", "user-a", "nonexistent%")
    assert not await service.resolve_targets("tenant-a", "user-a", "/?thread=foreign")
    assert not await service.resolve_targets("tenant-a", "user-a", "/?thread=archived")


@pytest.mark.asyncio
async def test_legacy_contact_uses_the_same_original_duty_thread_as_navigation(database):
    service, records = await context(database)
    contact = await bot(service, "旧编码助理")
    contact = await service.repository.update_specialist(
        "tenant-a", "user-a", contact.specialist_id, expected_revision=contact.revision,
        config={"conversation_thread_id": None}, now=datetime.now(UTC),
    )
    await thread(records, "original-task")
    duty = await service.create(
        "tenant-a", "user-a", brief(schedule=None), specialist_id=contact.specialist_id,
        managed_thread_id="original-task",
    )
    view = await get_specialist(
        contact.specialist_id, Identity(tenant_id="tenant-a", user_id="user-a"), service
    )
    candidates = await service.resolve_targets("tenant-a", "user-a", "旧编码助理")
    assert view["threadId"] == candidates[0]["thread_id"] == "original-task"
    assert not await service.repository.executions("tenant-a", "user-a", duty.objective_id)
    after = await service.repository.get_specialist("tenant-a", "user-a", contact.specialist_id)
    assert after == contact


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unstarted", "archived-contact", "archived-thread"])
async def test_unavailable_contacts_are_explicit_and_never_bound_to_phantom_work(database, state):
    service, records = await context(database)
    contact = await bot(service)
    original = f"assistant_{contact.specialist_id}"
    if state != "unstarted":
        await thread(records, original, archived=state == "archived-thread")
    if state == "archived-contact":
        await service.repository.update_specialist(
            "tenant-a", "user-a", contact.specialist_id, expected_revision=contact.revision,
            status=SpecialistStatus.ARCHIVED, now=datetime.now(UTC),
        )
    candidates = await service.resolve_targets("tenant-a", "user-a", "费用监控")
    assert len(candidates) == 1 and candidates[0]["unavailable_reason"]
    tools = {item.name: item.handler for item in overlay_for(service).tools}
    result = await tools["assistant_manage_work"]({"thread_id": original, "mode": "follow_only"})
    assert "不会创建替身" in text_of(result)
    assert not await service.repository.list("tenant-a", "user-a")


@pytest.mark.asyncio
async def test_contact_name_does_not_silently_choose_over_same_named_regular_task(database):
    service, records = await context(database)
    contact = await bot(service)
    await thread(records, f"assistant_{contact.specialist_id}")
    await thread(records, "different-task", title="费用监控")
    tools = {item.name: item.handler for item in overlay_for(service).tools}
    result = await tools["assistant_resolve_target"]({"query": "费用监控"})
    assert "多个匹配" in text_of(result)
    assert "子助理原对话" in text_of(result)
    assert "different-task" in text_of(result)
    assert not await service.repository.list("tenant-a", "user-a")


@pytest.mark.asyncio
async def test_foreign_contact_and_conflicting_links_never_resolve_or_create_work(database):
    service, records = await context(database)
    other = await bot(service, user="other")
    await thread(records, f"assistant_{other.specialist_id}", user="other")
    assert not await service.resolve_targets("tenant-a", "user-a", "费用监控")
    assert not await service.resolve_targets(
        "tenant-a", "user-a", f"/assistant?bot={other.specialist_id}"
    )
    for link in ["/?thread=a&thread=b", "/?thread=a&bot=b", "https://u:p@example.test/?thread=a"]:
        with pytest.raises(ValueError):
            await service.resolve_targets("tenant-a", "user-a", link)
    assert not await service.repository.list("tenant-a", "user-a")
