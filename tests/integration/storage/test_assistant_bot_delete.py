"""Bot deletion removes a contact, never the owner's conversations or Vault."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from harness.assistant.models import ObjectiveStatus, Specialist, SpecialistStatus
from harness.assistant.repository import PostgresAssistantRepository
from harness.assistant.storage import SpecialistRow
from harness.core.errors import ConflictError, NotFoundError
from tests.integration.storage.test_assistant_objectives import objective
from tests.integration.storage.test_delegation_real_gateway import make_service

NOW = datetime.now(UTC)


@pytest.mark.asyncio
async def test_delete_is_owned_revision_checked_and_retains_history(database):
    repo = PostgresAssistantRepository(database[1])
    bot = await repo.create_specialist(
        Specialist(
            specialistId="delete-fixture",
            tenantId="tenant-a",
            userId="user-a",
            slug="delete-fixture",
            name="删除验收 Bot",
            agentName="audit-agent",
            agentVersion="1.0.0",
            config={"credential_ref:合成资料": "vault:fixture"},
            createdAt=NOW,
            updatedAt=NOW,
        )
    )
    args = {"expected_revision": bot.revision, "confirm_name": bot.name, "now": NOW}
    with pytest.raises(NotFoundError):
        await repo.delete_specialist("tenant-a", "other", bot.specialist_id, **args)
    with pytest.raises(ConflictError, match="先归档"):
        await repo.delete_specialist("tenant-a", "user-a", bot.specialist_id, **args)
    archived = await repo.update_specialist(
        "tenant-a",
        "user-a",
        bot.specialist_id,
        expected_revision=bot.revision,
        status=SpecialistStatus.ARCHIVED,
        now=NOW,
    )
    await repo.create(objective().model_copy(update={"specialist_id": bot.specialist_id}))
    with pytest.raises(ConflictError):
        await repo.delete_specialist("tenant-a", "user-a", bot.specialist_id, **args)
    await repo.delete_specialist(
        "tenant-a",
        "user-a",
        bot.specialist_id,
        expected_revision=archived.revision,
        confirm_name=bot.name,
        now=NOW,
    )
    assert await repo.list_specialists("tenant-a", "user-a") == []
    with pytest.raises(NotFoundError):
        await repo.get_specialist("tenant-a", "user-a", bot.specialist_id)
    with pytest.raises(NotFoundError):
        await repo.update_specialist(
            "tenant-a",
            "user-a",
            bot.specialist_id,
            expected_revision=archived.revision + 1,
            status=SpecialistStatus.PAUSED,
            now=NOW,
        )
    assert (await repo.get("tenant-a", "user-a", "objective-a")).status is ObjectiveStatus.PAUSED
    async with database[1]() as db:
        row = await db.scalar(
            select(SpecialistRow).where(SpecialistRow.specialist_id == bot.specialist_id)
        )
        assert row and row.payload["deletedAt"] and not row.payload["config"]
    replacement = await repo.create_specialist(
        bot.model_copy(update={"specialist_id": "replacement"})
    )
    assert replacement.specialist_id == "replacement"
    assert len(await repo.list_specialists("tenant-a", "user-a")) == 1


@pytest.mark.asyncio
async def test_recreating_main_contact_uses_a_new_identity_without_reviving_deleted_grants(
    database,
):
    service = make_service(database)
    service._agui = SimpleNamespace(
        get_binding=AsyncMock(
            return_value=SimpleNamespace(
                agent_name="audit-agent",
                agent_version="1.0.0",
            )
        )
    )
    first = await service.primary_profile("tenant-a", "user-a", initialize=True)
    assert first
    archived = await service.repository.update_specialist(
        "tenant-a",
        "user-a",
        first.specialist_id,
        expected_revision=first.revision,
        status=SpecialistStatus.ARCHIVED,
        now=NOW,
    )
    await service.repository.delete_specialist(
        "tenant-a",
        "user-a",
        first.specialist_id,
        expected_revision=archived.revision,
        confirm_name=first.name,
        now=NOW,
    )
    assert await service.primary_profile("tenant-a", "user-a") is None
    replacement = await service.primary_profile("tenant-a", "user-a", initialize=True)
    assert replacement and replacement.specialist_id != first.specialist_id
    assert (
        await service.primary_profile("tenant-a", "user-a")
    ).specialist_id == replacement.specialist_id
