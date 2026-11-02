from datetime import timedelta

import pytest

from harness.assistant.chat_notices import notice_message_id
from harness.assistant.repository import PostgresAssistantRepository
from harness.assistant.storage import AssistantInboxRow
from tests.integration.api.test_agui_chat_notices import notice
from tests.integration.storage.conftest import DatabaseFixture
from tests.integration.storage.test_assistant_objectives import NOW, objective


@pytest.mark.asyncio
async def test_chat_receipts_page_beyond_inbox_limit_with_ties_and_read_items(
    database: DatabaseFixture,
):
    repo = PostgresAssistantRepository(database[1])
    await repo.create(objective())
    async with database[1]() as session:
        async with session.begin():
            for index in range(113):
                item = notice(
                    f"result-{index:03}",
                    objectiveId="objective-a",
                    createdAt=NOW + timedelta(seconds=index // 3),
                )
                session.add(
                    AssistantInboxRow(
                        item_id=item.item_id,
                        objective_id=item.objective_id,
                        tenant_id="tenant-a",
                        user_id="user-a",
                        dedup_key=item.item_id,
                        created_at=item.created_at,
                        read_at=NOW,
                        payload=item.model_dump(mode="json", by_alias=True),
                    )
                )
    seen = []
    cursor = None
    while True:
        page, count = await repo.conversation_notices("tenant-a", "user-a", before=cursor, limit=10)
        assert count == 113
        if not page:
            break
        assert all(n.read_at is not None for n in page)
        seen.extend(notice_message_id(n) for n in page)
        last = page[-1]
        cursor = (last.created_at, notice_message_id(last))
    assert len(seen) == len(set(seen)) == 113
    assert len(await repo.inbox("tenant-a", "user-a")) == 100
    for tenant, user in (("tenant-a", "other"), ("other", "user-a")):
        assert await repo.conversation_notices(tenant, user) == ([], 0)
        assert await repo.conversation_notice(tenant, user, "result-000") is None


@pytest.mark.asyncio
async def test_notice_sources_resolve_owned_bot_name_and_avatar_instead_of_internal_ids(
    database: DatabaseFixture,
):
    from harness.assistant.models import Specialist, SpecialistStatus

    repo = PostgresAssistantRepository(database[1])
    bot = await repo.create_specialist(
        Specialist(
            specialistId="specialist_source",
            tenantId="tenant-a",
            userId="user-a",
            slug="source",
            name="编码协同",
            agentName="audit-agent",
            agentVersion="1.0.0",
            status=SpecialistStatus.READY,
            createdAt=NOW,
            updatedAt=NOW,
            config={"appearance": {"color": "#f68b20", "avatar": "blob"}},
        )
    )
    await repo.create(objective().model_copy(update={"specialist_id": bot.specialist_id}))
    item = notice(threadId="assistant_specialist_source")
    source = (await repo.conversation_notice_sources("tenant-a", "user-a", [item]))[item.item_id]
    assert source["name"] == "编码协同"
    assert source["color"] == "#f68b20" and source["avatar"] == "blob"
    assert source["href"].startswith("/assistant?bot=specialist_source")
    for tenant, user in (("tenant-a", "other"), ("other", "user-a")):
        other = await repo.conversation_notice_sources(tenant, user, [item])
        assert other[item.item_id]["name"] != "编码协同"
        assert "avatar" not in other[item.item_id]
