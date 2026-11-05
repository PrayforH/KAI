"""The main Bot's editable contact never migrates its conversation or authority."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD, primary_specialist_id
from harness.core.errors import ConflictError, NotFoundError
from tests.integration.storage.test_delegation_real_gateway import make_service


@pytest.mark.asyncio
async def test_primary_profile_is_idempotent_owned_and_uses_the_existing_conversation(database):
    service = make_service(database)
    service._agui = SimpleNamespace(get_binding=AsyncMock(return_value=SimpleNamespace(
        agent_name="audit-agent", agent_version="1.0.0"
    )))
    assert await service.primary_profile("tenant-a", "user-a") is None
    first, retried = await asyncio.gather(*[
        service.primary_profile("tenant-a", "user-a", initialize=True) for _ in range(2)
    ])
    assert first and retried and first.specialist_id == retried.specialist_id
    assert first.specialist_id == primary_specialist_id("tenant-a", "user-a")
    assert await service.specialist_thread("tenant-a", "user-a", first) == PERSONAL_ASSISTANT_THREAD
    assert await service.repository.list("tenant-a", "user-a") == []
    assert len(await service.repository.list_specialists("tenant-a", "user-a")) == 1
    for tenant, user in (("tenant-a", "other"), ("other", "user-a")):
        assert await service.primary_profile(tenant, user) is None
        with pytest.raises(NotFoundError):
            await service.repository.get_specialist(tenant, user, first.specialist_id)

    edited = await service.update_specialist_profile(
        "tenant-a", "user-a", first.specialist_id, expected_revision=first.revision,
        name="我的总管", title="我的角色", description="负责统筹",
        appearance={"avatar": "leaf", "color": "#0d9381"},
        sidebar={"pinned": False, "hidden": True, "unread": True, "group": "工作"},
    )
    assert edited.name == "我的总管" and edited.config["sidebar"]["hidden"] is True
    assert edited.config["conversation_thread_id"] == PERSONAL_ASSISTANT_THREAD
    thread = await service.specialist_thread("tenant-a", "user-a", edited)
    assert thread == PERSONAL_ASSISTANT_THREAD
    assert edited.agent_name == first.agent_name and edited.agent_version == first.agent_version
    with pytest.raises(ConflictError):
        await service.update_specialist_profile(
            "tenant-a", "user-a", first.specialist_id, expected_revision=first.revision,
            name="stale", title=None, description=None,
        )
    # A read or retried setup does not overwrite the user's role or organization.
    reread = await service.primary_profile("tenant-a", "user-a", initialize=True)
    assert reread and reread.config == edited.config and reread.name == "我的总管"
