"""Group admin tools exercise real PostgreSQL ownership, roster and delete locks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from tests.integration.storage.test_delegation_real_gateway import make_service
from tests.unit.assistant.test_group_tools import admin_identity, admin_overlay


@pytest.mark.asyncio
async def test_admin_create_update_dissolve_persist_and_http_owner_checks_remain(database):
    service = make_service(database)
    service._agui = SimpleNamespace(
        get_thread_record_for_session=AsyncMock(
            return_value=SimpleNamespace(thread_id=PERSONAL_ASSISTANT_THREAD),
        )
    )
    run, session = admin_identity(direct=True)
    created = await service.admin_create_assistant_group(run, session, name="实库群Ａ")
    assert created.creator_user_id == session.user_id
    stored = await service.repository.get_group("t", "u", created.group_id)
    assert [m.member_id for m in stored.members] == ["primary"]
    assert [g.group_id for g in await service.list_assistant_groups("t", "u")] == [
        created.group_id,
    ]
    assert await service.list_assistant_groups("t", "other-user") == []
    assert await service.list_assistant_groups("other-tenant", "u") == []
    for action in (
        service.update_assistant_group(
            "t", "other-user", created.group_id, changes={"name": "越权"}
        ),
        service.delete_assistant_group("t", "other-user", created.group_id),
    ):
        with pytest.raises(PermissionDeniedError):
            await action
    run, session = admin_identity()
    run = run.model_copy(
        update={
            "input": {
                "group_context": {
                    **run.input["group_context"],
                    "groupId": created.group_id,
                }
            }
        }
    )
    updated, old_name = await service.admin_update_assistant_group(
        run,
        session,
        changes={"name": "实库群Ｂ", "description": "保留：标点"},
    )
    assert old_name == "实库群Ａ"
    assert updated.name == "实库群Ｂ"
    assert (
        await service.get_assistant_group("t", "u", created.group_id)
    ).description == "保留：标点"
    with pytest.raises(ConflictError, match="群名不一致"):
        await service.admin_dissolve_assistant_group(run, session, confirm_name="实库群Ａ")
    with pytest.raises(ConflictError, match="群名不一致"):
        await service.repository.delete_group("t", "u", created.group_id, expected_name="实库群Ａ")
    assert (await service.get_assistant_group("t", "u", created.group_id)).name == "实库群Ｂ"
    ai_run = run.model_copy(
        update={
            "input": {
                "group_context": {
                    **run.input["group_context"],
                    "triggerAuthor": "specialist",
                }
            }
        }
    )
    with pytest.raises(PermissionDeniedError, match="解散群需要你本人提出"):
        await service.admin_dissolve_assistant_group(ai_run, session, confirm_name="实库群B")
    await service.admin_dissolve_assistant_group(run, session, confirm_name="实库群B")
    with pytest.raises(NotFoundError):
        await service.get_assistant_group("t", "u", created.group_id)
    assert await service.list_assistant_groups("t", "u") == []


@pytest.mark.asyncio
async def test_duplicate_names_remain_allowed_and_tool_audits_real_writes(database):
    service = make_service(database)
    service._agui = SimpleNamespace(
        get_thread_record_for_session=AsyncMock(
            return_value=SimpleNamespace(thread_id=PERSONAL_ASSISTANT_THREAD),
        )
    )
    tools, events = await admin_overlay(service, *admin_identity(direct=True))
    for _ in range(2):
        result = await tools["assistant_group_create"]({"name": "同名群"})
        assert "isError" not in result
    groups = await service.list_assistant_groups("t", "u")
    assert len({g.group_id for g in groups}) == 2
    assert {g.name for g in groups} == {"同名群"}
    assert len(events.appended) == 2
    assert {e["payload"]["groupId"] for e in events.appended} == {g.group_id for g in groups}
